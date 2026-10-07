from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Where the database sits inside the application folder.
DEFAULT_NAME = Path("data") / "sp-clp.sqlite3"


def application_folder() -> Path:
    """The folder the panel runs from.

    Frozen by PyInstaller that is the folder holding ``SP-CLP.exe``; from a
    checkout it is the project root. It is deliberately not the working
    directory: a shortcut without "Start in", a scheduled task or a service
    starts the panel in ``C:\\Windows\\System32``, and the customer's database
    would be created there, or fail for lack of permission.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def resolve_path(path: str | Path | None = None) -> Path:
    """Where the database lives: the argument, then ``SP_CLP_DB``, then the default.

    A relative path from either source is read from the application folder rather
    than from the working directory, so a panel pointed at another file finds the
    same file whatever started the process.
    """
    chosen = path if path is not None else os.environ.get("SP_CLP_DB")
    if chosen is None or str(chosen) == "":
        return application_folder() / DEFAULT_NAME
    resolved = Path(chosen).expanduser()
    return resolved if resolved.is_absolute() else application_folder() / resolved


class Storage:
    def __init__(self, path: str | Path | None = None) -> None:
        # An explicit path wins, then SP_CLP_DB, then the installed default. The
        # variable lets an operator point the app at another file and keeps the
        # test suite away from the development database.
        self.path = resolve_path(path)
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
                CREATE TABLE IF NOT EXISTS signal_labels (
                    machine_id INTEGER NOT NULL REFERENCES machines(id),
                    address TEXT NOT NULL,
                    label TEXT NOT NULL,
                    PRIMARY KEY (machine_id, address)
                );
                CREATE TABLE IF NOT EXISTS signal_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    machine_id INTEGER NOT NULL REFERENCES machines(id),
                    address TEXT NOT NULL,
                    value INTEGER NOT NULL,
                    changed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS signal_events_window
                    ON signal_events(machine_id, address, changed_at);
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

    def plant_names(self) -> list[str]:
        with self.connect() as connection:
            return [
                row["plant_name"]
                for row in connection.execute("SELECT DISTINCT plant_name FROM areas")
            ]

    def update_area(self, area_id: int, plant_name: str, name: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE areas SET plant_name = ?, name = ? WHERE id = ?",
                (plant_name, name, area_id),
            )
            return cursor.rowcount == 1

    def delete_area(self, area_id: int) -> bool:
        """Remove one area. Callers must check that no machine is left in it."""
        with self.connect() as connection:
            exists = connection.execute("SELECT 1 FROM areas WHERE id = ?", (area_id,)).fetchone()
            if exists is None:
                return False
            connection.execute("DELETE FROM areas WHERE id = ?", (area_id,))
            return True

    def rename_plant(self, current: str, plant_name: str) -> int:
        """Rename every area of one plant; a plant exists only through its areas."""
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE areas SET plant_name = ? WHERE plant_name = ?", (plant_name, current)
            )
            return cursor.rowcount

    def machines_in_area(self, area_id: int) -> int:
        with self.connect() as connection:
            return int(connection.execute(
                "SELECT COUNT(*) FROM machines WHERE area_id = ?", (area_id,)
            ).fetchone()[0])

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
            connection.execute("DELETE FROM signal_labels WHERE machine_id = ?", (machine_id,))
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

    def hourly_counts(
        self, machine_id: int, start: str | None = None, end: str | None = None
    ) -> list[sqlite3.Row]:
        """Hourly rows for one machine, optionally inside a UTC range."""
        query = "SELECT hour_start, quantity FROM hourly_counts WHERE machine_id = ?"
        params: list[object] = [machine_id]
        if start is not None:
            query += " AND hour_start >= ?"
            params.append(start)
        if end is not None:
            query += " AND hour_start < ?"
            params.append(end)
        query += " ORDER BY hour_start"
        with self.connect() as connection:
            return list(connection.execute(query, params))

    def hourly_range(self, machine_id: int) -> tuple[str, str] | None:
        """First and last stored hour marker, or None when there is no history."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT MIN(hour_start) AS first, MAX(hour_start) AS last "
                "FROM hourly_counts WHERE machine_id = ?",
                (machine_id,),
            ).fetchone()
        if row is None or row["first"] is None:
            return None
        return row["first"], row["last"]

    def save_transitions(
        self, machine_id: int, bits: dict[str, bool], changed_at: datetime | None = None
    ) -> int:
        """One row per signal that moved, which is what the day chart is drawn from.

        Storing the change instead of every reading keeps the table small: a bit
        that stays where it is writes nothing, so a machine that runs all day costs
        one row, not seventeen thousand.
        """
        moment = changed_at or datetime.now(timezone.utc)
        written = 0
        with self.connect() as connection:
            previous = {
                row["address"]: bool(row["value"])
                for row in connection.execute(
                    "SELECT e.address, e.value FROM signal_events e JOIN ("
                    "  SELECT address, MAX(id) AS last_id FROM signal_events"
                    "  WHERE machine_id = ? GROUP BY address) last ON e.id = last.last_id",
                    (machine_id,),
                )
            }
            for address, value in bits.items():
                if address in previous and previous[address] is value:
                    continue
                connection.execute(
                    "INSERT INTO signal_events(machine_id, address, value, changed_at)"
                    " VALUES (?, ?, ?, ?)",
                    (machine_id, address, 1 if value else 0, moment.isoformat()),
                )
                written += 1
        return written

    def signal_events(
        self, machine_id: int, addresses: list[str], start: datetime, end: datetime
    ) -> dict[str, list[tuple[datetime, bool]]]:
        """Events per address inside the window, each led by the last one before it.

        The event before the window is what tells the chart which state the signal
        was already in at midnight; without it the day would start blank until the
        first change.
        """
        found: dict[str, list[tuple[datetime, bool]]] = {address: [] for address in addresses}
        with self.connect() as connection:
            for address in addresses:
                before = connection.execute(
                    "SELECT value, changed_at FROM signal_events WHERE machine_id = ?"
                    " AND address = ? AND changed_at < ?"
                    " ORDER BY changed_at DESC, id DESC LIMIT 1",
                    (machine_id, address, start.isoformat()),
                ).fetchone()
                if before is not None:
                    found[address].append(
                        (datetime.fromisoformat(before["changed_at"]), bool(before["value"]))
                    )
            for row in connection.execute(
                "SELECT address, value, changed_at FROM signal_events WHERE machine_id = ?"
                " AND changed_at >= ? AND changed_at < ? ORDER BY changed_at, id",
                (machine_id, start.isoformat(), end.isoformat()),
            ):
                found[row["address"]].append(
                    (datetime.fromisoformat(row["changed_at"]), bool(row["value"]))
                )
        return found

    def sample_times(self, machine_id: int, start: datetime, end: datetime) -> list[datetime]:
        """When the recorder last read this machine, one step before the window too.

        The reading is the heartbeat: the day chart trusts a state only between two
        readings close enough to each other, so this is what separates a machine
        that stopped from a panel that was not looking.
        """
        with self.connect() as connection:
            before = connection.execute(
                "SELECT sampled_at FROM count_samples WHERE machine_id = ? AND sampled_at < ?"
                " ORDER BY sampled_at DESC LIMIT 1",
                (machine_id, start.isoformat()),
            ).fetchone()
            inside = connection.execute(
                "SELECT sampled_at FROM count_samples WHERE machine_id = ?"
                " AND sampled_at >= ? AND sampled_at < ? ORDER BY sampled_at",
                (machine_id, start.isoformat(), end.isoformat()),
            )
            times = ([datetime.fromisoformat(before["sampled_at"])] if before else [])
            times.extend(datetime.fromisoformat(row["sampled_at"]) for row in inside)
        return times

    def prune(self, before: datetime) -> int:
        """Drop history older than the window the panel keeps, and say how much."""
        removed = 0
        with self.connect() as connection:
            for table, column in (
                ("count_samples", "sampled_at"),
                ("hourly_counts", "hour_start"),
                ("signal_events", "changed_at"),
            ):
                cursor = connection.execute(
                    f"DELETE FROM {table} WHERE {column} < ?", (before.isoformat(),)
                )
                removed += cursor.rowcount or 0
        return removed

    def signal_labels(self, machine_id: int) -> dict[str, str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT address, label FROM signal_labels WHERE machine_id = ?", (machine_id,)
            ).fetchall()
        return {row["address"]: row["label"] for row in rows}

    def set_signal_labels(self, machine_id: int, labels: dict[str, str]) -> None:
        """Store the given labels. An empty label removes the override and restores the default."""
        with self.connect() as connection:
            for address, label in labels.items():
                if label:
                    connection.execute(
                        "INSERT INTO signal_labels(machine_id, address, label) VALUES (?, ?, ?) "
                        "ON CONFLICT(machine_id, address) DO UPDATE SET label = excluded.label",
                        (machine_id, address, label),
                    )
                else:
                    connection.execute(
                        "DELETE FROM signal_labels WHERE machine_id = ? AND address = ?",
                        (machine_id, address),
                    )
