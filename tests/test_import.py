from pathlib import Path

import pytest
import sqlite_utils

from activity_sette import sports
from activity_sette.fit import parse_fit
from activity_sette.gpx import parse_gpx
from activity_sette.importer import run_import
from activity_sette.metrics import summarize
from activity_sette.tcx import parse_filename, parse_tcx

from .conftest import minute_times

CONFIG = Path(__file__).resolve().parents[1] / "config" / "sports.toml"


def cfg():
    return sports.Config.load(CONFIG)


def test_tcx_filename_parsing():
    assert parse_filename("2013-02-06_08-28-08_Quick trip to Bainbridge for lunch_Other") == (
        "Quick trip to Bainbridge for lunch",
        "Other",
    )
    assert parse_filename("2014-11-19_Cycling (1)") == (None, "Cycling")
    assert parse_filename("2017-06-28_Paddleboarding @ Des Moines_Other") == ("Paddleboarding @ Des Moines", "Other")
    assert parse_filename("Track_2016-01-01 114558") == (None, None)


def test_tcx_notes_split_into_title_and_description(write_tcx):
    path = write_tcx("2017-11-26_Newcastle Windsurfing_Other.tcx", notes="Newcastle Windsurfing\nWent paddling after.")
    (rec,) = parse_tcx(path)
    assert rec.name == "Newcastle Windsurfing"
    assert rec.notes == "Went paddling after."
    assert rec.file_label == "Other"
    assert rec.device == "Garmin Forerunner 310XT"


def test_tcx_without_gps_or_hr(write_tcx):
    (rec,) = parse_tcx(write_tcx("2015-06-01_Gym.tcx", gps=False, hr=False))
    s = summarize(rec)
    assert not s["has_gps"] and not s["has_hr"]
    assert s["route"] is None and s["start_lat"] is None
    assert s["distance_m"] > 0 and s["distance_source"] == "laps"
    assert s["avg_hr"] is None and s["max_hr"] is None


def test_tcx_summary_with_hr(write_tcx):
    (rec,) = parse_tcx(write_tcx("2015-06-01_Running.tcx", sport="Running"))
    s = summarize(rec)
    assert s["has_gps"] and s["has_hr"]
    assert 120 <= s["avg_hr"] <= 129 and s["max_hr"] == 129
    assert s["elapsed_s"] == 290
    assert s["moving_s"] == 290
    assert s["ascent_m"] >= 25


def test_gpx_bom_hr_and_gap_split(write_gpx):
    times = minute_times("2016-01-01", 10, 0, 20) + minute_times("2016-01-02", 10, 0, 20)
    path = write_gpx("Track_2016-01-01 100000.gpx", times, bom=True, with_hr=True, trk_name="2016-01-01 10:00:00")
    recs = parse_gpx(path, split_gap_s=6 * 3600)
    assert len(recs) == 2
    assert [len(r.points) for r in recs] == [20, 20]
    assert recs[0].points[0].hr == 130
    # Timestamp-like names are not meaningful
    assert recs[0].name is None


def test_sport_priority():
    c = cfg()
    from activity_sette.models import Recording

    def rec(**kw):
        return Recording(source_file=kw.pop("source_file", "x.tcx"), source_format="tcx", **kw)

    # Specific rule on title beats generic file label
    assert sports.resolve([rec(name="Teaching Windsurfing", file_label="Other", device_sport="Other")], c, "a")["sport"] == "windsurfing"
    # Description text is ignored
    assert sports.resolve([rec(name="Seattle Running", notes="sore from orienteering", file_label="Running")], c, "a")["sport"] == "running"
    # File label beats device sport
    assert sports.resolve([rec(file_label="MtnBiking", device_sport="Other")], c, "a")["sport"] == "mountain_biking"
    # Weak title rule used when only generic labels exist
    r = sports.resolve([rec(name="Cle Elum Running", file_label="Other", device_sport="Other")], c, "a")
    assert (r["sport"], r["sport_source"]) == ("running", "weak_rule")
    # Device sport mapped through labels
    assert sports.resolve([rec(device_sport="Biking")], c, "a")["sport"] == "cycling"
    # Nothing specific -> other
    assert sports.resolve([rec(file_label="Other", device_sport="Other")], c, "a")["sport"] == "other"
    # Conflicting labels in duplicate exports are flagged
    r = sports.resolve([rec(file_label="Walking"), rec(file_label="Running")], c, "a")
    assert r["sport"] == "walking" and r["sport_conflict"]
    # Overrides win
    c.overrides["a"] = "running"
    assert sports.resolve([rec(file_label="Walking")], c, "a")["sport"] == "running"


