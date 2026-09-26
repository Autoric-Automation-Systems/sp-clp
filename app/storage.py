from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class Storage:
    def __init__(self, path: str | Path = "data/sp-clp.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS areas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plant_name TEXT NOT NULL,
                    name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS machines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    area_id INTEGER NOT NULL REFERENCES areas(id),
                    name TEXT NOT NULL,
                    ip TEXT NOT NULL,
                    db_number INTEGER NOT NULL,
                    rack INTEGER NOT NULL DEFAULT 0,
                    slot INTEGER NOT NULL DEFAULT 1,
                    timezone TEXT NOT NULL DEFAULT 'UTC'
                );
                CREATE TABLE IF NOT EXISTS count_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    machine_id INTEGER NOT NULL REFERENCES machines(id),
                    sampled_at TEXT NOT NULL,
                    count INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS hourly_counts (
                    machine_id INTEGER NOT NULL REFERENCES machines(id),
                    hour_start TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    PRIMARY KEY (machine_id, hour_start)
                );
                """
            )

    def get_setting(self, key: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def add_area(self, plant_name: str, name: str) -> int:
        with self.connect() as connection:
            cursor = connection.execute("INSERT INTO areas(plant_name, name) VALUES (?, ?)", (plant_name, name))
            return int(cursor.lastrowid)

    def get_area(self, area_id: int) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute("SELECT * FROM areas WHERE id = ?", (area_id,)).fetchone()

    def list_areas(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(connection.execute("SELECT * FROM areas ORDER BY plant_name, name"))

    def add_machine(self, area_id: int, name: str, ip: str, db_number: int, timezone_name: str) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO machines(area_id, name, ip, db_number, timezone) VALUES (?, ?, ?, ?, ?)",
                (area_id, name, ip, db_number, timezone_name),
            )
            return int(cursor.lastrowid)

    def update_machine(self, machine_id: int, name: str, ip: str, db_number: int, timezone_name: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE machines SET name = ?, ip = ?, db_number = ?, timezone = ? WHERE id = ?",
                (name, ip, db_number, timezone_name, machine_id),
            )
            return cursor.rowcount == 1

    def delete_machine(self, machine_id: int) -> bool:
        with self.connect() as connection:
            machine_exists = connection.execute(
                "SELECT 1 FROM machines WHERE id = ?", (machine_id,)
            ).fetchone()
            if machine_exists is None:
                return False
            connection.execute("DELETE FROM count_samples WHERE machine_id = ?", (machine_id,))
            connection.execute("DELETE FROM hourly_counts WHERE machine_id = ?", (machine_id,))
            connection.execute("DELETE FROM machines WHERE id = ?", (machine_id,))
            return True

    def list_machines(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(connection.execute(
                "SELECT machines.*, areas.name AS area_name, areas.plant_name "
                "FROM machines JOIN areas ON areas.id = machines.area_id "
                "ORDER BY areas.plant_name, areas.name, machines.name"
            ))

    def get_machine(self, machine_id: int) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute("SELECT * FROM machines WHERE id = ?", (machine_id,)).fetchone()

    def save_sample(self, machine_id: int, count: int, sampled_at: datetime | None = None) -> int:
        sampled_at = sampled_at or datetime.now(timezone.utc)
        with self.connect() as connection:
            previous = connection.execute(
                "SELECT count FROM count_samples WHERE machine_id = ? ORDER BY id DESC LIMIT 1", (machine_id,)
            ).fetchone()
            delta = 0 if previous is None or count < previous["count"] else count - previous["count"]
            connection.execute(
                "INSERT INTO count_samples(machine_id, sampled_at, count) VALUES (?, ?, ?)",
                (machine_id, sampled_at.isoformat(), count),
            )
            hour = sampled_at.replace(minute=0, second=0, microsecond=0).isoformat()
            connection.execute(
                "INSERT INTO hourly_counts(machine_id, hour_start, quantity) VALUES (?, ?, ?) "
                "ON CONFLICT(machine_id, hour_start) DO UPDATE SET quantity=quantity+excluded.quantity",
                (machine_id, hour, delta),
            )
            return delta

    def hourly_counts(self, machine_id: int) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(connection.execute(
                "SELECT hour_start, quantity FROM hourly_counts WHERE machine_id = ? ORDER BY hour_start DESC",
                (machine_id,),
            ))
