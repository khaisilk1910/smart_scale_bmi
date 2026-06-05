"""SQLite storage helpers for Smart Scale BMI."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_BIRTH_MONTH,
    CONF_BIRTH_YEAR,
    CONF_GENDER,
    CONF_HEIGHT_M,
    CONF_INITIAL_WEIGHT_KG,
    CONF_PERSON_NAME,
    CONF_PROFILE_ID,
    CONF_PROFILE_SENSOR,
    CONF_WEIGHT_SENSOR,
)


def get_config(entry: ConfigEntry) -> dict[str, Any]:
    """Merge immutable entry data and editable options."""
    return {**entry.data, **entry.options}


def init_db(db_path: str) -> None:
    """Create or migrate the SQLite database."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS people (
                entry_id TEXT PRIMARY KEY,
                weight_sensor TEXT NOT NULL,
                profile_sensor TEXT NOT NULL,
                profile_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                gender TEXT NOT NULL,
                birth_month INTEGER NOT NULL,
                birth_year INTEGER NOT NULL,
                height_m REAL NOT NULL,
                initial_weight_kg REAL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS measurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id TEXT NOT NULL,
                profile_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                measured_at TEXT NOT NULL,
                weight_kg REAL NOT NULL,
                height_m REAL NOT NULL,
                bmi REAL NOT NULL,
                warning TEXT NOT NULL,
                standard TEXT NOT NULL,
                age_months INTEGER,
                age_text TEXT,
                gender TEXT,
                birth_month INTEGER,
                birth_year INTEGER,
                source_weight_sensor TEXT,
                source_profile_sensor TEXT,
                source_weight_last_changed TEXT,
                source_profile_last_changed TEXT,
                FOREIGN KEY(entry_id) REFERENCES people(entry_id)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_measurements_entry_time ON measurements(entry_id, measured_at DESC)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_measurements_profile ON measurements(profile_id, measured_at DESC)"
        )
        conn.commit()
    finally:
        conn.close()


def upsert_person(db_path: str, entry_id: str, config: dict[str, Any]) -> None:
    """Insert or update the configured person."""
    now = datetime.now().isoformat(timespec="seconds")
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT created_at FROM people WHERE entry_id=?", (entry_id,))
        row = cur.fetchone()
        created_at = row[0] if row else now
        cur.execute(
            """
            INSERT OR REPLACE INTO people (
                entry_id, weight_sensor, profile_sensor, profile_id,
                name, gender, birth_month, birth_year, height_m,
                initial_weight_kg, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                str(config[CONF_WEIGHT_SENSOR]),
                str(config[CONF_PROFILE_SENSOR]),
                int(config[CONF_PROFILE_ID]),
                str(config[CONF_PERSON_NAME]),
                str(config[CONF_GENDER]),
                int(config[CONF_BIRTH_MONTH]),
                int(config[CONF_BIRTH_YEAR]),
                float(config[CONF_HEIGHT_M]),
                float(config.get(CONF_INITIAL_WEIGHT_KG, 0) or 0),
                created_at,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def count_measurements(db_path: str, entry_id: str) -> int:
    """Count measurements for an entry."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM measurements WHERE entry_id=?", (entry_id,))
        return int(cur.fetchone()[0])
    finally:
        conn.close()


def insert_measurement(
    db_path: str,
    *,
    entry_id: str,
    config: dict[str, Any],
    measured_at: str,
    weight_kg: float,
    bmi: float,
    warning: str,
    standard: str,
    age_months: int | None,
    age_text: str | None,
    source_weight_last_changed: str | None = None,
    source_profile_last_changed: str | None = None,
) -> int:
    """Insert a new measurement and return its row id."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO measurements (
                entry_id, profile_id, name, measured_at, weight_kg, height_m,
                bmi, warning, standard, age_months, age_text, gender,
                birth_month, birth_year, source_weight_sensor, source_profile_sensor,
                source_weight_last_changed, source_profile_last_changed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                int(config[CONF_PROFILE_ID]),
                str(config[CONF_PERSON_NAME]),
                measured_at,
                float(weight_kg),
                float(config[CONF_HEIGHT_M]),
                float(bmi),
                warning,
                standard,
                age_months,
                age_text,
                str(config[CONF_GENDER]),
                int(config[CONF_BIRTH_MONTH]),
                int(config[CONF_BIRTH_YEAR]),
                str(config[CONF_WEIGHT_SENSOR]),
                str(config[CONF_PROFILE_SENSOR]),
                source_weight_last_changed,
                source_profile_last_changed,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def delete_measurement(db_path: str, entry_id: str, measurement_id: int) -> bool:
    """Delete a measurement for a specific entry."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM measurements WHERE entry_id=? AND id=?",
            (entry_id, int(measurement_id)),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_measurement(db_path: str, entry_id: str, measurement_id: int) -> dict[str, Any] | None:
    """Return one measurement for a specific entry."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM measurements WHERE entry_id=? AND id=?",
            (entry_id, int(measurement_id)),
        )
        return _row_to_dict(cur.fetchone())
    finally:
        conn.close()


def update_measurement(
    db_path: str,
    *,
    entry_id: str,
    measurement_id: int,
    config: dict[str, Any],
    measured_at: str,
    weight_kg: float,
    bmi: float,
    warning: str,
    standard: str,
    age_months: int | None,
    age_text: str | None,
) -> bool:
    """Update a measurement and return whether a row was changed."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE measurements
            SET profile_id=?, name=?, measured_at=?, weight_kg=?, height_m=?,
                bmi=?, warning=?, standard=?, age_months=?, age_text=?, gender=?,
                birth_month=?, birth_year=?, source_weight_sensor=?, source_profile_sensor=?
            WHERE entry_id=? AND id=?
            """,
            (
                int(config[CONF_PROFILE_ID]),
                str(config[CONF_PERSON_NAME]),
                measured_at,
                float(weight_kg),
                float(config[CONF_HEIGHT_M]),
                float(bmi),
                warning,
                standard,
                age_months,
                age_text,
                str(config[CONF_GENDER]),
                int(config[CONF_BIRTH_MONTH]),
                int(config[CONF_BIRTH_YEAR]),
                str(config[CONF_WEIGHT_SENSOR]),
                str(config[CONF_PROFILE_SENSOR]),
                entry_id,
                int(measurement_id),
            ),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def get_latest_measurement(db_path: str, entry_id: str) -> dict[str, Any] | None:
    """Return the latest measurement."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM measurements
            WHERE entry_id=?
            ORDER BY measured_at DESC, id DESC
            LIMIT 1
            """,
            (entry_id,),
        )
        return _row_to_dict(cur.fetchone())
    finally:
        conn.close()


def get_recent_measurements(db_path: str, entry_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Return recent measurements in reverse chronological order."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM measurements
            WHERE entry_id=?
            ORDER BY measured_at DESC, id DESC
            LIMIT ?
            """,
            (entry_id, int(limit)),
        )
        return [_row_to_dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_person(db_path: str, entry_id: str) -> dict[str, Any] | None:
    """Return person configuration stored in the database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM people WHERE entry_id=?", (entry_id,))
        return _row_to_dict(cur.fetchone())
    finally:
        conn.close()
