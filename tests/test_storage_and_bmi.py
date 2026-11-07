"""Regression tests for database compatibility, deduplication and BMI math."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
import sqlite3
import pytest

from smart_scale_bmi import database, who


def profile(name="Test Person", profile_id=1):
    return {
        "weight_sensor": "sensor.weight",
        "profile_sensor": "sensor.profile",
        "profile_id": profile_id,
        "person_name": name,
        "gender": "female",
        "birth_month": 5,
        "birth_year": 1988,
        "height_m": 1.65,
        "initial_weight_kg": 0,
    }


def insert(db_path, entry_id, conf, measured_at, weight=65, **kwargs):
    return database.insert_measurement(
        str(db_path), entry_id=entry_id, config=conf,
        measured_at=measured_at, weight_kg=weight,
        bmi=round(weight / conf["height_m"] ** 2, 1),
        warning="bình thường", standard="WHO", age_months=450,
        age_text="37 tuổi", **kwargs,
    )


def test_schema_upsert_does_not_delete_history(tmp_path):
    path = tmp_path / "smart_scale_bmi" / "scale.db"
    database.init_db(str(path))
    conf = profile()
    database.upsert_person(str(path), "entry1", conf)
    first = insert(path, "entry1", conf, "2026-06-01T07:00:00+00:00")
    conf2 = {**conf, "person_name": "Renamed Person", "height_m": 1.66}
    database.upsert_person(str(path), "entry1", conf2)
    assert database.get_measurement(str(path), "entry1", first)["name"] == "Test Person"
    assert database.get_person(str(path), "entry1")["name"] == "Renamed Person"
    assert database.count_measurements(str(path), "entry1") == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_measurements_dedupe_and_person_isolation(tmp_path):
    path = tmp_path / "db.sqlite"
    database.init_db(str(path))
    conf = profile()
    database.upsert_person(str(path), "entry1", conf)
    conf2 = profile("Another", 2)
    database.upsert_person(str(path), "entry2", conf2)
    metadata = dict(
        source_weight_last_changed="2026-11-07T01:00:00.123456+00:00",
        source_profile_last_changed="2026-11-07T01:00:00.200000+00:00",
    )
    first = insert(path, "entry1", conf, "2026-11-07T01:00:02+00:00", **metadata)
    assert first > 0
    assert insert(path, "entry1", conf, "2026-11-07T01:00:04+00:00", **metadata) is None
    assert insert(path, "entry2", conf2, "2026-11-07T01:00:04+00:00", **metadata) is not None
    assert database.count_measurements(str(path), "entry1") == 1
    assert database.count_measurements(str(path), "entry2") == 1
    assert database.delete_measurement(str(path), "entry2", first) is False


def test_update_delete_snapshot_and_config(tmp_path):
    path = tmp_path / "db.sqlite"
    conf = profile()
    database.init_db(str(path))
    database.upsert_person(str(path), "entry1", conf)
    entry = SimpleNamespace(data=conf, options={"person_name": "New Name"})
    assert database.get_config(entry)["person_name"] == "New Name"
    ids = [insert(path, "entry1", conf, f"2026-11-{day:02d}T10:00:00+00:00", weight=60+day) for day in range(1, 12)]
    latest, recent = database.get_sensor_snapshot(str(path), "entry1", 5)
    assert latest["id"] == ids[-1]
    assert len(recent) == 5
    assert set(recent[0]) == {"id", "entry_id", "measured_at", "weight_kg", "bmi", "warning"}
    assert database.update_measurement(
        str(path), entry_id="entry1", measurement_id=ids[-1],
        config=conf, measured_at=latest["measured_at"], weight_kg=70,
        bmi=25.7, warning="thừa cân", standard="WHO", age_months=450,
        age_text="37 tuổi",
    )
    changed = database.get_measurement(str(path), "entry1", ids[-1])
    assert changed["weight_kg"] == 70 and changed["bmi"] == 25.7
    assert changed["measured_at"] == latest["measured_at"]
    assert database.delete_measurement(str(path), "entry1", ids[-1])
    assert not database.delete_measurement(str(path), "entry1", ids[-1])


def test_who_calculation_and_age():
    assert who.calculate_bmi(70, 1.75) == 22.9
    assert who.classify_adult_bmi(24.9)["warning"] == "bình thường"
    assert who.classify_adult_bmi(25.0)["warning"] == "thừa cân"
    assert who.classify_adult_bmi(30.0)["warning"] == "béo phì độ I"
    assert who.age_months_from_birth(2015, 11, date(2026, 11, 7)) == 132
    result = who.classify_bmi(18.5, "male", 2015, 11, date(2026, 11, 7))
    assert result["age_months"] == 132
    assert result["standard"].startswith("WHO")
    with pytest.raises(ValueError):
        who.calculate_bmi(70, 0)


def test_mixed_utc_and_local_history_sorts_by_instant(tmp_path):
    """Pre-upgrade local timestamps must not displace a newer UTC reading."""
    path = tmp_path / "mixed.sqlite"
    conf = profile()
    database.init_db(str(path))
    database.upsert_person(str(path), "entry1", conf)
    old = insert(path, "entry1", conf, "2026-11-07T12:30:00+07:00", weight=70)
    newest = insert(path, "entry1", conf, "2026-11-07T06:00:00+00:00", weight=71)
    latest, history = database.get_sensor_snapshot(str(path), "entry1", 2)
    assert latest["id"] == newest
    assert [x["id"] for x in history] == [newest, old]
    assert database.get_latest_measurement(str(path), "entry1")["id"] == newest


def test_upgrade_preserves_existing_sqlite_data(tmp_path):
    """Simulate the older schema before the UTC ordering and dedupe indexes."""
    path = tmp_path / "legacy.sqlite"
    # Preserve the pre-upgrade schema exactly, rather than using a new DB.
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE people (
              entry_id TEXT PRIMARY KEY, weight_sensor TEXT NOT NULL,
              profile_sensor TEXT NOT NULL, profile_id INTEGER NOT NULL,
              name TEXT NOT NULL, gender TEXT NOT NULL, birth_month INTEGER NOT NULL,
              birth_year INTEGER NOT NULL, height_m REAL NOT NULL,
              initial_weight_kg REAL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE measurements (
              id INTEGER PRIMARY KEY AUTOINCREMENT, entry_id TEXT NOT NULL,
              profile_id INTEGER NOT NULL, name TEXT NOT NULL, measured_at TEXT NOT NULL,
              weight_kg REAL NOT NULL, height_m REAL NOT NULL, bmi REAL NOT NULL,
              warning TEXT NOT NULL, standard TEXT NOT NULL, age_months INTEGER,
              age_text TEXT, gender TEXT, birth_month INTEGER, birth_year INTEGER,
              source_weight_sensor TEXT, source_profile_sensor TEXT,
              source_weight_last_changed TEXT, source_profile_last_changed TEXT,
              FOREIGN KEY(entry_id) REFERENCES people(entry_id)
            );
            CREATE INDEX idx_measurements_entry_time
              ON measurements(entry_id, measured_at DESC);
        """)
    conf = profile()
    database.init_db(str(path))
    database.upsert_person(str(path), "entry1", conf)
    legacy_id = insert(path, "entry1", conf, "2025-01-02T07:00:00+07:00")
    database.init_db(str(path))
    database.upsert_person(str(path), "entry1", {**conf, "person_name": "Updated"})
    assert database.get_measurement(str(path), "entry1", legacy_id)["weight_kg"] == 65
    assert database.count_measurements(str(path), "entry1") == 1
