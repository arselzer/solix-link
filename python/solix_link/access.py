"""Explicit per-station HTTP scopes, loaded from owner-only local files."""

from __future__ import annotations

from dataclasses import dataclass, field
import hmac
import json
import os
from pathlib import Path
import stat

from .commands import COMMAND_FIELDS
from .gateway_client import GatewayClient


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


@dataclass(frozen=True)
class Principal:
    read: frozenset[str]
    write: tuple[tuple[str, frozenset[str]], ...] = ()

    def commands(self, name: str) -> frozenset[str]:
        return dict(self.write).get(name, frozenset())


@dataclass(frozen=True, repr=False)
class AccessPolicy:
    _entries: tuple[tuple[bytes, Principal], ...] = field(repr=False)

    def authenticate(self, header: str) -> Principal | None:
        if not isinstance(header, str) or len(header) > 1100:
            return None
        candidate = header.encode("utf-8")
        result = None
        for expected, principal in self._entries:
            if hmac.compare_digest(candidate, expected):
                result = principal
        return result

    @classmethod
    def load(cls, path: Path, devices) -> AccessPolicy:
        """No wildcards: adding a station never silently expands a token's access."""
        descriptor = None
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_nlink != 1 or not 1 <= info.st_size <= 65536):
                raise ValueError
            raw = os.read(descriptor, 65537)
            if len(raw) > 65536:
                raise ValueError
            body = json.loads(raw, object_pairs_hook=unique_object)
            if (type(body) is not dict or set(body) != {"schema_version", "tokens"}
                    or type(body["schema_version"]) is not int or body["schema_version"] != 1
                    or type(body["tokens"]) is not list or not 1 <= len(body["tokens"]) <= 32):
                raise ValueError
            known = set(devices)
            entries, secrets = [], set()
            for item in body["tokens"]:
                if type(item) is not dict or set(item) != {"token_file", "read", "write"}:
                    raise ValueError
                reads, writes = item["read"], item["write"]
                if (type(reads) is not list or not 1 <= len(reads) <= 32
                        or any(type(name) is not str for name in reads)
                        or len(set(reads)) != len(reads) or not set(reads) <= known
                        or type(writes) is not dict or not set(writes) <= set(reads)):
                    raise ValueError
                scoped = []
                for name, commands in writes.items():
                    if (type(commands) is not list or not commands
                            or any(type(command) is not str for command in commands)
                            or len(set(commands)) != len(commands) or not set(commands) <= COMMAND_FIELDS.keys()):
                        raise ValueError
                    scoped.append((name, frozenset(commands)))
                token_path = item["token_file"]
                if type(token_path) is not str or not Path(token_path).is_absolute():
                    raise ValueError
                token = GatewayClient._read_token(Path(token_path))
                if not 16 <= len(token) <= 1024 or token in secrets:
                    raise ValueError
                secrets.add(token)
                entries.append((("Bearer " + token).encode("ascii"), Principal(frozenset(reads), tuple(scoped))))
            return cls(tuple(entries))
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
            raise ValueError("Invalid owner-only HTTP permissions or token file") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