def test_fit_parse_summary(write_fit):
    (rec,) = parse_fit(write_fit("2015-06-01_Running.fit", sport="running"))
    assert rec.source_format == "fit"
    assert rec.external_id == "2015-06-01T10:00:00Z"  # matches the TCX Id convention
    assert rec.device == "Garmin edge500"
    assert rec.device_sport == "running"
    assert len(rec.points) == 30 and len(rec.laps) == 1
    p0 = rec.points[0]
    assert p0.time.isoformat() == "2015-06-01T10:00:00+00:00"
    assert abs(p0.lat - 47.6) < 1e-6 and abs(p0.lon - -122.3) < 1e-6
    assert abs(p0.ele - 10) < 0.25  # altitude scale/offset applied
    assert rec.points[3].distance == pytest.approx(3 * 22.2, abs=0.02)
    assert rec.points[0].speed == pytest.approx(2.5)
    lap = rec.laps[0]
    assert lap.timer_s == pytest.approx(290) and lap.calories == 150
    assert lap.avg_hr == pytest.approx(124, abs=1) and lap.max_hr == 129

    s = summarize(rec)
    assert s["has_gps"] and s["has_hr"]
    assert s["elapsed_s"] == 290
    assert s["distance_m"] == pytest.approx(29 * 22.2)
    assert s["distance_source"] == "laps"
    assert 120 <= s["avg_hr"] <= 129


def test_fit_without_gps_or_hr(write_fit):
    (rec,) = parse_fit(write_fit("2015-06-01_Gym.fit", sport="generic", gps=False, hr=False))
    assert rec.device_sport == "other"  # FIT "generic" maps to the generic label
    s = summarize(rec)
    assert not s["has_gps"] and not s["has_hr"]
    assert s["distance_m"] > 0 and s["distance_source"] == "laps"


def test_fit_unrecognized_sport_enum(write_fit):
    """Sport ids added by devices newer than the fitparse profile decode as raw ints."""
    (rec,) = parse_fit(write_fit("2015-06-01_NewGadget.fit", sport="unrecognized"))
    assert rec.device_sport is None
    # ...and must not break sport resolution
    assert sports.resolve([rec], cfg(), "a")["sport"] == "other"


def test_fit_dedupes_with_tcx_export(tmp_path, write_tcx, write_fit):
    """A FIT file and the TCX export of the same activity merge into one."""
    src = tmp_path / "src"
    src.mkdir()
    a = write_tcx("2015-06-01_10-00-00_Running.tcx", sport="Running", hr=False)
    b = write_fit("2015-06-01_Running.fit", sport="running", n=31)
    for p in (a, b):
        p.rename(src / p.name)

    db_path = tmp_path / "out.db"
    result = run_import([src], db_path, cfg())
    assert len(result.activities) == 1

    db = sqlite_utils.Database(db_path)
    (act,) = db["activities"].rows
    assert act["source_file"] == "2015-06-01_Running.fit"  # richest: HR + native format
    assert act["recording_count"] == 2
    assert act["sport"] == "running"

    roles = {r["file"]: r["role"] for r in db["recordings"].rows}
    assert roles["2015-06-01_Running.fit"] == "primary"
    assert roles["2015-06-01_10-00-00_Running.tcx"] == "duplicate"


def test_import_dedupes_and_types(tmp_path, write_tcx, write_gpx):
    src = tmp_path / "src"
    src.mkdir()
    # Two exports of the same TCX activity (same Id), one GPX recording of the same outing,
    # and an unrelated GPX later that day.
    a = write_tcx("2015-06-01_10-00-00_Running.tcx", sport="Running", hr=False)
    b = write_tcx("2015-06-01_Running.tcx", sport="Running", n=31)
    g1 = write_gpx("dup.gpx", minute_times("2015-06-01", 10, 0, 10))
    g2 = write_gpx("Kayak Trip.gpx", minute_times("2015-06-01", 15, 0, 30))
    for p in (a, b, g1, g2):
        p.rename(src / p.name)

    db_path = tmp_path / "out.db"
    result = run_import([src], db_path, cfg())
    assert len(result.activities) == 2

    db = sqlite_utils.Database(db_path)
    acts = list(db["activities"].rows_where(order_by="start_utc"))
    assert acts[0]["source_file"] == "2015-06-01_Running.tcx"  # richest: has HR, more points
    assert acts[0]["recording_count"] == 3
    assert acts[0]["sport"] == "running"
    assert acts[1]["sport"] == "kayaking"
    assert acts[1]["avg_hr"] is None

    roles = {r["file"]: r["role"] for r in db["recordings"].rows}
    assert roles["2015-06-01_Running.tcx"] == "primary"
    assert roles["dup.gpx"] == "duplicate"

    cols = {c.name: c.type for c in db["activities"].columns}
    assert cols["max_hr"] == "INTEGER" and cols["avg_hr"] == "REAL"
    assert db["trackpoints"].count == 31 + 30
    assert db["laps"].count == 1


def test_reimport_replaces_contents(tmp_path, write_tcx):
    src = tmp_path / "src"
    src.mkdir()
    p = write_tcx("2015-06-01_Running.tcx", sport="Running")
    p.rename(src / p.name)
    db_path = tmp_path / "out.db"
    run_import([src], db_path, cfg())
    run_import([src], db_path, cfg())
    assert sqlite_utils.Database(db_path)["activities"].count == 1
