"""HA and SDK enforce the same bounded saved-plan contract."""


import pytest

from test_charging_blueprint import api
from solix_link.plan_readback import validate_plan_readback


def plan():
    return dict(schema_version=1, enabled=True, reported_at=1000, source="status_d9",
                periods=[dict(tariff="peak", start_hour=0, end_hour=24)])


@pytest.mark.parametrize("change", [{}, {"enabled": 1}, {"periods": []}, {"reported_at": 10**400},
    {"reported_at": float("nan")}, {"source": "PRIVATE"}, {"identity": "PRIVATE"},
    {"periods": [dict(tariff="peak", start_hour=0, end_hour=24)] * 2},
    {"periods": [dict(tariff="peak", start_hour=False, end_hour=24)]},
    {"periods": [dict(tariff="peak", start_hour=0, end_hour=24, account_id="PRIVATE")]},
])
def test_ha_sdk_contract_matches(change):
    value = {**plan(), **change}
    assert api.parse_tou_plan(value) == validate_plan_readback(value)


def test_snapshot_readback_copy_and_original_exclusion():
    snapshot = dict(name="test", model="c1000_gen2", protocol="native_mqtt", connected=True,
                    available=True, metrics={}, tou_plan_readback=plan())
    parsed = api.parse_snapshot(snapshot)
    parsed["tou_plan_readback"]["periods"].clear()
    assert snapshot["tou_plan_readback"]["periods"]
    assert api.parse_snapshot({**snapshot, "model": "c1000"})["tou_plan_readback"] is None
