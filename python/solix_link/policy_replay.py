"""Replay supplied adaptive-preview frames; no device, executor or physics model."""

from __future__ import annotations

from html import escape

from .adaptive_policy import preview_adaptive_policy
from .charging_policy import ChargingPolicyRequestError, _timestamp

MAX_REPLAY_BYTES = 1_048_576
MAX_FRAMES = 256


class PolicyReplayError(ValueError):
    def __init__(self) -> None:
        super().__init__("InvalidPolicyReplay")


def replay_adaptive_timeline(document: object) -> dict:
    """Carry previous-preview state across fresh, independently supplied frames.

    Proposals never update a snapshot. SOC, watts, mode and signal response must
    be supplied by the caller, not inferred from a proposed action. Cooldown
    advances on preview decisions, not an ACK or executed station command.
    """
    if (type(document) is not dict or set(document) != {"schema_version", "request", "frames"}
            or type(document["schema_version"]) is not int or document["schema_version"] != 1
            or type(document["request"]) is not dict
            or type(document["frames"]) is not list or not 1 <= len(document["frames"]) <= MAX_FRAMES):
        raise PolicyReplayError()
    previous_time = None
    for frame in document["frames"]:
        if (type(frame) is not dict
                or not {"timestamp", "snapshot", "signals"} <= set(frame)
                or set(frame) - {"timestamp", "snapshot", "signals", "manual_override"}
                or not _timestamp(frame["timestamp"])
                or previous_time is not None and frame["timestamp"] <= previous_time):
            raise PolicyReplayError()
        previous_time = frame["timestamp"]
    frames = []
    started = document["frames"][0]["timestamp"]
    try:
        # Validate the complete starting request; frame overrides cannot repair it.
        preview_adaptive_policy(document["frames"][0]["snapshot"], document["request"], now=started)
        request = {"config": document["request"]["config"].copy(),
                   "state": document["request"]["state"].copy()}
        for index, frame in enumerate(document["frames"]):
            current = {"config": request["config"].copy(), "state": request["state"].copy(),
                       "signals": frame["signals"]}
            if "manual_override" in frame:
                current["config"]["manual_override"] = frame["manual_override"]
            result = preview_adaptive_policy(frame["snapshot"], current, now=frame["timestamp"])
            frames.append({"frame_index": index, "elapsed_seconds": frame["timestamp"] - started,
                           "preview": result})
            if result["next_preview_state"] is not None:
                request["state"] = result["next_preview_state"].copy()
    except ChargingPolicyRequestError:
        raise PolicyReplayError() from None
    return {"schema_version": 1, "dry_run": True, "commands_sent": 0,
            "executor_available": False, "electrical_behavior_verified": False,
            "snapshots_modified": False, "state_basis": "previous_preview_only",
            "frames": frames}


def adaptive_timeline_svg(document: object) -> str:
    """Render sanitized decisions and proposed settings as a standalone SVG."""
    report = replay_adaptive_timeline(document)
    height = 145 + len(report["frames"]) * 68
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="{height}" viewBox="0 0 1000 {height}" role="img" aria-labelledby="title desc">',
             '<title id="title">Adaptive policy replay</title>',
             '<desc id="desc">Read-only proposals from supplied snapshots. Zero commands, no predicted electrical behavior.</desc>',
             '<rect width="100%" height="100%" fill="#0c1424"/>',
             '<g font-family="system-ui, sans-serif" fill="#e1eaf7">',
             '<text x="24" y="34" font-size="23">Adaptive policy replay · commands sent: 0</text>',
             '<text x="24" y="61" font-size="14">Supplied snapshots · previous-preview state · no battery/grid-flow prediction</text>',
             '<text x="24" y="94" font-size="14">Elapsed</text><text x="132" y="94" font-size="14">Decision</text>',
             '<text x="270" y="94" font-size="14">Proposals / blocking reasons</text>']
    colors = {"blocked": "#ef8d83", "grid": "#89addb", "battery": "#dfb65f",
              "charge": "#77dfc2", "idle": "#89addb", "emergency": "#dfb65f"}
    for row in report["frames"]:
        preview = row["preview"]
        decision = preview["decision"]
        proposals = []
        for item in preview["proposed_settings"]:
            label, unit = {"ac_charging_power_limit_w": ("Charging limit", " W"),
                           "backup_reserve_percentage": ("Reserve", "%")}[item["setting"]]
            proposals.append(f'{label} → {item["value"]}{unit}')
        if preview["proposed_plan"]:
            tariff = preview["proposed_plan"]["periods"][0]["tariff"]
            proposals.append(f"TOU {tariff}, 00:00–24:00")
        y = 125 + row["frame_index"] * 68
        parts += [f'<rect x="16" y="{y-19}" width="968" height="60" rx="7" fill="#13233a"/>',
                  f'<text x="24" y="{y}" font-size="14">{row["elapsed_seconds"]:g}s</text>',
                  f'<text x="132" y="{y}" font-size="14" fill="{colors[decision]}">{decision}</text>',
                  f'<text x="270" y="{y}" font-size="13">{escape("; ".join(proposals) or "No setting or plan proposal")}</text>',
                  f'<text x="270" y="{y+22}" font-size="11" fill="#a8b8ce">{escape(", ".join(preview["reasons"]))}</text>']
    return "\n".join(parts + ['</g></svg>']) + "\n"
