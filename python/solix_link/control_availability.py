"""Pure cached control explanations; backend raw-state checks stay decisive."""

from .commands import COMMAND_FIELDS, commands_for_transport
from .protocol import Model
from .settings_export import export_settings, valid_preference


CONTROL_METRICS = {
    "set-charge-power": ("ac_charging_power_limit_w",),
    "set-charge-cap": ("max_charge_percentage", "backup_reserve_percentage"),
    "set-backup-reserve": ("min_charge_percentage", "max_charge_percentage", "backup_reserve_percentage"),
    "set-discharge-floor": ("min_charge_percentage", "max_charge_percentage", "backup_reserve_percentage"),
    "set-display-timeout": ("display_timeout_seconds",),
    "set-display-brightness": ("display_brightness",),
    "set-clock-brightness": ("clock_screen_first_brightness_flag_raw", "clock_screen_second_brightness_flag_raw"),
    "set-device-timeout": ("device_timeout_minutes",),
    "set-port-memory": ("port_memory_enabled",),
    "set-fast-charge": ("ac_fast_charge_enabled",),
    "set-temperature-unit": ("temperature_unit_fahrenheit",),
    "set-off-grid-alert": ("ac_off_grid_alert_enabled",),
    "set-light": ("light_mode",),
    "set-ac-power-saving": ("ac_power_saving_mode_enabled",),
    "set-dc-power-saving": ("dc_power_saving_mode_enabled",),
    "set-tou-plan": ("min_charge_percentage", "max_charge_percentage", "backup_reserve_percentage"),
    "return-grid": ("min_charge_percentage", "max_charge_percentage", "backup_reserve_percentage"),
}

REASONS = {
    "model_unsupported": "This model has no established gateway control for this setting.",
    "transport_unsupported": "This setting is not exposed over the configured transport.",
    "gateway_controls_disabled": "The HTTP gateway was started without controls enabled.",
    "token_scope_denied": "This token cannot send this command to this station.",
    "worker_controls_disabled": "The monitoring worker reports controls disabled.",
    "not_advertised": "The monitoring service does not advertise this command.",
    "telemetry_unavailable": "Connected, fresh telemetry is required.",
    "missing_or_invalid_metrics": "Required cached setting values are missing or invalid.",
    "firmware_unqualified": "This control requires model-specific firmware qualification.",
    "output_must_be_off": "Smart configuration requires its output already off.",
    "countdown_must_be_inactive": "Output countdowns must be reported inactive.",
    "standard_mode_required": "Standard mode with no active tariff is required.",
    "clock_must_be_inactive": "The clock screen and asset transfer must be inactive.",
    "command_in_progress": "Another HTTP command is running for this station.",
}


def parse_control_availability(value: object) -> dict:
    """Sanitize a bounded cached report for GET-only CLI/SDK presentation."""
    if (type(value) is not dict or type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or value.get("model") not in tuple(model.value for model in Model)
            or value.get("protocol") not in ("legacy", "prime", "native_mqtt")
            or value.get("preflight_only") is not True or value.get("backend_validation_required") is not True
            or type(value.get("snapshot_fresh")) is not bool
            or type(value.get("commands")) is not list or len(value["commands"]) > len(COMMAND_FIELDS)):
        raise ValueError("Invalid cached control availability")
    rows, seen = [], set()
    fields = set().union(*CONTROL_METRICS.values())
    for row in value["commands"]:
        if (type(row) is not dict or type(row.get("command")) is not str or row["command"] not in COMMAND_FIELDS
                or row["command"] in seen or any(type(row.get(key)) is not bool for key in ("advertised", "permitted", "ready"))
                or type(row.get("reasons")) is not list or len(row["reasons"]) > len(REASONS)
                or any(type(reason) is not str or reason not in REASONS for reason in row["reasons"])
                or type(row.get("missing_metrics")) is not list or len(row["missing_metrics"]) > len(fields)
                or any(type(key) is not str or key not in fields for key in row["missing_metrics"])):
            raise ValueError("Invalid cached control availability")
        seen.add(row["command"])
        rows.append({"command": row["command"], "advertised": row["advertised"], "permitted": row["permitted"],
            "ready": row["ready"] and row["advertised"] and row["permitted"] and not row["reasons"] and not row["missing_metrics"],
            "reasons": list(row["reasons"]), "missing_metrics": list(row["missing_metrics"])})
    return {"schema_version": 1, "model": value["model"], "protocol": value["protocol"],
        "source": "cached_telemetry", "preflight_only": True, "backend_validation_required": True,
        "snapshot_fresh": value["snapshot_fresh"], "commands": rows}


