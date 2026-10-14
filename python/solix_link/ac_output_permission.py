"""Per-station, owner-managed AC permission and durable ON restoration intent."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

from .protocol import Model

POWER_COMMANDS = frozenset(("set-ac-output", "cycle-ac-output"))


class ACOutputPermission:
    """Missing files are locked; malformed files never grant permission."""

    def __init__(self, config, directory: Path):
        self.supported = config.model == Model.C2000_GEN2
        self.directory = directory
        self.identity = hashlib.sha256(json.dumps([config.product, config.device_serial,
            config.account_id], separators=(",", ":")).encode()).hexdigest()
        self.path = directory / "ac-output-permission.json"
        self.restore_path = directory / "ac-cycle-restore.json"

    def _read(self, path: Path) -> dict | None:
        fd = None
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_nlink != 1 or not 1 <= info.st_size <= 4096):
                raise ValueError("Invalid AC permission storage")
            from .access import unique_object
            body = json.loads(os.read(fd, 4097), object_pairs_hook=unique_object)
            if (type(body) is not dict or body.get("identity") != self.identity
                    or type(body.get("schema_version")) is not int or body["schema_version"] != 1):
                raise ValueError("Invalid AC permission storage")
            return body
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError, UnicodeError):
            raise ValueError("Invalid AC permission storage") from None
        finally:
            if fd is not None:
                os.close(fd)

    def _write(self, path: Path, values: dict) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".ac-output-", dir=self.directory)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump({"schema_version": 1, "identity": self.identity, **values}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def enabled(self) -> bool:
        body = self._read(self.path)
        if body is None:
            return False
        if set(body) != {"schema_version", "identity", "enabled"} or type(body["enabled"]) is not bool:
            raise ValueError("Invalid AC permission storage")
        return self.supported and body["enabled"]

    def require(self) -> None:
        if not self.enabled():
            raise PermissionError("Allow AC output changes must be explicitly enabled for this station")

    def configure(self, enabled: bool, *, acknowledge_high_impact: bool) -> None:
        if not self.supported or type(enabled) is not bool or type(acknowledge_high_impact) is not bool:
            raise ValueError("AC permission requires a supported station and explicit booleans")
        if enabled and not acknowledge_high_impact:
            raise PermissionError("Acknowledge that AC changes interrupt all attached AC loads")
        self.enabled()  # Preserve invalid evidence instead of overwriting it.
        self._write(self.path, {"enabled": enabled})

    def pending(self) -> bool:
        body = self._read(self.restore_path)
        if body is None:
            return False
        if set(body) != {"schema_version", "identity", "restore_on"} or body["restore_on"] is not True:
            raise ValueError("Invalid AC restoration intent")
        return self.supported

    def arm(self) -> None:
        if self.pending():
            raise RuntimeError("AC restoration is already pending")
        self._write(self.restore_path, {"restore_on": True})

    def restored(self) -> None:
        if self.pending():
            self.restore_path.unlink()
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)

    def snapshot(self) -> dict:
        try:
            enabled, pending = self.enabled(), self.pending()
            valid = True
        except ValueError:
            enabled, pending, valid = False, False, False
        return {"supported": self.supported, "enabled": enabled, "storage_valid": valid,
                "restore_pending": pending}


def public_ac_power_control(value: object) -> dict | None:
    """Never export journal contents or station fingerprints."""
    if type(value) is not dict or any(type(value.get(k)) is not bool for k in
            ("supported", "enabled", "storage_valid", "restore_pending")):
        return None
    return {k: value[k] for k in ("supported", "enabled", "storage_valid", "restore_pending")}
