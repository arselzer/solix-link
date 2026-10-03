"""Manual terminal preview inputs; never writes configuration or sends commands."""

from __future__ import annotations

from pathlib import Path
import re
import time

from .charging_policy import ChargingPolicyRequestError, MAX_PREVIEW_BYTES, preview_charging_policy
from .charging_preview_cli import read_document


def manual_preview(snapshot: dict, request_file: Path, *, price_value: str = "", price_age: str = "",
                   export_value: str = "", export_age: str = "", now: float | None = None) -> dict:
    """Keep file signals, or replace explicitly paired manual value/age inputs."""
    now = time.time() if now is None else now
    request = read_document(request_file, MAX_PREVIEW_BYTES)
    # Validate the complete original request even when overrides are supplied.
    preview_charging_policy(snapshot, request, now=now)
    request = {"config": request["config"].copy(),
               "signals": {key: value.copy() for key, value in request["signals"].items()}}
    for role, value_text, age_text, minimum, maximum in (
        ("price", price_value, price_age, -1000, 1000),
        ("export", export_value, export_age, -20000, 20000),
    ):
        if type(value_text) is not str or type(age_text) is not str or len(value_text) > 64 or len(age_text) > 5:
            raise ChargingPolicyRequestError()
        if not value_text and not age_text:
            continue
        if (role not in request["signals"] or not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", value_text)
                or not re.fullmatch(r"[0-9]{1,5}", age_text)):
            raise ChargingPolicyRequestError()
        value, age = float(value_text), int(age_text)
        if not minimum <= value <= maximum or not 0 <= age <= 86400:
            raise ChargingPolicyRequestError()
        request["signals"][role].update(value=value, timestamp=now - age)
    return preview_charging_policy(snapshot, request, now=now)
