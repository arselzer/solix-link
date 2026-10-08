"""Independent references never silently qualify counters as physical energy."""

from copy import deepcopy
import json

import pytest

from solix_link.energy_calibration import append_observation, calibration_report, new_session
from solix_link.energy_store import NativeEnergyStore
from solix_link import cli
from solix_link import energy_calibration_cli as calibration_cli
from test_native_energy_meter import report as gen2_report
from test_original_counter_diagnostics import report as original_report

NOW=1900000000


def snapshot(model, at, raw, store=None):
    version={"c1000":"1.7.1","c1000_gen2":"1.1.4.9","c2000_gen2":"2.1.6.4"}[model]
    value=dict(model=model,protocol="native_mqtt",connected=True,available=True,last_seen_timestamp=at,
               metrics=dict(software_version=version,usage_mode="standard"),private_account="PRIVATE")
    if model=="c1000":
        energy=original_report(now=at)
        energy["reports"][0]["counters"]["counter_1_raw"]=raw
        value["original_counters"]=energy
    else:
        value["native_energy"]=(store if store is not None else NativeEnergyStore(model)).ingest(
            [gen2_report(at,raw)],reported_at=at,firmware_version=version)
    return value


def paired(model="c1000_gen2"):
    counter="counter_1_raw" if model=="c1000" else "standard.ac_input_energy_raw"
    session=new_session(model,counter,"ac_input",now=NOW)
    store=NativeEnergyStore(model) if model!="c1000" else None
    for at,raw,kwh in ((NOW,100,1.0),(NOW+600,120,1.02)):
        session=append_observation(session,snapshot(model,at,raw,store),meter_kwh=kwh,meter_timestamp=at,now=at)
    return session


@pytest.mark.parametrize("model",["c1000","c1000_gen2","c2000_gen2"])
def test_all_three_models_produce_only_candidate_scales(model):
    session=paired(model)
    result=calibration_report(session,candidate_wh_per_unit=1)
    assert result["usable_ratio_intervals"]==1
    assert result["candidate_scale"]["median_wh_per_raw_unit"]==pytest.approx(1)
    assert all(result[key] is False for key in ("physical_units_verified","channel_mapping_verified",
        "mcu_snapshot_timing_verified","restart_retention_verified","battery_energy_estimate","changes_runtime_conversions"))
    assert "PRIVATE" not in json.dumps(session)+json.dumps(result)


@pytest.mark.parametrize("kind,reason",[("reset","counter_decreased_reset_wrap_or_reorder"),
    ("epoch","counter_epoch_changed"),("firmware","firmware_changed"),("mode","observed_mode_changed"),
    ("reference_reset","reference_meter_decreased"),("reference_clock","reference_clock_nonincreasing"),
    ("timing","endpoint_timing_mismatch"),("event_missing","event_timestamp_missing"),
    ("event_clock","event_clock_nonincreasing"),("restart","explicit_device_restart")])
def test_no_delta_crosses_an_ambiguous_boundary(kind,reason):
    session=paired();end=session["observations"][1]
    if kind=="reset":end["raw"]=1
    if kind=="epoch":end["counter_epoch"]+=1
    if kind=="firmware":end["firmware_version"]="1.1.4.10"
    if kind=="mode":end["mode"]="time_of_use"
    if kind=="reference_reset":end["reference_kwh"]=.5
    if kind=="reference_clock":end["reference_timestamp"]=NOW
    if kind=="timing":end["reference_timestamp"]+=31
    if kind=="event_missing":end["event_timestamp"]=None
    if kind=="event_clock":end["event_timestamp"]=NOW
    if kind=="restart":end["boundary"]="device_restart"
    interval=calibration_report(session)["intervals"][0]
    assert reason in interval["boundary_reasons"] and interval["raw_delta"] is None
    assert not interval["comparison_available"]


@pytest.mark.parametrize("kind",["stale","batch","wrong_model","duplicate","missing_counter"])
def test_capture_requires_a_new_matching_independent_upload(kind):
    session=paired()
    s=snapshot("c1000_gen2",NOW+1200,140)
    now=NOW+1200
    if kind=="stale":now+=30;s["last_seen_timestamp"]=now
    if kind=="batch":s["native_energy"]["batch_reports"]=2;s["native_energy"]["received_reports"]=2
    if kind=="wrong_model":s["model"]="c2000_gen2"
    if kind=="duplicate":s["native_energy"]["reported_at"]=NOW+600
    if kind=="missing_counter":s["native_energy"]["groups"]["standard"]["raw"].pop("ac_input_energy_raw")
    with pytest.raises(ValueError):append_observation(session,s,meter_kwh=1.04,meter_timestamp=now,now=now)


def test_cli_creates_private_records_without_touching_sources(monkeypatch,tmp_path,capsys):
    path=tmp_path/"private/session.json"
    monkeypatch.setattr(calibration_cli.time,"time",lambda:NOW)
    assert cli.main(["energy-calibration","create","--session",str(path),"--model","c1000",
        "--counter","counter_1_raw","--reference","ac_output"])==0
    assert path.stat().st_mode&0o777==0o600
    assert path.parent.stat().st_mode&0o777==0o700
    original=path.read_bytes()
    assert cli.main(["energy-calibration","create","--session",str(path),"--model","c1000",
        "--counter","counter_1_raw","--reference","ac_output"])==1
    assert path.read_bytes()==original
    source=tmp_path/"snapshot.json";source.write_text(json.dumps(snapshot("c1000",NOW,100)))
    assert cli.main(["energy-calibration","record","--session",str(path),"--snapshot-file",str(source),
        "--meter-kwh","1","--meter-timestamp",str(NOW)])==0
    assert "PRIVATE" not in path.read_text()
    assert cli.main(["energy-calibration","report","--session",str(path)])==0
    assert '"physical_units_verified": false' in capsys.readouterr().out


def test_unsafe_paths_and_unknown_metadata_are_rejected(tmp_path):
    directory=tmp_path/"private";directory.mkdir(mode=0o700)
    victim=directory/"victim";victim.write_text("keep");victim.chmod(0o600)
    path=directory/"session.json";path.symlink_to(victim)
    with pytest.raises(ValueError):
        with calibration_cli.session_lock(path):pass
    assert victim.read_text()=="keep"
    session=paired();session["account_id"]="PRIVATE"
    with pytest.raises(ValueError):calibration_report(session)
