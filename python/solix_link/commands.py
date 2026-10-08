"""Allowlisted gateway command shapes; protocol clients validate value ranges."""

from .tou import TouPeriod, validate_periods
from .protocol import Model, validate_device_timeout
from .c1000_capabilities import original_native_commands, original_prime_commands

COMMAND_FIELDS = {
    "set-charge-power": {"watts": int},
    "set-charge-cap": {"upper": int},
    "set-discharge-floor": {"lower": int},
    "set-backup-reserve": {"reserve": int},
    "set-tou-plan": {"periods": list, "enabled": bool},
    "return-grid": {"timeout": int},
    "set-display-timeout": {"seconds": int},
    "set-display-brightness": {"level": int},
    "set-clock-brightness": {"window": int, "high": bool},
    "set-port-memory": {"enabled": bool},
    "set-fast-charge": {"enabled": bool},
    "set-light": {"mode": int},
    "set-temperature-unit": {"fahrenheit": bool},
    "set-off-grid-alert": {"enabled": bool},
    "set-device-timeout": {"minutes": int},
    "set-ac-power-saving": {"enabled": bool},
    "set-dc-power-saving": {"enabled": bool},
}
NATIVE_COMMANDS = ("set-charge-power", "set-charge-cap", "set-backup-reserve", "set-tou-plan", "return-grid")
NATIVE_C1000_COMMANDS = ("set-temperature-unit", "set-off-grid-alert", "set-discharge-floor", "set-device-timeout", "set-fast-charge",
                        "set-display-brightness", "set-display-timeout", "set-port-memory", "set-dc-power-saving", "set-ac-power-saving", "set-clock-brightness")


def native_commands_for_model(model: Model) -> tuple[str, ...]:
    """Return model-specific controls; backend guards validate fresh readback."""
    if model == Model.C1000:
        return original_native_commands()
    if model == Model.C1000_GEN2:
        return NATIVE_COMMANDS + NATIVE_C1000_COMMANDS
    if model == Model.C2000_GEN2:
        return NATIVE_COMMANDS + ("set-display-timeout",)
    return ()


def commands_for_transport(model: Model, protocol: str) -> tuple[str, ...]:
    """Shared gateway allowlist, independent of token/worker enablement."""
    if protocol == "native_mqtt":
        return native_commands_for_model(model)
    if model in (Model.C300, Model.C1000):
        if protocol == "prime":
            return tuple(original_prime_commands()) if model == Model.C1000 else ()
        if protocol == "legacy":
            return ("set-charge-power", "set-display-timeout", "set-light") + (
                ("set-device-timeout", "set-temperature-unit", "set-fast-charge", "set-ac-power-saving", "set-dc-power-saving")
                if model == Model.C1000 else ())
        return ()
    if protocol != "prime":
        return ()
    if model == Model.C2000_GEN2:
        return ("set-charge-power", "set-charge-cap", "set-display-timeout")
    if model == Model.C1000_GEN2:
        return ("set-charge-power", "set-display-timeout", "set-fast-charge", "set-device-timeout")
    return ()


def validate_command(command: str, values: dict) -> None:
    expected = COMMAND_FIELDS.get(command) if isinstance(command, str) else None
    if expected is None or set(values) != set(expected) or any(type(values[k]) is not kind for k, kind in expected.items()):
        raise ValueError("Unsupported command or invalid fields/types")
    if command == "set-tou-plan":
        periods = values["periods"]
        if len(periods) > 6:
            raise ValueError("Schedule has more than six periods")
        parsed = []
        for period in periods:
            if not isinstance(period, dict) or set(period) != {"tariff", "start_hour", "end_hour"}:
                raise ValueError("Invalid schedule period")
            parsed.append(TouPeriod(**period))
        validate_periods(parsed)
        if values["enabled"] and not parsed:
            raise ValueError("Enabled Time-of-Use requires a nonempty schedule")
    if command == "return-grid" and not 5 <= values["timeout"] <= 120:
        raise ValueError("Grid confirmation timeout must be 5–120 seconds")
    if command == "set-device-timeout":
        validate_device_timeout(values["minutes"])
    if command == "set-clock-brightness" and values["window"] not in (1, 2):
        raise ValueError("Clock window must be 1 or 2")
    if command == "set-display-brightness" and values["level"] not in (1, 2, 3):
        raise ValueError("Display brightness must be 1 (low), 2 (medium) or 3 (high)")