def control_availability(snapshot: dict, advertised, permitted, *, gateway_enabled: bool,
                         now: float | None = None, busy: bool = False) -> dict:
    exported = export_settings(snapshot, now=now)
    metrics = snapshot.get("metrics", {})
    try:
        model = Model(exported["model"])
        supported = set(commands_for_transport(model, exported["protocol"]))
        model_supported = set().union(*(commands_for_transport(model, protocol)
                                      for protocol in ("legacy", "prime", "native_mqtt")))
    except ValueError:
        supported, model_supported = set(), set()
    advertised, permitted = set(advertised), set(permitted)
    rows = []
    for command in COMMAND_FIELDS:
        reasons, missing = [], []
        if command not in model_supported:
            reasons.append("model_unsupported")
        elif command not in supported:
            reasons.append("transport_unsupported")
        if not gateway_enabled:
            reasons.append("gateway_controls_disabled")
        if command not in permitted:
            reasons.append("token_scope_denied")
        if command not in advertised and command in supported:
            reasons.append("worker_controls_disabled" if snapshot.get("control_enabled") is False else "not_advertised")
        if not exported["snapshot_fresh"]:
            reasons.append("telemetry_unavailable")
        if busy:
            reasons.append("command_in_progress")
        if command in supported:
            missing = [key for key in CONTROL_METRICS[command]
                       if not valid_preference(key, metrics.get(key), exported["model"])]
            if missing:
                reasons.append("missing_or_invalid_metrics")
            if snapshot.get("model") == "c1000_gen2" and snapshot.get("protocol") == "native_mqtt":
                if command in ("set-ac-power-saving", "set-dc-power-saving", "set-clock-brightness"):
                    if metrics.get("software_version") != "1.1.4.9":
                        reasons.append("firmware_unqualified")
                    if any(type(metrics.get(key)) is not int or metrics[key] != 0
                           for key in ("ac_output_timeout_seconds", "dc_output_timeout_seconds")):
                        reasons.append("countdown_must_be_inactive")
                if command in ("set-display-brightness", "set-clock-brightness"):
                    if metrics.get("usage_mode") != "standard" or metrics.get("active_tariff") != "none":
                        reasons.append("standard_mode_required")
                    if any(type(metrics.get(key)) is not int or metrics[key] != 0
                           for key in ("clock_screen_enabled", "clock_screen_transfer_status_raw")):
                        reasons.append("clock_must_be_inactive")
            if command in ("set-ac-power-saving", "set-dc-power-saving"):
                key = "ac_output_enabled" if command.startswith("set-ac-") else "dc_output_enabled"
                if snapshot.get("protocol") != "legacy" and (type(metrics.get(key)) is not int or metrics[key] != 0):
                    reasons.append("output_must_be_off")
        rows.append({"command": command, "advertised": command in advertised,
            "permitted": gateway_enabled and command in advertised and command in permitted,
            "ready": not reasons, "reasons": reasons, "missing_metrics": missing})
    return {"schema_version": 1, "model": exported["model"], "protocol": exported["protocol"],
        "firmware_version": exported["firmware_version"], "source": "cached_telemetry",
        "preflight_only": True, "backend_validation_required": True,
        "snapshot_fresh": exported["snapshot_fresh"], "commands": rows}
