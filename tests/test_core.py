import getpass
import logging
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import uvicorn
import app as app_package
from app import main as main_module
from app.branding import remove_logos
from app.models import MachineInput
from app.plc import (
    BOOL_LAYOUT,    DINT_LAYOUT,
    DINT_SIZE,
    EDITABLE_ADDRESSES,
    LOCKED_ADDRESSES,
    PROBE_READY,
    PROBE_UNREACHABLE,
    PROBE_UNSIGNED,
    READ_SIZE,
    SIGNAL_LAYOUT,
    FakePLCClient,
    FakePLCProbeClient,
    describe_error,
    duplicate_labels,
    effective_labels,
    parse_db,
    signal_label,
    signal_offset,
)
from app.recovery import apply_reset, parse_options
from app.security import hash_password, verify_password
from app.storage import Storage
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app, end_session, start_session, storage as app_storage


def test_parse_standard_layout():
    data = bytearray(READ_SIZE)
    data[0] = 0b00000111
    data[1] = 0b10000001
    data[0] |= 1 << 4  # Counter at 0.4, inside the same word as the status bits
    data[14:19] = b"SPCLP"
    data[20:24] = (42).to_bytes(4, "big", signed=True)
    data[2:6] = (1500).to_bytes(4, "big", signed=True)
    data[6:10] = (-42).to_bytes(4, "big", signed=True)
    data[10:14] = (0).to_bytes(4, "big", signed=True)
    reading = parse_db(bytes(data))
    assert reading.bits["0.0"] is True
    assert reading.bits["0.1"] is True
    assert reading.bits["0.2"] is True
    assert reading.bits["0.4"] is True
    assert reading.bits["0.3"] is False
    assert reading.count == 42
    # DInt is signed, so a negative value must survive the round trip.
    assert reading.integers == {"2.0": 1500, "6.0": -42, "10.0": 0}


def test_parse_db_reports_every_address_even_with_repeated_labels():
    """Labels are user editable, so readings are keyed by address and never collapse."""
    reading = parse_db(bytes(READ_SIZE))
    assert set(reading.bits) == {spec.address for spec in SIGNAL_LAYOUT}
    assert len(reading.bits) == len(SIGNAL_LAYOUT)


def test_fake_client_produces_readings():
    first = FakePLCClient(count=10).read(32)
    assert first.count == 10
    assert first.bits["0.0"] is True
    assert first.bits["0.4"] is True


def test_snap7_read_requests_full_standard_contract(monkeypatch):
    captured = {}

    class FakeSnap7Client:
        def connect(self, ip, rack, slot):
            captured["connection"] = (ip, rack, slot)

        def db_read(self, db_number, start, size):
            captured["read"] = (db_number, start, size)
            data = bytearray(READ_SIZE)
            data[0] = 0b00010001
            data[20:24] = (7).to_bytes(4, "big", signed=True)
            return bytes(data)

        def disconnect(self):
            captured["disconnected"] = True

    import snap7
    monkeypatch.setattr(snap7.client, "Client", FakeSnap7Client)
    from app.plc import Snap7PLCClient

    reading = Snap7PLCClient("192.168.0.1").read(53)
    assert captured["connection"] == ("192.168.0.1", 0, 1)
    # One read covers the status word, the signature and the counter.
    assert captured["read"] == (53, 0, READ_SIZE)
    client = Snap7PLCClient("192.168.0.1")
    client.read(53)
    client.close()
    assert captured["disconnected"] is True
    assert reading.count == 7


def test_machine_status_reports_every_address_with_labels(monkeypatch):
    from app.main import machine_status
    from app.plc import PLCReading, SIGNAL_LAYOUT

    class FakeClient:
        def read(self, db_number):
            bits = {spec.address: False for spec in BOOL_LAYOUT}
            bits["0.0"] = True
            bits["0.2"] = True
            bits["0.3"] = True
            bits["0.4"] = True
            integers = {spec.address: 0 for spec in DINT_LAYOUT}
            integers["2.0"] = 1500
            return PLCReading(
                timestamp=datetime.now(timezone.utc), bits=bits, integers=integers, count=7
            )

    monkeypatch.setattr("app.main.client_for", lambda machine: FakeClient())
    area_id = app_storage.add_area("Test Plant", "Status Area")
    machine_id = app_storage.add_machine(area_id, "Status Machine", "fake", 53, "UTC")
    app_storage.set_signal_labels(machine_id, {"0.5": "Portao de entrada"})
    status = machine_status(machine_id)

    assert status.connected is True
    assert status.auto is True and status.run is False
    assert status.fault is True and status.safety is True
    assert status.count == 7
    assert [item.address for item in status.signals] == [spec.address for spec in SIGNAL_LAYOUT]

    by_address = {item.address: item for item in status.signals}
    assert by_address["0.0"].label == "Automático"
    assert by_address["0.2"].label == "Falha"
    assert by_address["0.3"].label == "Segurança"
    assert by_address["0.4"].label == "Contador"
    assert by_address["0.4"].kind == "counter"
    assert by_address["0.5"].label == "Portao de entrada"
    assert by_address["0.5"].type == "BOOL"
    assert by_address["0.5"].kind == "custom"
    assert by_address["0.5"].value is False
    # The DInts travel in the same list, with numbers instead of bits.
    assert by_address["2.0"].type == "DINT"
    assert by_address["2.0"].value == 1500
    assert by_address["6.0"].value == 0
    # Only the five standard signals are read only in the dashboard editor.
    assert [item.address for item in status.signals if not item.editable] == [
        "0.0", "0.1", "0.2", "0.3", "0.4"
    ]
    app_storage.delete_machine(machine_id)


def test_offline_machine_still_lists_signal_addresses():
    from app.plc import SIGNAL_LAYOUT

    area_id = app_storage.add_area("Test Plant", "Offline Area")
    machine_id = app_storage.add_machine(area_id, "Offline", "192.0.2.1", 53, "UTC")
    app_storage.set_signal_labels(machine_id, {"1.7": "Sensor final"})
    status = TestClient(app).get(f"/api/machines/{machine_id}/status").json()
    assert status["connected"] is False
    assert len(status["signals"]) == len(SIGNAL_LAYOUT)
    by_address = {item["address"]: item for item in status["signals"]}
    assert by_address["1.7"]["label"] == "Sensor final"
    assert by_address["1.7"]["value"] is None, "offline must not be reported as a false bit"
    app_storage.delete_machine(machine_id)


def test_snap7_client_reuses_connection_for_multiple_dbs(monkeypatch):
    connections = []

    class FakeSnap7Client:
        def connect(self, ip, rack, slot):
            connections.append((ip, rack, slot))

        def db_read(self, db_number, start, size):
            data = bytearray(READ_SIZE)
            data[0] = 1
            data[20:24] = db_number.to_bytes(4, "big", signed=True)
            return bytes(data)

        def disconnect(self):
            pass

    import snap7
    monkeypatch.setattr(snap7.client, "Client", FakeSnap7Client)
    from app.plc import Snap7PLCClient

    client = Snap7PLCClient("192.168.0.1")
    assert client.read(53).count == 53
    assert client.read(54).count == 54
    assert connections == [("192.168.0.1", 0, 1)]
    client.close()


def test_password_hash_is_not_plaintext():
    encoded = hash_password("senha-segura")
    assert encoded != "senha-segura"
    assert verify_password("senha-segura", encoded)
    assert not verify_password("errada", encoded)


def test_counter_reset_does_not_create_negative_delta(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    storage.add_area("Planta", "Area")
    machine_id = storage.add_machine(1, "M1", "fake", 32, "UTC")
    now = datetime(2026, 1, 1, 10, 10, tzinfo=timezone.utc)
    assert storage.save_sample(machine_id, 100, now) == 0
    assert storage.save_sample(machine_id, 130, now.replace(minute=20)) == 30
    assert storage.save_sample(machine_id, 5, now.replace(minute=30)) == 0
    assert storage.save_sample(machine_id, 8, now.replace(minute=40)) == 3
    assert storage.hourly_counts(machine_id)[0]["quantity"] == 33


def test_area_lookup_uses_area_table(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    area_id = storage.add_area("Planta", "Area")
    assert storage.get_area(area_id)["name"] == "Area"
    assert storage.get_area(999) is None


def test_machine_can_be_updated(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    area_id = storage.add_area("Planta", "Area")
    machine_id = storage.add_machine(area_id, "M1", "fake", 32, "UTC")
    assert storage.update_machine(machine_id, "M1 Atualizada", "192.168.0.10", 40, "America/Sao_Paulo")
    machine = storage.get_machine(machine_id)
    assert machine["name"] == "M1 Atualizada"
    assert machine["ip"] == "192.168.0.10"
    assert machine["db_number"] == 40


def test_machine_delete_removes_machine_history(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    area_id = storage.add_area("Planta", "Area")
    machine_id = storage.add_machine(area_id, "M1", "fake", 32, "UTC")
    storage.save_sample(machine_id, 10)
    assert storage.delete_machine(machine_id)
    assert storage.get_machine(machine_id) is None
    assert storage.hourly_counts(machine_id) == []
    assert not storage.delete_machine(machine_id)


def test_machine_list_includes_plant_and_area_names(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    area_id = storage.add_area("Planta Norte", "Area Corte")
    storage.add_machine(area_id, "M1", "fake", 53, "UTC")
    machine = storage.list_machines()[0]
    assert machine["plant_name"] == "Planta Norte"
    assert machine["area_name"] == "Area Corte"


def test_unreachable_plc_is_reported_as_disconnected():
    area_id = app_storage.add_area("Test Plant", "Test Area")
    machine_id = app_storage.add_machine(area_id, "Unreachable", "192.0.2.1", 32, "UTC")
    client = TestClient(app)
    response = client.get(f"/api/machines/{machine_id}/status")
    assert response.status_code == 200
    assert response.json()["connected"] is False
    app_storage.delete_machine(machine_id)


def test_dashboard_shell_references_existing_assets():
    client = TestClient(app)
    page = client.get("/")
    assert page.status_code == 200
    for asset in ("/static/app.js", "/static/styles.css", "/static/icons.js"):
        assert asset in page.text, f"{asset} is not referenced by the dashboard page"
        assert client.get(asset).status_code == 200, f"{asset} is not served"


def test_footer_links_to_repository_and_instagram():
    page = TestClient(app).get("/")
    assert page.status_code == 200
    assert "https://github.com/Autoric-Automation-Systems/sp-clp" in page.text
    assert "https://www.instagram.com/autoricbr/" in page.text
    assert "@autoricbr" in page.text


def test_dashboard_wires_the_signal_labels_editor():
    client = TestClient(app)
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="signals-dialog"' in page.text
    assert 'id="signals-editor"' in page.text
    assert 'id="signals-save"' in page.text

    script = client.get("/static/app.js").text
    for hook in (
        ".signals-toggle",
        ".labels-machine",
        "openSignalLabels",
        "saveSignalLabels",
        "/signals",
    ):
        assert hook in script, f"app.js does not wire {hook}"


def test_snap7_error_bytes_are_decoded_for_logging():
    assert describe_error(RuntimeError(b" TCP : Unreachable peer")) == "TCP : Unreachable peer"
    assert describe_error(ValueError("falha de leitura")) == "falha de leitura"
    assert describe_error(ValueError()) == "ValueError"


def test_snap7_library_error_spam_is_silenced(caplog):
    assert logging.getLogger("snap7").getEffectiveLevel() == logging.CRITICAL
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("snap7.common").error(b" TCP : Unreachable peer")
    assert [r for r in caplog.records if r.name.startswith("snap7")] == []


def test_unreachable_plc_failure_is_logged_at_debug_level(caplog):
    area_id = app_storage.add_area("Test Plant", "Test Area")
    machine_id = app_storage.add_machine(area_id, "Unreachable", "192.0.2.1", 32, "UTC")
    client = TestClient(app)
    with caplog.at_level(logging.DEBUG, logger="sp_clp"):
        response = client.get(f"/api/machines/{machine_id}/status")
    assert response.status_code == 200
    assert response.json()["connected"] is False
    records = [r for r in caplog.records if r.name == "sp_clp"]
    assert len(records) == 1, f"expected one diagnostic log, got {len(records)}"
    assert records[0].levelno == logging.DEBUG
    assert "192.0.2.1" in records[0].getMessage()
    app_storage.delete_machine(machine_id)


def test_hourly_counts_are_reported_in_the_machine_timezone():
    area_id = app_storage.add_area("Planta Fuso", "Area Fuso")
    machine_id = app_storage.add_machine(area_id, "Fuso", "fake", 53, "America/Sao_Paulo")
    app_storage.save_sample(machine_id, 100, datetime(2026, 9, 26, 10, 15, tzinfo=timezone.utc))
    app_storage.save_sample(machine_id, 160, datetime(2026, 9, 26, 10, 45, tzinfo=timezone.utc))
    try:
        body = TestClient(app).get(
            f"/api/machines/{machine_id}/hourly-counts?day=2026-09-26"
        ).json()
        assert body["day"] == "2026-09-26"
        assert body["first_day"] == "2026-09-26"
        assert len(body["slots"]) == 24
        # 10:15 UTC is 07:15 in Sao Paulo, and both samples share that bucket.
        assert body["slots"][7]["local_hour"] == "2026-09-26T07:00:00-03:00"
        assert body["slots"][7]["quantity"] == 60
        assert sum(slot["quantity"] for slot in body["slots"]) == 60
    finally:
        app_storage.delete_machine(machine_id)


def test_hourly_counts_answer_only_the_requested_day():
    area_id = app_storage.add_area("Planta Dia", "Area Dia")
    machine_id = app_storage.add_machine(area_id, "Dia", "fake", 53, "America/Sao_Paulo")
    # The first sample only sets the baseline, so it adds no quantity.
    app_storage.save_sample(machine_id, 10, datetime(2026, 9, 26, 12, 5, tzinfo=timezone.utc))
    # 01:05 UTC on the 27th is 22:05 of the 26th in Sao Paulo, so this belongs to the 26th.
    app_storage.save_sample(machine_id, 40, datetime(2026, 9, 27, 1, 5, tzinfo=timezone.utc))
    # 15:05 UTC is 12:05 on the 27th, so this one belongs to the 27th.
    app_storage.save_sample(machine_id, 50, datetime(2026, 9, 27, 15, 5, tzinfo=timezone.utc))
    client = TestClient(app)
    try:
        first = client.get(f"/api/machines/{machine_id}/hourly-counts?day=2026-09-26").json()
        second = client.get(f"/api/machines/{machine_id}/hourly-counts?day=2026-09-27").json()
        assert sum(slot["quantity"] for slot in first["slots"]) == 30
        assert sum(slot["quantity"] for slot in second["slots"]) == 10
        # The day starts and ends on the local clock, not on the UTC one.
        assert first["slots"][0]["local_hour"] == "2026-09-26T00:00:00-03:00"
        assert first["slots"][-1]["local_hour"] == "2026-09-26T23:00:00-03:00"
        # The navigation limits cover every day with data, and never end before today.
        assert first["first_day"] == "2026-09-26"
        assert second["first_day"] == "2026-09-26"
        assert second["last_day"] >= second["today"]

        bad = client.get(f"/api/machines/{machine_id}/hourly-counts?day=ontem")
        assert bad.status_code == 422
        assert "AAAA-MM-DD" in bad.json()["detail"]
    finally:
        app_storage.delete_machine(machine_id)


def test_hourly_counts_default_to_today_in_the_machine_timezone():
    from app.timezones import today_in

    area_id = app_storage.add_area("Planta Hoje", "Area Hoje")
    machine_id = app_storage.add_machine(area_id, "Hoje", "fake", 53, "America/Sao_Paulo")
    try:
        body = TestClient(app).get(f"/api/machines/{machine_id}/hourly-counts").json()
        assert body["day"] == today_in("America/Sao_Paulo")
        assert body["first_day"] == body["last_day"] == body["day"]
        assert sum(slot["quantity"] for slot in body["slots"]) == 0
    finally:
        app_storage.delete_machine(machine_id)


def test_hourly_counts_survive_an_unknown_stored_timezone():
    area_id = app_storage.add_area("Planta Fuso", "Area Fuso")
    machine_id = app_storage.add_machine(area_id, "Fuso Invalido", "fake", 53, "Marte/Olympus")
    app_storage.save_sample(machine_id, 5, datetime(2026, 9, 26, 10, 5, tzinfo=timezone.utc))
    app_storage.save_sample(machine_id, 12, datetime(2026, 9, 26, 10, 20, tzinfo=timezone.utc))
    try:
        body = TestClient(app).get(
            f"/api/machines/{machine_id}/hourly-counts?day=2026-09-26"
        ).json()
        # The unknown zone falls back to UTC, so the bucket stays on hour 10.
        assert body["slots"][10]["local_hour"] == "2026-09-26T10:00:00+00:00"
        assert body["slots"][10]["quantity"] == 7
    finally:
        app_storage.delete_machine(machine_id)


def test_machine_timezone_is_validated_before_saving():
    with pytest.raises(ValidationError):
        MachineInput(name="M1", ip="10.0.0.1", db_number=53, timezone="Marte/Olympus")
    machine = MachineInput(name="M1", ip="10.0.0.1", db_number=53, timezone="America/Sao_Paulo")
    assert machine.timezone == "America/Sao_Paulo"


# --- o dia de um sinal, a partir do que ficou gravado --------------------------


def _readings(start, count, step_seconds=5):
    return [start + timedelta(seconds=step_seconds * index) for index in range(count)]


def test_a_signal_day_splits_on_the_moment_the_bit_moved():
    from app.signals import build_day

    start = datetime(2026, 9, 29, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=24)
    now = start + timedelta(seconds=60)
    # The first reading of the day is also the first event: what the panel saw is
    # what it stored, at the same instant.
    events = [(start, False), (start + timedelta(seconds=30), True)]

    day = build_day("0.1", _readings(start, 13), events, start, end, now)

    assert [(segment.seconds, segment.value) for segment in day.segments] == [
        (30, False),
        (30, True),
    ]
    assert (day.on_seconds, day.off_seconds, day.unknown_seconds) == (30, 30, 0)
    # The reading 5 s ago still speaks for now, so nothing is left unknown.
    assert day.elapsed_seconds == 60
    assert day.percent(day.on_seconds) == 50


def test_a_stretch_with_no_readings_is_not_a_stopped_machine():
    from app.signals import build_day

    start = datetime(2026, 9, 29, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=24)
    events = [(start, True)]
    # Ten seconds of readings, ten minutes of nothing, then readings again.
    samples = _readings(start, 3) + _readings(start + timedelta(minutes=10), 3)
    now = start + timedelta(minutes=10, seconds=10)

    day = build_day("0.0", samples, events, start, end, now)

    assert [(segment.seconds, segment.value) for segment in day.segments] == [
        (10, True),
        (590, None),
        (10, True),
    ]
    # The hole runs from the last reading to the first one after it: ten seconds of
    # the 610 are attested on each side, the rest is not.
    assert day.unknown_seconds == 590
    # The hole counts in the denominator, so the percentages cannot hide it.
    assert day.percent(day.unknown_seconds) == round(100 * 590 / 610)


def test_the_state_at_midnight_comes_from_the_change_before_it():
    from app.signals import build_day

    start = datetime(2026, 9, 29, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=24)
    # The bit was already set at 23:50 the day before, and only fell at 10:00.
    events = [(start - timedelta(minutes=10), True), (start + timedelta(hours=10), False)]
    now = start + timedelta(hours=12)
    # Read every ten minutes across the twelve hours that matter, with the
    # tolerance opened to match, so the fall can be measured.
    samples = [start + timedelta(minutes=10 * index) for index in range(0, 73)]

    day = build_day(
        "0.2", samples, events, start, end, now, tolerance=timedelta(minutes=15)
    )

    first = day.segments[0]
    assert first.start == start
    assert first.value is True
    assert day.off_seconds == 7200


def test_a_day_with_no_readings_at_all_is_unknown_from_end_to_end():
    from app.signals import build_day

    start = datetime(2026, 9, 29, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=24)
    day = build_day("0.3", [], [], start, end, start + timedelta(hours=6))

    assert [(segment.seconds, segment.value) for segment in day.segments] == [(21600, None)]
    assert (day.on_seconds, day.off_seconds) == (0, 0)
    assert day.percent(day.unknown_seconds) == 100


def test_a_past_day_is_measured_over_the_whole_day():
    from app.signals import build_day

    start = datetime(2026, 9, 28, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=24)
    events = [(start, True), (start + timedelta(hours=18), False)]
    # A full day of readings every ten minutes, like a machine nobody closed.
    samples = [start + timedelta(minutes=10 * index) for index in range(0, 145)]
    now = start + timedelta(days=3)

    day = build_day(
        "0.1", samples, events, start, end, now, tolerance=timedelta(minutes=15)
    )

    assert day.elapsed_seconds == 86400
    assert day.percent(day.on_seconds) == 75


def test_the_percentages_do_not_divide_by_zero_on_a_day_that_has_not_started():
    from app.signals import build_day

    start = datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=24)
    day = build_day("0.0", [], [], start, end, start - timedelta(hours=1))

    assert day.segments == []
    assert day.elapsed_seconds == 0
    assert day.percent(0) == 0


# --- o que o painel grava enquanto ninguem esta olhando ------------------------


def test_transitions_are_stored_only_when_the_bit_moves():
    area_id = app_storage.add_area("Planta Gravacao", "Area Gravacao")
    machine_id = app_storage.add_machine(area_id, "Transicoes", "fake", 53, "UTC")
    moment = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
    try:
        # The first look stores everything: nothing was known before it.
        assert app_storage.save_transitions(machine_id, {"0.0": True, "0.1": False}, moment) == 2
        # The same values again cost nothing, which is the point of the table.
        assert (
            app_storage.save_transitions(
                machine_id, {"0.0": True, "0.1": False}, moment + timedelta(seconds=5)
            )
            == 0
        )
        assert (
            app_storage.save_transitions(
                machine_id, {"0.0": False, "0.1": False}, moment + timedelta(seconds=10)
            )
            == 1
        )
        events = app_storage.signal_events(
            machine_id, ["0.0", "0.1"], moment, moment + timedelta(minutes=1)
        )
        assert [(when, bit) for when, bit in events["0.0"]] == [
            (moment, True),
            (moment + timedelta(seconds=10), False),
        ]
        assert [bit for _, bit in events["0.1"]] == [False]
    finally:
        app_storage.delete_machine(machine_id)


def test_only_the_four_standard_signals_are_recorded():
    from app.recorder import recorded_addresses

    # COUNTER is a diagnostic pulse and the free signals belong to the customer, so
    # neither belongs on a timeline of the day.
    assert recorded_addresses() == ["0.0", "0.1", "0.2", "0.3"]


def test_the_recorder_keeps_the_count_and_the_changes_without_a_browser():
    from app.recorder import record_once

    area_id = app_storage.add_area("Planta 24h", "Area 24h")
    machine_id = app_storage.add_machine(area_id, "Gravador", "fake", 53, "UTC")
    client = FakePLCClient()
    try:
        assert record_once(app_storage, lambda machine: client) == 1
        # A reading and a transition for each of the four signals.
        assert len(app_storage.hourly_counts(machine_id)) == 1
        day = app_storage.signal_events(
            machine_id, ["0.0", "0.1", "0.2", "0.3"], datetime(2020, 1, 1, tzinfo=timezone.utc),
            datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
        assert [len(day[address]) for address in ("0.0", "0.1", "0.2", "0.3")] == [1, 1, 1, 1]
    finally:
        app_storage.delete_machine(machine_id)


def test_the_recorder_survives_a_machine_that_does_not_answer():
    from app.recorder import record_once

    area_id = app_storage.add_area("Planta Muda", "Area Muda")
    machine_id = app_storage.add_machine(area_id, "Sem resposta", "10.255.255.1", 53, "UTC")

    def refusing(_machine):
        raise ConnectionError("sem resposta")

    try:
        assert record_once(app_storage, refusing) == 0
        assert app_storage.hourly_counts(machine_id) == []
    finally:
        app_storage.delete_machine(machine_id)


def test_the_history_is_pruned_to_the_retention_window(tmp_path):
    """Prune sweeps every machine, so this one runs on a database of its own.

    On the shared file it also deleted what the tests before it had written, and
    the count only matched until the calendar passed the fixed dates those tests
    use: the assertion held on the day it was written and broke four days later.
    """
    store = Storage(tmp_path / "retention.sqlite3")
    area_id = store.add_area("Planta Retencao", "Area Retencao")
    machine_id = store.add_machine(area_id, "Retencao", "fake", 53, "UTC")
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=8)
    fresh = now - timedelta(days=1)
    try:
        store.save_sample(machine_id, 10, old)
        store.save_transitions(machine_id, {"0.0": True}, old)
        store.save_sample(machine_id, 40, fresh)
        store.save_transitions(machine_id, {"0.0": False}, fresh)

        removed = store.prune(now - timedelta(days=7))

        # The old reading, its hourly row and the old transition.
        assert removed == 3
        assert [row["quantity"] for row in store.hourly_counts(machine_id)] == [30]
        assert len(store.sample_times(machine_id, now - timedelta(days=2), now)) == 1
    finally:
        store.delete_machine(machine_id)


def test_the_signals_day_endpoint_reports_the_stretches():
    area_id = app_storage.add_area("Planta Sinal", "Area Sinal")
    machine_id = app_storage.add_machine(area_id, "Sinal", "fake", 53, "UTC")
    start = datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)
    try:
        # Read for a minute at midnight, then again at noon: the state in between is
        # known, and the rest of the day is a hole nobody was looking at.
        for index in range(0, 13):
            moment = start + timedelta(seconds=5 * index)
            app_storage.save_sample(machine_id, 100 + index, moment)
            app_storage.save_transitions(machine_id, {"0.1": True}, moment)
        noon = start + timedelta(hours=12)
        for index in range(0, 13):
            moment = noon + timedelta(seconds=5 * index)
            app_storage.save_sample(machine_id, 200 + index, moment)
            app_storage.save_transitions(machine_id, {"0.1": False}, moment)

        body = TestClient(app).get(f"/api/machines/{machine_id}/signals-day?day=2026-09-20").json()

        assert body["day"] == "2026-09-20"
        assert body["elapsed_seconds"] == 86400
        assert [signal["address"] for signal in body["signals"]] == ["0.0", "0.1", "0.2", "0.3"]
        run = next(signal for signal in body["signals"] if signal["address"] == "0.1")
        assert run["label"] == "Produção"
        assert [(segment["value"], segment["start"][11:19]) for segment in run["segments"][:2]] == [
            (True, "00:00:00"),
            (None, "00:01:00"),
        ]
        assert run["on_seconds"] == 60
        # The noon stretch adds the twenty seconds a reading still speaks for after
        # it, which is what makes the live day reach up to the present.
        assert run["off_seconds"] == 60 + 20
        assert run["unknown_seconds"] == 86400 - 60 - 80
        # The three parts are of the whole day and add up to it.
        assert 99 <= run["on_percent"] + run["off_percent"] + run["unknown_percent"] <= 101
        assert run["unknown_percent"] == 100
        # A signal nothing was ever recorded for is unknown from end to end.
        auto = next(signal for signal in body["signals"] if signal["address"] == "0.0")
        assert auto["unknown_seconds"] == 86400
    finally:
        app_storage.delete_machine(machine_id)


def test_the_signals_day_endpoint_validates_the_date_and_the_machine():
    client = TestClient(app)
    assert client.get("/api/machines/999999/signals-day").status_code == 404

    area_id = app_storage.add_area("Planta Data", "Area Data")
    machine_id = app_storage.add_machine(area_id, "Data", "fake", 53, "UTC")
    try:
        assert client.get(f"/api/machines/{machine_id}/signals-day?day=20-09-2026").status_code == 422
    finally:
        app_storage.delete_machine(machine_id)


def test_the_trend_screen_is_its_own_window():
    page = TestClient(app).get("/").text
    assert 'id="trend-dialog"' in page
    assert "TENDÊNCIA DOS SINAIS" in page
    # The counts window has to survive beside it: the two are different subjects.
    assert 'id="chart-dialog"' in page

    script = TestClient(app).get("/static/app.js").text
    assert "openTrend" in script
    assert "'/api/machines/' + trendMachine.id + '/signals-day'" in script
    # Its own button and its own attribute, so one window never opens the other.
    assert "data-trend-id" in script
    assert "querySelectorAll('.trend-open')" in script
    assert "querySelectorAll('.chart-open')" in script
    # The words come from the same table the card uses.
    assert "BIT_WORDS[signal.kind]" in script

    css = TestClient(app).get("/static/styles.css").text
    assert ".chart-open,.trend-open{" in css
    assert "#trend-dialog{width" in css
    # The hole in the day needs a look of its own, not the same grey as the off bit.
    assert ".trend-segment.off{background:#c6d4dd}" in css
    assert ".trend-segment.unknown{" in css


def test_the_trend_icon_is_vendored_with_the_others():
    icons = TestClient(app).get("/static/icons.js").text
    assert 'trending-up' in icons


def test_the_trend_screen_is_its_own_window():
    page = TestClient(app).get("/").text
    assert 'id="trend-dialog"' in page
    assert "TENDÊNCIA DOS SINAIS" in page
    # The counts window has to survive beside it: the two are different subjects.
    assert 'id="chart-dialog"' in page

    script = TestClient(app).get("/static/app.js").text
    assert "openTrend" in script
    assert "'/api/machines/' + trendMachine.id + '/signals-day'" in script
    # Its own button and its own attribute, so one window never opens the other.
    assert "data-trend-id" in script
    assert "querySelectorAll('.trend-open')" in script
    assert "querySelectorAll('.chart-open')" in script
    # The words come from the same table the card uses.
    assert "BIT_WORDS[signal.kind]" in script

    css = TestClient(app).get("/static/styles.css").text
    assert ".chart-open,.trend-open{" in css
    assert "#trend-dialog{width" in css
    # The hole in the day needs a look of its own, not the same grey as the off bit.
    assert ".trend-segment.off{background:#c6d4dd}" in css
    assert ".trend-segment.unknown{" in css


def test_the_trend_icon_is_vendored_with_the_others():
    icons = TestClient(app).get("/static/icons.js").text
    assert "trending-up" in icons


def test_the_help_page_says_what_the_panel_keeps():
    page = TestClient(app).get("/").text
    assert "últimos 7 dias" in page
    # The customer's fear is losing the registration, so the page says it does not
    # go away with the history.
    assert "cadastro não entra" in page
    assert "mesmo com o navegador fechado" in page


def test_the_tv_panel_is_its_own_address():
    from app.slugs import RESERVED_PAGES, plant_name_error

    # A plant must never answer on the panel's address.
    assert "TVPanel" in RESERVED_PAGES
    assert plant_name_error("tvpanel", set()) is not None

    response = TestClient(app).get("/tvpanel")
    assert response.status_code == 200
    assert 'id="tv-page"' in response.text


def test_the_tv_panel_rotates_and_keeps_a_way_out():
    page = TestClient(app).get("/").text
    assert 'id="tv-stage"' in page
    assert 'id="tv-progress"' in page
    # A discreet way back to the normal panel.
    assert 'data-route="/"' in page
    assert "Sair do modo TV" in page
    # The developer's mark: the logo without a link, the slogan, the addresses in
    # words. A remote control cannot click, so a band of links would be decoration.
    assert 'class="tv-brand"' in page
    assert "Você imagina, nós fazemos funcionar" in page
    assert "www.autoric.com.br" in page
    assert "instagram/@autoricbr" in page

    script = TestClient(app).get("/static/app.js").text
    assert "TV_SECONDS = 5" in script
    assert "function renderTv" in script
    assert "function startTv" in script
    assert "function stopTv" in script
    assert "'#tv-stage'" in script
    # The rotation stops when the page is left, instead of running in the background.
    assert "stopTv();" in script
    # The TV address is not a plant, whatever the machine list says.
    assert "segment === TV_SEGMENT) return null" in script

    css = TestClient(app).get("/static/styles.css").text
    assert "body.tv .topbar{display:none}" in css
    assert "body.tv footer{position:fixed" in css
    assert "@keyframes tv-progress{" in css
    # A band of links on a wall is decoration: the TV footer writes the brand
    # instead, and the linked logo belongs to the normal panel only.
    assert "body.tv .footer-links{display:none}" in css
    assert "body.tv .authoric-link{display:none}" in css
    # On a wall nobody hovers, so the TV card writes the name of each signal.
    assert ".tv-state-name{" in css
    assert ".tv-state.unknown .tv-state-icon{border:3px dashed" in css


def test_the_tv_controls_are_a_videocassette_deck():
    """The operator already knows a cassette deck: back, pause, play, forward."""
    page = TestClient(app).get("/").text
    for button in ("tv-back", "tv-hold", "tv-play", "tv-next"):
        assert f'id="{button}"' in page
    # Order on screen is the order of the tape, not the order of the code.
    assert (
        page.index('id="tv-back"')
        < page.index('id="tv-hold"')
        < page.index('id="tv-play"')
        < page.index('id="tv-next"')
    )
    icons = TestClient(app).get("/static/icons.js").text
    for glyph in ("skip-back", "pause", "skip-forward"):
        assert f'"{glyph}":' in icons
    assert 'data-icon="skip-back"' in page
    assert 'data-icon="pause"' in page
    assert 'data-icon="skip-forward"' in page

    script = TestClient(app).get("/static/app.js").text
    assert "function stepTv" in script
    assert "function setTvPaused" in script
    assert "function updateTvControls" in script
    # Backward from the first slide wraps to the last one, like a ring.
    assert "% lastItems.length" in script
    # The player follows the state: the button in force is the lit one, so a
    # transparent remote shows what the wall is doing.
    assert "'#tv-hold'" in script
    assert "'#tv-play'" in script
    assert "classList.toggle('on'" in script
    # Arriving at the TV address always starts playing, even after a pause.
    assert "setTvPaused(false)" in script
    # Each button drives the deck, and a slide asked for by hand gets its whole
    # time on screen.
    assert "document.querySelector('#tv-back').onclick = function () { stepTv(-1); };" in script
    assert "document.querySelector('#tv-next').onclick = function () { stepTv(1); };" in script
    assert "document.querySelector('#tv-hold').onclick = function () { setTvPaused(true); };" in script
    assert "document.querySelector('#tv-play').onclick = function () { setTvPaused(false); };" in script

    css = TestClient(app).get("/static/styles.css").text
    # The tape bar stops and turns amber while paused.
    assert "body.tv-paused .tv-progress{animation-play-state:paused;background:#ffb703}" in css
    # A button that is off does not light up under the cursor.
    assert ".tv-control:disabled:hover" in css


def test_the_tv_card_fits_a_short_screen():
    """A centred card overflows upwards, and that is what hid the plant name.

    Every size of the card also watches the height of the screen, so a notebook
    with the browser open shrinks the card instead of pushing it off the top.
    """
    css = TestClient(app).get("/static/styles.css").text
    # The stage is the window minus the brand band, and it reserves room at the
    # top, so the plant · area line is never the first thing to leave the screen.
    assert "height:calc(100vh - var(--tv-band))" in css
    assert "padding:clamp(18px,5vh,60px) 5vw clamp(14px,3.5vh,40px)" in css
    # vmin is the shorter side, so a short window shrinks the type.
    assert "font:700 clamp(28px,6.2vmin,108px)/1.02" in css
    assert "width:clamp(48px,6.2vmin,138px)" in css
    assert "font:700 clamp(34px,8.4vmin,158px)/1" in css
    assert "gap:clamp(9px,2.1vh,26px)" in css


def test_the_tv_mode_is_in_the_menu():
    page = TestClient(app).get("/").text
    assert 'data-route="/TVPanel"' in page
    assert "Modo TV" in page
    assert 'data-icon="tv"' in page

    script = TestClient(app).get("/static/app.js").text
    # The route is in the menu, so its button lights up while the panel runs.
    assert "if (tv) return '/TVPanel';" in script


def test_the_status_endpoint_does_not_write_history():
    """The recorder owns the history, so opening the panel cannot change it."""
    area_id = app_storage.add_area("Planta Leitura", "Area Leitura")
    machine_id = app_storage.add_machine(area_id, "Somente leitura", "fake", 53, "UTC")
    try:
        assert TestClient(app).get(f"/api/machines/{machine_id}/status").status_code == 200
        assert app_storage.hourly_counts(machine_id) == []
        assert app_storage.sample_times(machine_id, datetime(2020, 1, 1, tzinfo=timezone.utc), datetime(2030, 1, 1, tzinfo=timezone.utc)) == []
    finally:
        app_storage.delete_machine(machine_id)


def test_signal_labels_are_keyed_by_address():
    area_id = app_storage.add_area("Test Plant", "Labels Area")
    machine_id = app_storage.add_machine(area_id, "Labels", "fake", 53, "UTC")
    app_storage.set_signal_labels(machine_id, {"0.4": "Portao de entrada", "0.5": "Portao de saida"})
    assert app_storage.signal_labels(machine_id) == {
        "0.4": "Portao de entrada",
        "0.5": "Portao de saida",
    }
    app_storage.delete_machine(machine_id)


def test_standard_signals_use_the_contract_labels():
    labels = effective_labels()
    assert labels["0.0"] == "Automático"
    assert labels["0.1"] == "Produção"
    assert labels["0.2"] == "Falha"
    assert labels["0.3"] == "Segurança"
    assert labels["0.4"] == "Contador"
    assert labels["0.5"] == "Sinal 0.5"


def test_the_contract_has_five_standard_signals():
    from app.plc import LOCKED_KINDS

    standard = [spec for spec in SIGNAL_LAYOUT if spec.kind in LOCKED_KINDS]
    assert [(spec.address, spec.kind, spec.default_label) for spec in standard] == [
        ("0.0", "auto", "Automático"),
        ("0.1", "run", "Produção"),
        ("0.2", "fault", "Falha"),
        ("0.3", "safety", "Segurança"),
        ("0.4", "counter", "Contador"),
    ]


def test_locked_signals_ignore_stored_overrides():
    # A row left behind by an older version must never rename a standard signal.
    auto, run, fault, safety, counter = SIGNAL_LAYOUT[:5]
    assert signal_label(auto, {"0.0": "Outro nome"}) == "Automático"
    assert signal_label(run, {"0.1": "Outro nome"}) == "Produção"
    assert signal_label(fault, {"0.2": "Outro nome"}) == "Falha"
    assert signal_label(safety, {"0.3": "Saude"}) == "Segurança"
    assert signal_label(counter, {"0.4": "Peças boas"}) == "Contador"


def test_only_the_standard_signals_are_locked():
    assert LOCKED_ADDRESSES == {"0.0", "0.1", "0.2", "0.3", "0.4"}
    assert EDITABLE_ADDRESSES == {
        "0.5", "0.6", "0.7",
        "1.0", "1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7",
        "2.0", "6.0", "10.0",
    }
    assert EDITABLE_ADDRESSES | LOCKED_ADDRESSES == {spec.address for spec in SIGNAL_LAYOUT}


def test_the_contract_holds_sixteen_bools_and_three_integers():
    """The PLC block declares exactly 16 BOOLs and three DInts, with no gap."""
    assert [spec.address for spec in SIGNAL_LAYOUT] == [
        "0.0", "0.1", "0.2", "0.3", "0.4", "0.5", "0.6", "0.7",
        "1.0", "1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7",
        "2.0", "6.0", "10.0",
    ]
    assert [spec.type for spec in BOOL_LAYOUT] == ["BOOL"] * 16
    assert [(spec.address, spec.type) for spec in DINT_LAYOUT] == [
        ("2.0", "DINT"), ("6.0", "DINT"), ("10.0", "DINT")
    ]


def test_duplicate_labels_are_detected():
    assert duplicate_labels({"0.5": "Portao", "0.6": "Portao"}) == ["Portao"]
    # Case differences would be indistinguishable on the dashboard.
    assert duplicate_labels({"0.5": "portao", "0.6": "Portao"}) == ["portao"]
    assert duplicate_labels({"0.5": "Portao", "0.6": "portao"}) == ["Portao"]
    # A free signal may not steal the name of a standard one, accents included.
    # The counter at 0.4 is read before the free signals, so its own spelling is
    # the one reported when both name the same thing.
    assert duplicate_labels({"0.5": "Contador"}) == ["Contador"]
    assert duplicate_labels({"0.5": "SEGURANCA"}) == ["Segurança"]
    assert duplicate_labels({"0.5": "CONtador"}) == ["Contador"]
    # An integer label is a label like any other, in both directions.
    assert duplicate_labels({"6.0": "Valor 1"}) == ["Valor 1"]
    assert duplicate_labels({"0.5": "Valor 2"}) == ["Valor 2"]
    assert duplicate_labels({"2.0": "Peso", "6.0": "peso"}) == ["Peso"]
    # Naming an address the way it is already named is not a conflict.
    assert duplicate_labels({"2.0": "Valor 1"}) == []
    assert duplicate_labels({"0.5": "Portao de entrada", "0.6": "Portao de saida"}) == []
    assert duplicate_labels() == []


def test_clearing_a_signal_label_restores_the_default():
    area_id = app_storage.add_area("Test Plant", "Labels Area")
    machine_id = app_storage.add_machine(area_id, "Labels", "fake", 53, "UTC")
    app_storage.set_signal_labels(machine_id, {"0.4": "Portao"})
    app_storage.set_signal_labels(machine_id, {"0.4": ""})
    assert app_storage.signal_labels(machine_id) == {}
    app_storage.delete_machine(machine_id)


def test_signal_label_rejects_unknown_address_and_trims_whitespace():
    from app.models import SignalLabelInput

    with pytest.raises(ValidationError):
        SignalLabelInput(address="3.0", label="Fora do contrato")
    with pytest.raises(ValidationError):
        SignalLabelInput(address="0.4", label="Duas\nlinhas")
    assert SignalLabelInput(address="0.4", label="  Portao  ").label == "Portao"
    assert SignalLabelInput(address="0.4", label="").label == ""


def test_machine_delete_removes_signal_labels():
    area_id = app_storage.add_area("Test Plant", "Labels Area")
    machine_id = app_storage.add_machine(area_id, "Labels", "fake", 53, "UTC")
    app_storage.set_signal_labels(machine_id, {"0.4": "Portao"})
    app_storage.delete_machine(machine_id)
    assert app_storage.signal_labels(machine_id) == {}


def test_signal_label_endpoints_require_authentication():
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/api/config/machines/1/signals").status_code == 401
    assert client.put("/api/config/machines/1/signals", json=[]).status_code == 401


def test_signal_labels_round_trip_through_the_api():
    client = TestClient(app)
    area_id = app_storage.add_area("Test Plant", "API Labels Area")
    machine_id = app_storage.add_machine(area_id, "API Labels", "fake", 53, "UTC")
    token = "test-token-signal-labels"
    headers = {"Authorization": f"Bearer {token}"}
    start_session(token)
    try:
        response = client.get(f"/api/config/machines/{machine_id}/signals", headers=headers)
        assert response.status_code == 200
        before = response.json()
        assert [item["address"] for item in before] == [spec.address for spec in SIGNAL_LAYOUT]
        # Address and type come from the contract; only the label is user editable.
        assert all(item["label"] == spec.default_label for item, spec in zip(before, SIGNAL_LAYOUT))

        response = client.put(
            f"/api/config/machines/{machine_id}/signals",
            headers=headers,
            json=[{"address": "0.5", "label": "Portao de entrada"}],
        )
        assert response.status_code == 200
        after = {item["address"]: item for item in response.json()}
        assert after["0.5"]["label"] == "Portao de entrada"
        assert after["0.5"]["type"] == "BOOL"
        assert after["0.0"]["label"] == SIGNAL_LAYOUT[0].default_label
        assert app_storage.signal_labels(machine_id) == {"0.5": "Portao de entrada"}

        # The three DInts are renamed the same way, and keep their type.
        response = client.put(
            f"/api/config/machines/{machine_id}/signals",
            headers=headers,
            json=[
                {"address": "0.5", "label": "Portao de entrada"},
                {"address": "2.0", "label": "Peso da caixa"},
                {"address": "10.0", "label": "Temperatura"},
            ],
        )
        assert response.status_code == 200
        after = {item["address"]: item for item in response.json()}
        assert after["2.0"]["label"] == "Peso da caixa"
        assert after["2.0"]["type"] == "DINT"
        assert after["2.0"]["editable"] is True
        assert after["10.0"]["label"] == "Temperatura"
        assert app_storage.signal_labels(machine_id) == {
            "0.5": "Portao de entrada",
            "2.0": "Peso da caixa",
            "10.0": "Temperatura",
        }

        response = client.put(
            f"/api/config/machines/{machine_id}/signals",
            headers=headers,
            json=[{"address": "3.0", "label": "Fora do contrato"}],
        )
        assert response.status_code == 422
    finally:
        end_session(token)
        app_storage.delete_machine(machine_id)


def test_signal_labels_for_unknown_machine_are_rejected():
    client = TestClient(app)
    token = "test-token-signal-labels-missing"
    headers = {"Authorization": f"Bearer {token}"}
    start_session(token)
    try:
        missing_id = 999_999
        assert app_storage.get_machine(missing_id) is None
        assert client.get(f"/api/config/machines/{missing_id}/signals", headers=headers).status_code == 404
        response = client.put(
            f"/api/config/machines/{missing_id}/signals",
            headers=headers,
            json=[{"address": "0.3", "label": "Portao"}],
        )
        assert response.status_code == 404
    finally:
        end_session(token)


def _admin_client(token: str) -> TestClient:
    start_session(token)
    return TestClient(app)


def _temp_machine(name: str = "Signals") -> int:
    area_id = app_storage.add_area("Test Plant", "Signals Area")
    return app_storage.add_machine(area_id, name, "fake", 53, "UTC")


def test_locked_signals_cannot_be_renamed_through_the_api():
    from app.main import describe_machine_signals

    client = _admin_client("test-token-locked")
    machine_id = _temp_machine()
    headers = {"Authorization": "Bearer test-token-locked"}
    try:
        response = client.put(
            f"/api/config/machines/{machine_id}/signals",
            headers=headers,
            json=[{"address": "0.3", "label": "Seguranca do portao"}],
        )
        assert response.status_code == 422
        assert "0.3" in response.json()["detail"]
        assert app_storage.signal_labels(machine_id) == {}
        labels = {item.address: item.label for item in describe_machine_signals(machine_id)}
        assert labels["0.2"] == "Falha"
        assert labels["0.3"] == "Segurança"
    finally:
        end_session("test-token-locked")
        app_storage.delete_machine(machine_id)


def test_duplicate_signal_labels_are_rejected_through_the_api():
    client = _admin_client("test-token-duplicates")
    machine_id = _temp_machine()
    headers = {"Authorization": "Bearer test-token-duplicates"}
    try:
        response = client.put(
            f"/api/config/machines/{machine_id}/signals",
            headers=headers,
            json=[
                {"address": "0.5", "label": "Portao de entrada"},
                {"address": "0.6", "label": "portao de ENTRADA"},
            ],
        )
        assert response.status_code == 422
        assert "Portao de entrada" in response.json()["detail"]
        # Nothing is persisted when the payload is rejected.
        assert app_storage.signal_labels(machine_id) == {}

        response = client.put(
            f"/api/config/machines/{machine_id}/signals",
            headers=headers,
            json=[
                {"address": "0.5", "label": "Portao de entrada"},
                {"address": "0.6", "label": "Portao de saida"},
            ],
        )
        assert response.status_code == 200
        assert app_storage.signal_labels(machine_id) == {
            "0.5": "Portao de entrada",
            "0.6": "Portao de saida",
        }

        # A free signal may not adopt the name of a standard one either.
        response = client.put(
            f"/api/config/machines/{machine_id}/signals",
            headers=headers,
            json=[{"address": "0.5", "label": "Contador"}],
        )
        assert response.status_code == 422
        assert app_storage.signal_labels(machine_id) == {
            "0.5": "Portao de entrada",
            "0.6": "Portao de saida",
        }

        # An integer label counts for the same uniqueness rule. The payload
        # replaces the whole set, so both addresses go in the same request.
        response = client.put(
            f"/api/config/machines/{machine_id}/signals",
            headers=headers,
            json=[
                {"address": "0.5", "label": "Portao de entrada"},
                {"address": "2.0", "label": "portao de ENTRADA"},
            ],
        )
        assert response.status_code == 422

        # And the counter is part of the contract now, so it is refused too.
        response = client.put(
            f"/api/config/machines/{machine_id}/signals",
            headers=headers,
            json=[{"address": "0.4", "label": "Peças boas"}],
        )
        assert response.status_code == 422
        assert app_storage.signal_labels(machine_id) == {
            "0.5": "Portao de entrada",
            "0.6": "Portao de saida",
        }
    finally:
        end_session("test-token-duplicates")
        app_storage.delete_machine(machine_id)


def test_the_three_integers_can_be_renamed_through_the_api():
    client = _admin_client("test-token-integers")
    machine_id = _temp_machine()
    headers = {"Authorization": "Bearer test-token-integers"}
    try:
        response = client.put(
            f"/api/config/machines/{machine_id}/signals",
            headers=headers,
            json=[
                {"address": "2.0", "label": "Peso da caixa"},
                {"address": "6.0", "label": "Caixas rejeitadas"},
                {"address": "10.0", "label": "Temperatura"},
            ],
        )
        assert response.status_code == 200
        by_address = {item["address"]: item for item in response.json()}
        assert by_address["2.0"]["label"] == "Peso da caixa"
        assert by_address["6.0"]["label"] == "Caixas rejeitadas"
        assert by_address["10.0"]["label"] == "Temperatura"
        for address in ("2.0", "6.0", "10.0"):
            assert by_address[address]["type"] == "DINT"
            assert by_address[address]["editable"] is True
        assert app_storage.signal_labels(machine_id) == {
            "2.0": "Peso da caixa",
            "6.0": "Caixas rejeitadas",
            "10.0": "Temperatura",
        }
    finally:
        end_session("test-token-integers")
        app_storage.delete_machine(machine_id)


def test_the_counter_cannot_be_renamed_through_the_api():
    from app.main import describe_machine_signals

    client = _admin_client("test-token-counter")
    machine_id = _temp_machine()
    headers = {"Authorization": "Bearer test-token-counter"}
    try:
        response = client.put(
            f"/api/config/machines/{machine_id}/signals",
            headers=headers,
            json=[{"address": "0.4", "label": "Peças boas"}],
        )
        assert response.status_code == 422
        assert "0.4" in response.json()["detail"]
        assert app_storage.signal_labels(machine_id) == {}
        by_address = {item.address: item for item in describe_machine_signals(machine_id)}
        assert by_address["0.4"].label == "Contador"
        assert by_address["0.4"].editable is False
    finally:
        end_session("test-token-counter")
        app_storage.delete_machine(machine_id)


def test_dashboard_icons_are_vendored_and_used():
    client = TestClient(app)
    icons = client.get("/static/icons.js")
    assert icons.status_code == 200, "the vendored icon module is not served"
    assert "/static/icons.js" in client.get("/").text

    shipped = re.findall(r'^\s{2}"([a-z0-9-]+)":', icons.text, re.MULTILINE)
    defined = set(shipped)
    assert "circle-check" in defined and "lock" in defined
    # A name asked for twice would ship the same glyph twice, and the last one wins
    # silently, so the file is checked for repeats rather than only for coverage.
    assert len(shipped) == len(defined), "the vendored icon module repeats a name"

    asked = re.findall(
        r'^    "([a-z0-9-]+)",$',
        (Path(app_package.__file__).resolve().parents[1] / "scripts" / "build_icons.py").read_text(
            encoding="utf-8"
        ),
        re.MULTILINE,
    )
    assert len(asked) == len(set(asked)), "the icon generator asks for a name twice"
    # The generator is the single source of the list: regenerating changes nothing.
    assert defined == set(asked), f"unexpected: {sorted(defined ^ set(asked))}"

    used: set[str] = set()
    for source in (client.get("/static/app.js").text, client.get("/").text):
        used |= set(re.findall(r"icon\('([a-z0-9-]+)'", source))
        used |= set(re.findall(r'data-icon="([a-z0-9-]+)"', source))

    assert used, "the dashboard does not reference any icon"
    assert used <= defined, f"icons used but not generated: {sorted(used - defined)}"


def test_icon_module_loads_before_the_dashboard_script():
    page = TestClient(app).get("/")
    assert page.status_code == 200
    # Placeholders are hydrated by icons.js, which therefore has to load first.
    assert 'data-icon="layout-dashboard"' in page.text
    assert page.text.index("/static/icons.js") < page.text.index("/static/app.js")


def test_plant_slugs_are_url_safe():
    from app.slugs import plant_slugs, slugify

    assert slugify("RioVerde") == "RioVerde"
    assert slugify("Rio Verde") == "Rio-Verde"
    assert slugify("Anápolis") == "Anapolis"
    assert slugify("  Linha  2  ") == "Linha-2"
    assert slugify("!!!") == ""
    # Ajuda e Configuracoes are menu addresses, so a plant cannot take them.
    assert plant_slugs({"RioVerde", "Ajuda", "configuracoes", "!!!"}) == {"rioverde": "RioVerde"}


def test_machine_payload_carries_the_plant_slug():
    area_id = app_storage.add_area("Rio Verde", "Linha 1")
    machine_id = app_storage.add_machine(area_id, "M Slug", "fake", 53, "UTC")
    try:
        machines = {item["id"]: item for item in TestClient(app).get("/api/machines").json()}
        assert machines[machine_id]["plant_name"] == "Rio Verde"
        assert machines[machine_id]["plant_slug"] == "Rio-Verde"
    finally:
        app_storage.delete_machine(machine_id)


def test_every_plant_answers_on_its_own_address():
    client = TestClient(app)
    area_id = app_storage.add_area("RioVerde", "Linha 1")
    machine_id = app_storage.add_machine(area_id, "M Rota", "fake", 53, "UTC")
    try:
        response = client.get("/RioVerde")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        # Addresses are matched without regard to case.
        assert client.get("/rioverde").status_code == 200
        # Menu pages live in the same namespace.
        assert client.get("/Ajuda").status_code == 200
        assert client.get("/Configuracoes").status_code == 200
        assert client.get("/Anapolis").status_code == 404
        assert client.get("/qualquer-coisa").status_code == 404
        # The single segment route must not shadow the API or the assets.
        assert client.get("/api/machines").status_code == 200
        assert client.get("/static/app.js").status_code == 200
    finally:
        app_storage.delete_machine(machine_id)


def test_help_page_is_in_the_menu_and_documents_the_signals():
    page = TestClient(app).get("/")
    assert page.status_code == 200
    assert 'id="help-page"' in page.text
    assert 'data-route="/Ajuda"' in page.text
    assert 'data-route="/Configuracoes"' in page.text
    assert "Renomear os sinais" in page.text
    assert "Cada nome precisa ser" in page.text


def test_menu_button_uses_the_burger_icon():
    page = TestClient(app).get("/").text
    assert 'aria-controls="main-menu"><svg class="icon" data-icon="menu">' in page
    # Three loose bars were flex items of the button, so they lined up sideways
    # and the control read as a row of dashes.
    assert "<span></span><span></span><span></span>" not in page


def test_footer_social_links_show_only_the_icon():
    page = TestClient(app).get("/").text
    assert "https://github.com/Autoric-Automation-Systems/sp-clp" in page
    assert "https://www.instagram.com/autoricbr/" in page
    # The address lives in the link itself, not as visible text next to the icon.
    assert "<span>Autoric-Automation-Systems/sp-clp</span>" not in page
    assert "<span>@autoricbr</span>" not in page
    assert 'title="GitHub: Autoric-Automation-Systems/sp-clp"' in page
    assert 'title="Instagram: @autoricbr"' in page


def test_bit_address_is_only_shown_in_the_signal_list():
    script = TestClient(app).get("/static/app.js").text
    # The status blocks and the counter header no longer repeat the bit address.
    assert 'em class="address"' not in script
    # The expandable signal list keeps it, because that is where it is useful.
    assert 'class="signal-address"' in script


# A real 1x1 PNG, so the signature check can be exercised end to end.
TINY_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_branding_defaults_to_the_shipped_identity():
    response = TestClient(app).get("/api/branding")
    assert response.status_code == 200
    assert response.json() == {"company_name": "SP-CLP", "logo_url": None}
    assert TestClient(app).get("/api/branding/logo").status_code == 404


def test_branding_endpoints_require_authentication():
    client = TestClient(app, raise_server_exceptions=False)
    assert client.put("/api/config/branding", json={"company_name": "X"}).status_code == 401
    assert client.post(
        "/api/config/branding/logo", json={"filename": "l.png", "content": TINY_PNG}
    ).status_code == 401
    assert client.delete("/api/config/branding/logo").status_code == 401


def test_company_name_and_logo_round_trip():
    from app.branding import COMPANY_NAME_KEY, LOGO_EXT_KEY

    client = _admin_client("test-token-branding")
    headers = {"Authorization": "Bearer test-token-branding"}
    try:
        response = client.put(
            "/api/config/branding", headers=headers, json={"company_name": "Autoric"}
        )
        assert response.status_code == 200
        assert response.json()["company_name"] == "Autoric"
        assert client.get("/api/branding").json()["company_name"] == "Autoric"

        response = client.post(
            "/api/config/branding/logo",
            headers=headers,
            json={"filename": "marca.png", "content": TINY_PNG},
        )
        assert response.status_code == 200
        assert response.json()["logo_url"] == "/api/branding/logo"

        served = client.get("/api/branding/logo")
        assert served.status_code == 200
        assert served.headers["content-type"] == "image/png"
        assert served.content.startswith(b"\x89PNG")

        assert client.delete("/api/config/branding/logo", headers=headers).status_code == 200
        assert client.get("/api/branding").json()["logo_url"] is None
        assert client.get("/api/branding/logo").status_code == 404
    finally:
        end_session("test-token-branding")
        app_storage.set_setting(COMPANY_NAME_KEY, "SP-CLP")
        # The logo file lives next to the test database; clear both the pointer
        # and the file so later tests see the shipped default again.
        app_storage.set_setting(LOGO_EXT_KEY, "")
        remove_logos(app_storage.path.parent)


def test_branding_logo_must_be_an_image():
    import base64

    client = _admin_client("test-token-branding-bad")
    headers = {"Authorization": "Bearer test-token-branding-bad"}
    try:
        not_an_image = base64.b64encode(b"isto nao e uma imagem").decode()
        response = client.post(
            "/api/config/branding/logo",
            headers=headers,
            json={"filename": "marca.png", "content": not_an_image},
        )
        assert response.status_code == 422
        assert "Formato não reconhecido" in response.json()["detail"]

        # An SVG is a document that can run script in this origin, so it is refused
        # even though it is a valid image.
        svg = base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>').decode()
        response = client.post(
            "/api/config/branding/logo",
            headers=headers,
            json={"filename": "marca.svg", "content": svg},
        )
        assert response.status_code == 422

        oversized = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 1_100_000).decode()
        response = client.post(
            "/api/config/branding/logo",
            headers=headers,
            json={"filename": "grande.png", "content": oversized},
        )
        assert response.status_code == 422
        assert "limite" in response.json()["detail"]
    finally:
        end_session("test-token-branding-bad")


def test_dashboard_header_carries_the_branding():
    page = TestClient(app).get("/").text
    assert 'id="brand-name"' in page
    assert 'id="brand-logo"' in page
    assert 'id="branding-form"' in page
    assert 'id="company-logo"' in page
    script = TestClient(app).get("/static/app.js").text
    assert "loadBranding()" in script
    assert "applyBranding" in script


def test_hourly_chart_window_is_wired():
    page = TestClient(app).get("/").text
    for element in ('id="chart-dialog"', 'id="chart-body"', 'id="chart-prev"', 'id="chart-next"', 'id="chart-day"'):
        assert element in page, f"the chart window is missing {element}"
    script = TestClient(app).get("/static/app.js").text
    for hook in ("openChart", "chartMarkup", "stepChart", "shiftDay"):
        assert hook in script, f"app.js does not wire {hook}"


def test_access_urls_prefer_the_machine_name():
    from app.access import DEFAULT_HOST, access_urls, bind_host

    assert bind_host() == DEFAULT_HOST == "0.0.0.0"
    urls = access_urls(port=8000, host="0.0.0.0")
    assert urls[0].startswith("http://") and urls[0].endswith(":8000")
    assert "http://localhost:8000" in urls
    # A localhost-only bind must not advertise addresses that would not answer.
    assert access_urls(port=8000, host="127.0.0.1") == ["http://localhost:8000"]


def test_local_addresses_lists_every_card_and_drops_what_nobody_can_use(monkeypatch):
    """A Windows box with a plant card and a VirtualBox NAT card is the reason."""
    from app import access

    def fake_getaddrinfo(*_args, **_kwargs):
        return [
            (2, 1, 6, "", ("127.0.0.1", 0)),
            # What a dead cable or a missing DHCP server leaves behind.
            (2, 1, 6, "", ("169.254.10.20", 0)),
            (2, 1, 6, "", ("10.0.3.15", 0)),
            (2, 1, 6, "", ("192.168.0.250", 0)),
            (2, 1, 6, "", ("192.168.0.250", 0)),
        ]

    monkeypatch.setattr(access.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(access, "lan_address", lambda: "10.0.3.15")

    # The default route one comes first, and the duplicates are gone.
    assert access.local_addresses() == ["10.0.3.15", "192.168.0.250"]

    urls = access.access_urls(port=8000, host="0.0.0.0")
    # The plant address has to be on the list even though the default route
    # points at the NAT card, which is the address nobody else can reach.
    assert "http://192.168.0.250:8000" in urls
    assert "http://10.0.3.15:8000" in urls
    assert "http://localhost:8000" in urls


def test_local_addresses_falls_back_to_the_probe_when_the_name_lookup_fails(monkeypatch):
    from app import access

    def refusing(*_args, **_kwargs):
        raise OSError("sem DNS nesta rede")

    monkeypatch.setattr(access.socket, "getaddrinfo", refusing)
    monkeypatch.setattr(access, "lan_address", lambda: "192.168.0.250")
    assert access.local_addresses() == ["192.168.0.250"]

    # And with no address at all the list stays empty instead of raising.
    monkeypatch.setattr(access, "lan_address", lambda: None)
    assert access.local_addresses() == []


def test_access_endpoint_is_public_and_lists_the_addresses():
    response = TestClient(app).get("/api/access")
    assert response.status_code == 200
    body = response.json()
    assert body["port"] == 8000
    assert body["hostname"]
    assert any(url.endswith(":8000") for url in body["urls"])
    assert f"http://localhost:{body['port']}" in body["urls"]


def test_help_page_explains_the_network():
    page = TestClient(app).get("/").text
    assert "Infraestrutura de rede" in page
    assert "Porta 102" in page
    assert 'id="access-addresses"' in page
    # The customer asks about the phone next, so the page answers it where the
    # addresses are: the IP works, the machine name usually does not.
    assert "No celular ou tablet" in page
    assert "mesma rede" in page
    # The list is filled from /api/access, so app.js has to wire it.
    assert "loadAccess" in TestClient(app).get("/static/app.js").text


def test_bind_port_ignores_a_broken_value():
    import os

    from app.access import DEFAULT_PORT, bind_port

    previous = os.environ.get("SP_CLP_PORT")
    try:
        os.environ["SP_CLP_PORT"] = "nao-e-porta"
        assert bind_port() == DEFAULT_PORT
        os.environ["SP_CLP_PORT"] = "9001"
        assert bind_port() == 9001
    finally:
        if previous is None:
            os.environ.pop("SP_CLP_PORT", None)
        else:
            os.environ["SP_CLP_PORT"] = previous


def test_access_endpoint_reports_the_alias():
    body = TestClient(app).get("/api/access").json()
    assert body["alias"] == "sp-clp"
    assert body["alias_url"].startswith("http://sp-clp")
    assert isinstance(body["alias_ready"], bool)
    # The alias is only listed once it answers, so the help page never sends the
    # operator to an address that would fail.
    if body["alias_ready"]:
        assert body["urls"][0] == body["alias_url"]
    else:
        assert body["alias_url"] not in body["urls"]


def test_alias_is_advertised_only_when_the_name_resolves():
    from app import access

    assert access.ALIAS == "sp-clp"
    assert access.website("sp-clp", 8000) == "http://sp-clp:8000"
    # Port 80 is what removes the suffix, leaving the bare branded address.
    assert access.website("sp-clp", 80) == "http://sp-clp"

    original = access.machine_name
    try:
        access.machine_name = lambda: "SP-CLP"
        assert access.alias_ready() is True
        assert access.access_urls(port=8000, host="0.0.0.0")[0] == "http://sp-clp:8000"

        access.machine_name = lambda: "PC-PLANTA"
        assert access.alias_ready() is False
        assert "http://sp-clp:8000" not in access.access_urls(port=8000, host="0.0.0.0")
    finally:
        access.machine_name = original


def test_help_page_offers_the_brand_alias():
    page = TestClient(app).get("/").text
    assert 'id="access-alias"' in page
    assert "Renomear este computador" in page
    assert "SP_CLP_PORT" in page
    assert "loadAccess" in TestClient(app).get("/static/app.js").text


def test_footer_keeps_the_product_mark():
    page = TestClient(app).get("/").text
    # The client logo takes the header, so the product mark lives in the footer.
    assert 'class="footer-brand"' in page
    assert "android-chrome-192x192.png" in page


def test_footer_site_link_keeps_the_address_in_the_link():
    page = TestClient(app).get("/").text
    assert 'class="authoric-link"' in page
    assert 'href="https://www.autoric.com.br"' in page
    assert 'title="Autoric: https://www.autoric.com.br"' in page
    # The address lives in the link, instead of being printed beside the mark.
    assert ">www.autoric.com.br<" not in page
    assert page.count('href="https://www.autoric.com.br"') == 1


def test_fault_bit_is_set_while_the_machine_is_in_fault():
    script = TestClient(app).get("/static/app.js").text
    # FAULT is the opposite of the other fixed bits: 0 is normal, 1 is in fault.
    assert "fault: ['Normal', 'Em falha']" in script
    assert "new Set(['fault'])" in script
    assert "isHealthy" in script
    page = TestClient(app).get("/").text
    assert "Em falha" in page and "quando o bit é 1" in page


def test_status_words_follow_what_each_bit_means():
    script = TestClient(app).get("/static/app.js").text
    assert "stateText" in script
    # A set bit is the healthy state for FAULT and SAFETY, so the wording cannot
    # be the generic "Ativo/Parado" everywhere.
    for word in ("'Manual'", "'Automático'", "'Produzindo'", "'Em falha'", "'Pendente'"):
        assert word in script, f"the status vocabulary lost {word}"
    # A free signal shows the bit itself instead of a word that has to be
    # interpreted, so 0 and 1 are never ambiguous on the card.
    assert "'1 LIGADO'" in script and "'0 DESLIGADO'" in script
    page = TestClient(app).get("/").text
    assert "Pendente" in page and "Produzindo" in page
    assert "1 LIGADO" in page and "0 DESLIGADO" in page


def test_header_logo_is_rounded_and_lifts_on_hover():
    css = TestClient(app).get("/static/styles.css").text
    assert ".brand:hover .brand-icon" in css
    assert "transform:scale(" in css
    base = re.search(r"\.brand-icon\{[^}]*\}", css)
    assert base is not None
    assert "border-radius" in base.group(0)


def test_logo_crop_tool_is_wired():
    page = TestClient(app).get("/").text
    assert 'id="logo-dialog"' in page
    assert 'id="crop-canvas"' in page
    assert 'id="crop-zoom"' in page
    script = TestClient(app).get("/static/app.js").text
    for hook in ("openCropTool", "setCropZoom", "toDataURL('image/png')"):
        assert hook in script, f"app.js does not wire {hook}"


def _area_ids(plant_name: str) -> list[int]:
    return [row["id"] for row in app_storage.list_areas() if row["plant_name"] == plant_name]


def test_area_endpoints_require_authentication():
    client = TestClient(app, raise_server_exceptions=False)
    assert client.put("/api/config/areas/1", json={"plant_name": "P", "name": "A"}).status_code == 401
    assert client.delete("/api/config/areas/1").status_code == 401
    assert client.put("/api/config/plants/P", json={"plant_name": "P2"}).status_code == 401


def test_an_area_can_be_edited():
    area_id = app_storage.add_area("Planta Editavel", "Area Antiga")
    client = _admin_client("test-token-area-edit")
    headers = {"Authorization": "Bearer test-token-area-edit"}
    try:
        response = client.put(
            f"/api/config/areas/{area_id}",
            headers=headers,
            json={"plant_name": "Planta Editavel", "name": "Area Nova"},
        )
        assert response.status_code == 200
        area = app_storage.get_area(area_id)
        assert area["name"] == "Area Nova"
        assert area["plant_name"] == "Planta Editavel"
    finally:
        app_storage.delete_area(area_id)
        end_session("test-token-area-edit")


def test_editing_an_area_cannot_rename_the_plant():
    area_id = app_storage.add_area("Planta Fixa", "Area Fixa")
    client = _admin_client("test-token-area-plant")
    headers = {"Authorization": "Bearer test-token-area-plant"}
    try:
        response = client.put(
            f"/api/config/areas/{area_id}",
            headers=headers,
            json={"plant_name": "Outra Planta", "name": "Area Fixa"},
        )
        assert response.status_code == 422
        # One area renamed alone would leave two plants on the same address.
        assert app_storage.get_area(area_id)["plant_name"] == "Planta Fixa"
    finally:
        app_storage.delete_area(area_id)
        end_session("test-token-area-plant")


def test_area_with_machines_cannot_be_deleted():
    area_id = app_storage.add_area("Planta Exclusao", "Area Cheia")
    machine_id = app_storage.add_machine(area_id, "M Area", "fake", 53, "UTC")
    client = _admin_client("test-token-area-delete")
    headers = {"Authorization": "Bearer test-token-area-delete"}
    try:
        response = client.delete(f"/api/config/areas/{area_id}", headers=headers)
        assert response.status_code == 409
        assert "1 máquinas" in response.json()["detail"]
        assert app_storage.get_area(area_id) is not None

        app_storage.delete_machine(machine_id)
        assert client.delete(f"/api/config/areas/{area_id}", headers=headers).status_code == 200
        assert app_storage.get_area(area_id) is None
        # The plant disappears with its last area.
        assert "Planta Exclusao" not in app_storage.plant_names()
    finally:
        app_storage.delete_machine(machine_id)
        app_storage.delete_area(area_id)
        end_session("test-token-area-delete")


def test_renaming_a_plant_moves_every_area_and_its_address():
    first = app_storage.add_area("Planta Renomear", "Area Um")
    second = app_storage.add_area("Planta Renomear", "Area Dois")
    client = _admin_client("test-token-plant-rename")
    headers = {"Authorization": "Bearer test-token-plant-rename"}
    try:
        assert client.get("/Planta-Renomear").status_code == 200
        response = client.put(
            "/api/config/plants/Planta-Renomear",
            headers=headers,
            json={"plant_name": "Planta Nova"},
        )
        assert response.status_code == 200
        assert response.json()["areas"] == 2
        assert app_storage.get_area(first)["plant_name"] == "Planta Nova"
        assert app_storage.get_area(second)["plant_name"] == "Planta Nova"
        # The old address stops answering and the new one takes over.
        assert client.get("/Planta-Renomear").status_code == 404
        assert client.get("/Planta-Nova").status_code == 200
        assert client.get("/plaNTA-nova").status_code == 200
    finally:
        app_storage.delete_area(first)
        app_storage.delete_area(second)
        end_session("test-token-plant-rename")


def test_plant_names_must_own_a_usable_address():
    client = _admin_client("test-token-plant-clash")
    headers = {"Authorization": "Bearer test-token-plant-clash"}
    areas: list[int] = []
    try:
        # "colisao base" and "colisao-base" reduce to the same address, so the
        # second one *is* the same plant: the area joins it. A second plant
        # fighting for the address is what must never be created.
        areas.append(app_storage.add_area("colisao base", "Area Um"))
        response = client.post(
            "/api/config/areas",
            headers=headers,
            json={"plant_name": "colisao-base", "name": "Area Dois"},
        )
        assert response.status_code == 201
        areas.append(response.json()["id"])
        assert sorted(
            row["name"] for row in app_storage.list_areas() if row["plant_name"] == "colisao base"
        ) == ["Area Dois", "Area Um"]

        # A name that cannot become an address at all.
        response = client.post(
            "/api/config/areas",
            headers=headers,
            json={"plant_name": "!!!", "name": "Area Tres"},
        )
        assert response.status_code == 422
        assert "letra ou número" in response.json()["detail"]

        # Menu pages are not available as plant addresses.
        response = client.post(
            "/api/config/areas",
            headers=headers,
            json={"plant_name": "Ajuda", "name": "Area Quatro"},
        )
        assert response.status_code == 422
        assert "reservado" in response.json()["detail"]

        # Renaming onto an address that is already taken is refused too.
        areas.append(app_storage.add_area("colisao alvo", "Area Cinco"))
        response = client.put(
            "/api/config/plants/colisao-alvo",
            headers=headers,
            json={"plant_name": "Colisao Base"},
        )
        assert response.status_code == 422
    finally:
        for area_id in areas:
            app_storage.delete_area(area_id)
        end_session("test-token-plant-clash")


def test_an_area_finds_the_plant_the_address_already_names():
    """The plant is identified by its address, not by the spelling that was typed.

    The customer adds the second area to a plant they can see in the list, and the
    name arrives with whatever capitals, spaces or accents they typed. Refusing it
    as "this address belongs to Linha 1" is a dead end: that address already names
    the plant they meant.
    """
    from app.slugs import slugify

    client = _admin_client("test-token-area-fold")
    headers = {"Authorization": "Bearer test-token-area-fold"}
    app_storage.add_area("Linha 1", "Envase")
    try:
        for typed in ("Linha 1", "linha 1", "LINHA 1", "Linha-1", "Linha 1 ", "Linha  1"):
            response = client.post(
                "/api/config/areas",
                headers=headers,
                json={"plant_name": typed, "name": f"Area {typed.strip()}"},
            )
            assert response.status_code == 201, (typed, response.json())
        # The accents fold too, because the address is what identifies a plant.
        app_storage.add_area("Produção", "Linha A")
        response = client.post(
            "/api/config/areas",
            headers=headers,
            json={"plant_name": "producao", "name": "Linha B"},
        )
        assert response.status_code == 201, response.json()

        for name, address in (("Linha 1", "linha-1"), ("Produção", "producao")):
            matching = [
                candidate
                for candidate in app_storage.plant_names()
                if slugify(candidate).casefold() == address
            ]
            assert matching == [name], f"{address} answers for more than one plant"
    finally:
        for area in app_storage.list_areas():
            if area["plant_name"] in {"Linha 1", "Produção"}:
                app_storage.delete_area(area["id"])
        end_session("test-token-area-fold")


def test_the_area_form_offers_the_plants_that_exist():
    """The plant is chosen from the list, because a plant exists once it has an area."""
    page = TestClient(app).get("/").text
    assert 'id="plant-choice" required' in page
    assert 'id="plant-new-field"' in page
    assert 'id="plant-new"' in page
    # The free text field is gone: a plant that exists is chosen, not typed.
    assert 'id="plant-name"' not in page

    script = TestClient(app).get("/static/app.js").text
    assert "Nova planta" in script
    assert "const NEW_PLANT = '__new__';" in script
    assert "#plant-choice" in script
    assert "#plant-new-field" in script
    assert "function syncNewPlantField" in script
    # The typed name is only read when the option for a new plant is chosen.
    assert "choice === NEW_PLANT" in script
    # Editing an area keeps its plant, which is why the choice is disabled there.
    assert "document.querySelector('#plant-choice').disabled" in script


def test_the_hidden_attribute_actually_hides():
    """A rule that sets display beats the browser's own [hidden] rule.

    The field for a new plant was painted while the attribute was set, and so were
    the cancel buttons and the probe result. One rule covers every element instead
    of naming them one by one.
    """
    css = TestClient(app).get("/static/styles.css").text
    assert "[hidden]{display:none!important}" in css


def test_renaming_a_plant_to_the_same_address_is_still_allowed():
    area_id = app_storage.add_area("Rio Verde", "Area Rio")
    client = _admin_client("test-token-plant-case")
    headers = {"Authorization": "Bearer test-token-plant-case"}
    try:
        # Only the spelling changes, so the address is unchanged and valid.
        response = client.put(
            "/api/config/plants/Rio-Verde",
            headers=headers,
            json={"plant_name": "Rio verde"},
        )
        assert response.status_code == 200
        assert app_storage.get_area(area_id)["plant_name"] == "Rio verde"
        assert client.get("/Rio-Verde").status_code == 200
    finally:
        app_storage.delete_area(area_id)
        end_session("test-token-plant-case")


def test_layout_matches_the_customer_db():
    """Guards the offsets read from the block in the field.

    16 BOOLs in 0.0-1.7 with Counter at 0.4, the 'SPCLP' array at 14.0 and Count at
    20.0, with byte 19 as the alignment gap. A read that misses one of these
    silently returns other data, which is how a count of 1250 once showed as
    1397769036 on the dashboard.
    """
    from app.plc import (  # noqa: PLC0415
        COUNT_OFFSET,
        COUNT_SIZE,
        SIGNATURE,
        SIGNATURE_OFFSET,
        SIGNATURE_TEXT,
    )

    assert SIGNATURE_OFFSET == 14
    assert SIGNATURE_TEXT == "SPCLP"
    assert SIGNATURE == b"SPCLP"
    assert COUNT_OFFSET == 20
    assert COUNT_SIZE == 4
    # 24 bytes: 0..23, the last byte of Count inclusive.
    assert READ_SIZE == 24

    # The whole BOOL word must be inside bytes 0-1, the three DInts are inside the
    # read as well, and the counter must be the last thing the read covers.
    assert {spec.address.split(".")[0] for spec in BOOL_LAYOUT} == {"0", "1"}
    assert [signal_offset(spec.address) for spec in DINT_LAYOUT] == [2, 6, 10]
    assert max(signal_offset(spec.address) for spec in DINT_LAYOUT) + DINT_SIZE <= READ_SIZE
    assert SIGNATURE_OFFSET + len(SIGNATURE) == 19 < COUNT_OFFSET
    assert COUNT_OFFSET + COUNT_SIZE == READ_SIZE


def _scan(first: int = 1, last: int = 10, **kwargs):
    from app.plc import scan_databases

    return scan_databases(FakePLCProbeClient(**kwargs), "192.168.0.10", first=first, last=last)


def test_scan_lists_the_databases_that_carry_the_signature():
    result = _scan(databases=(1, 7))
    assert result.status == PROBE_READY
    assert result.databases == (1, 7)
    # Every number in the range is looked at, and only the matches are offered.
    assert result.scanned == 10
    assert result.answered == 2
    assert "1, 7" in result.message


def test_scan_reaches_the_high_db_numbers():
    result = _scan(first=1, last=1000, databases=(137,))
    assert result.status == PROBE_READY
    assert result.databases == (137,)
    assert result.scanned == 1000


def test_scan_summarises_a_long_list():
    result = _scan(first=1, last=100, databases=tuple(range(1, 25)))
    assert result.databases == tuple(range(1, 25))
    assert "e mais" in result.message


def test_scan_says_when_no_database_answers():
    result = _scan(databases=())
    assert result.status == PROBE_UNSIGNED
    assert result.databases == ()
    assert result.answered == 0
    assert "1" in result.message and "10" in result.message


def test_scan_shows_what_a_foreign_database_holds():
    """A range full of other DBs must say what it found there, not just 'nothing'."""
    result = _scan(databases=(3,), signature=b"OUTRO")
    assert result.status == PROBE_UNSIGNED
    assert result.answered == 1
    assert result.detail == "DB 3: OUTRO"
    assert "aceitaram a leitura" in result.message


def test_scan_keeps_the_matches_when_the_link_dies():
    result = _scan(first=1, last=50, databases=(1, 2, 40), drop_at=30)
    assert result.status == PROBE_UNREACHABLE
    assert result.truncated is True
    # The numbers confirmed before the drop are still offered.
    assert result.databases == (1, 2)
    assert result.scanned == 30
    assert "30" in result.message


def test_scan_reports_an_address_that_never_answers():
    result = _scan(reachable=False)
    assert result.status == PROBE_UNREACHABLE
    assert result.databases == ()
    assert result.scanned == 0
    assert "192.168.0.10" in result.message
    # Rack and slot belong in the message: a wrong value also breaks the connect.
    assert "rack 0 / slot 1" in result.message


def test_scan_stops_at_the_deadline():
    """A slow PLC must not hold the request thread until the range ends."""
    from app.plc import scan_databases

    ticks = {"now": 0.0}

    def clock() -> float:
        ticks["now"] += 5.0
        return ticks["now"]

    result = scan_databases(
        FakePLCProbeClient(), "192.168.0.10", first=1, last=1000, deadline_s=12.0, clock=clock
    )
    assert result.truncated is True
    assert result.scanned == 2
    assert result.databases == (1,)
    assert "interrompida" in result.message


def test_scan_never_leaves_the_connection_open():
    """Even a sweep that finds nothing must close, or a wrong address leaks a socket."""
    class Tracked(FakePLCProbeClient):
        def __init__(self):
            super().__init__(databases=())
            self.closed = False

        def close(self):
            self.closed = True

    from app.plc import scan_databases

    prober = Tracked()
    assert scan_databases(prober, "192.168.0.10", first=1, last=3).status == PROBE_UNSIGNED
    assert prober.closed is True


def test_scan_survives_a_close_that_raises():
    from app.plc import scan_databases

    class Stubborn(FakePLCProbeClient):
        def close(self):
            raise OSError("socket already gone")

    result = scan_databases(Stubborn(), "192.168.0.10", first=1, last=3)
    assert result.status == PROBE_READY


def test_readable_signature_reads_only_text_out_of_a_mixed_block():
    """A wrong offset usually points at numbers, which are not a signature."""
    from app.plc import SIGNATURE, readable_signature

    assert readable_signature(b"SPX\x00\x00\x00\x00\x00") == "SPX"
    assert readable_signature(b"\x00\x01\x02\x03") == ""
    assert readable_signature(b"P\x00\x04\xe2\x00\x00") == "P"
    assert readable_signature(SIGNATURE) == "SPCLP"


def test_scan_endpoint_requires_authentication():
    response = TestClient(app).post("/api/config/plc/scan", json={"ip": "192.168.0.10"})
    assert response.status_code == 401


def test_scan_endpoint_lists_the_simulated_database():
    client = _admin_client("test-token-scan")
    try:
        response = client.post(
            "/api/config/plc/scan",
            headers={"Authorization": "Bearer test-token-scan"},
            json={"ip": "fake"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == PROBE_READY
        assert body["databases"] == [1]
        assert body["truncated"] is False
    finally:
        end_session("test-token-scan")


def test_scan_endpoint_honours_a_narrow_range():
    client = _admin_client("test-token-scan-range")
    try:
        response = client.post(
            "/api/config/plc/scan",
            headers={"Authorization": "Bearer test-token-scan-range"},
            json={"ip": "fake", "first": 5, "last": 5},
        )
        assert response.status_code == 200
        assert response.json()["databases"] == []
        assert response.json()["scanned"] == 1
    finally:
        end_session("test-token-scan-range")


def test_scan_endpoint_rejects_a_blank_address():
    client = _admin_client("test-token-scan-blank")
    try:
        response = client.post(
            "/api/config/plc/scan",
            headers={"Authorization": "Bearer test-token-scan-blank"},
            json={"ip": "   "},
        )
        assert response.status_code == 422
    finally:
        end_session("test-token-scan-blank")


def test_scan_endpoint_rejects_an_unusable_range():
    from app.plc import MAX_DB_RANGE

    client = _admin_client("test-token-scan-bad-range")
    headers = {"Authorization": "Bearer test-token-scan-bad-range"}
    try:
        inverted = client.post(
            "/api/config/plc/scan", headers=headers, json={"ip": "fake", "first": 100, "last": 10}
        )
        assert inverted.status_code == 422

        too_wide = client.post(
            "/api/config/plc/scan", headers=headers, json={"ip": "fake", "first": 1, "last": MAX_DB_RANGE + 1}
        )
        assert too_wide.status_code == 422
    finally:
        end_session("test-token-scan-bad-range")


def test_the_counter_bit_has_a_status_block_of_its_own():
    """The counter is a diagnostic: the logic counts on the rising edge, so the
    block answers whether counting is happening, not how much was counted."""
    script = TestClient(app).get("/static/app.js").text
    assert "counter: ['Sem contagem', 'Contando']" in script
    assert "firstOfKind(status, 'counter')" in script
    # It has a block, so the signal list must not pick it up again.
    listed = script.split("function listedSignals(")[1].split("\nfunction ")[0]
    assert "'custom'" in listed and "'integer'" in listed
    assert "'counter'" not in listed
    page = TestClient(app).get("/").text
    assert "Contando" in page and "borda de subida" in page


def test_machine_form_offers_the_sweep():
    page = TestClient(app).get("/").text
    assert 'id="machine-probe"' in page
    assert 'id="machine-probe-message"' in page
    assert 'id="machine-scan-databases"' in page
    assert "SPCLP" in page
    # The button is wired in app.js, and the sweep is read only by design.
    script = TestClient(app).get("/static/app.js").text
    assert "scanMachine" in script
    assert "renderScanDatabases" in script
    assert "'/api/config/plc/scan'" in script
    assert "db_write" not in script


def test_library_lists_nothing_when_the_build_carries_none(tmp_path, monkeypatch):
    from app import libraries

    monkeypatch.setattr(libraries, "BUNDLED_DIR", tmp_path)
    assert TestClient(app).get("/api/library").json() == {"files": []}
    # The help page must never offer a download that would fail.
    assert TestClient(app).get("/api/library/s7/SP-CLP.zal").status_code == 404


def test_library_lists_every_file_of_every_family(tmp_path, monkeypatch):
    from app import libraries

    (tmp_path / "s7").mkdir()
    (tmp_path / "s7" / "SP-CLP.zal17").write_bytes(b"PK\x03\x04versao-17")
    (tmp_path / "s7" / "SP-CLP.zal16").write_bytes(b"PK\x03\x04versao-16")
    (tmp_path / "s7" / "SP-CLP.zal").write_bytes(b"PK\x03\x04antiga")
    (tmp_path / "mitsubishi").mkdir()
    (tmp_path / "mitsubishi" / "SP-CLP.gxw").write_bytes(b"outro-clp")
    monkeypatch.setattr(libraries, "BUNDLED_DIR", tmp_path)

    body = TestClient(app).get("/api/library").json()
    # Several versions of the same block live side by side, as they will in the
    # folder once the customer keeps more than one TIA release.
    assert [(item["family"], item["filename"]) for item in body["files"]] == [
        ("mitsubishi", "SP-CLP.gxw"),
        ("s7", "SP-CLP.zal"),
        ("s7", "SP-CLP.zal16"),
        ("s7", "SP-CLP.zal17"),
    ]
    # Known families get a name a customer can read; the rest show their folder.
    labels = {item["family"]: item["label"] for item in body["files"]}
    assert labels == {"s7": "Siemens S7", "mitsubishi": "mitsubishi"}
    sizes = {item["filename"]: item["size_bytes"] for item in body["files"]}
    assert sizes["SP-CLP.zal17"] == len(b"PK\x03\x04versao-17")


def test_library_offers_the_scl_the_customer_imports(tmp_path, monkeypatch):
    """The block is an .scl source; a *.zal rule would have missed it."""
    from app import libraries

    (tmp_path / "s7").mkdir()
    (tmp_path / "s7" / "FB_SP-CLP.scl").write_text("FUNCTION_BLOCK \"FB_SP-CLP\"\n")
    monkeypatch.setattr(libraries, "BUNDLED_DIR", tmp_path)

    item = TestClient(app).get("/api/library").json()["files"][0]
    assert item["filename"] == "FB_SP-CLP.scl"
    assert item["url"] == "/api/library/s7/FB_SP-CLP.scl"

    response = TestClient(app).get(item["url"])
    assert response.status_code == 200
    assert response.text == "FUNCTION_BLOCK \"FB_SP-CLP\"\n"
    disposition = response.headers["content-disposition"]
    assert "attachment" in disposition
    assert "FB_SP-CLP.scl" in disposition


def test_library_ignores_dotfiles_and_stray_folders(tmp_path, monkeypatch):
    """A .gitkeep keeps an empty folder in git; it is not a library."""
    from app import libraries

    (tmp_path / "s7").mkdir()
    (tmp_path / "s7" / ".gitkeep").write_text("")
    (tmp_path / "s7" / "rascunho").mkdir()
    monkeypatch.setattr(libraries, "BUNDLED_DIR", tmp_path)

    assert libraries.available_files() == []
    assert TestClient(app).get("/api/library").json() == {"files": []}


def test_library_refuses_a_path_outside_the_folders(tmp_path, monkeypatch):
    from app import libraries

    (tmp_path / "s7").mkdir()
    (tmp_path / "s7" / "SP-CLP.zal17").write_bytes(b"PK\x03\x04")
    (tmp_path / "segredo.txt").write_text("nao e para sair")
    monkeypatch.setattr(libraries, "BUNDLED_DIR", tmp_path)

    # Only the scanned folders are reachable, and only by exact name.
    for url in (
        "/api/library/s7/..%2F..%2Fsegredo.txt",
        "/api/library/../segredo.txt",
        "/api/library//segredo.txt",
        "/api/library/s7/SP-CLP.zal",
        "/api/library/nao-existe/SP-CLP.zal17",
    ):
        assert TestClient(app).get(url).status_code == 404, url


def test_status_blocks_colour_the_icon_without_trusting_colour_alone():
    css = TestClient(app).get("/static/styles.css").text
    for hue in ("on-blue", "on-green", "on-red", "on-cyan"):
        assert f".state.{hue}{{" in css, hue
    # A card with no reading must never look like a stopped machine, so the ring
    # changes shape and not only shade.
    assert ".state.unknown .state-icon{background:transparent;border:1.5px dashed" in css

    script = TestClient(app).get("/static/app.js").text
    assert "STATE_HUES" in script
    assert "statusColour" in script
    # The icon is the whole block, so the bar that used to sit above it is gone.
    assert "border-top:3px solid var(--state)" not in css
    assert ".state strong" not in css and ".state small" not in css
    # The wording did not disappear, it moved into the tooltip, which is also the
    # accessible label because there is no visible text left to name the block.
    assert "${stateText(item, state)}" in script
    assert "aria-label=" in script
    assert "counter: ['Sem contagem', 'Contando']" in script
    assert "statusTooltip" in script


def test_every_hue_the_script_emits_exists_in_the_stylesheet():
    """Join the two halves: a class the stylesheet does not define renders grey.

    The colour was verified once against a hand written class name, which is how a
    missing prefix survived: the stylesheet was right and the markup was wrong, and
    every block fell back to the neutral default without anything failing.
    """
    script = TestClient(app).get("/static/app.js").text
    css = TestClient(app).get("/static/styles.css").text

    block = script.split("const STATE_HUES = {", 1)[1].split("};", 1)[0]
    hues = set(re.findall(r":\s*'([a-z]+)'", block))
    assert hues == {"blue", "green", "red", "cyan"}

    # The class is built with a prefix, and the stylesheet has to use the same one.
    assert "'on-' +" in script or "`on-${" in script
    for hue in hues:
        assert f".state.on-{hue}{{" in css, f"a classe .state.on-{hue} sumiu do CSS"
    # Both neutral cases the script can emit must be covered as well.
    assert '.state{--state:#b6c6d1' in css
    assert ".state.unknown .state-icon{" in css

    page = TestClient(app).get("/").text
    assert "tracejado" in page


def test_the_troubleshooting_panel_points_at_the_command_not_the_database():
    page = TestClient(app).get("/").text
    # The old advice was to delete the settings table by hand: destructive, and
    # more than a customer should ever be asked to do.
    assert "apague a tabela" not in page
    assert "Senha esquecida" in page
    # Both topics that cover a forgotten password point at the command.
    assert page.count("--reset-password") >= 2


def test_help_page_lists_the_library_files():
    page = TestClient(app).get("/").text
    assert "9. BIBLIOTECA" in page
    # The block travels as an SCL source, imported as an external source.
    assert "FB_SP-CLP.scl" in page
    assert "Fontes externas" in page
    assert "Gerar blocos a partir da fonte" in page
    assert "TIA Portal 13" in page
    # The customer watches production and never opens TIA Portal, so the topic has
    # to say who it is for instead of reading like a task for everyone.
    assert "quem programa o CLP" in page
    # The customer may have installed TIA Portal in English, so the labels and the
    # file filter of the dialog go in both languages, as they appear on screen.
    assert "External source files" in page
    assert "*.scl;*.db;*.udt" in page
    # The old flow was a .zal global library opened from a menu: it must not come
    # back, because the customer would follow it and find no such file.
    assert "Bibliotecas globais" not in page
    assert "zal" not in page
    assert 'id="library-files"' in page
    # The rows are built in app.js, so the list must wait for the endpoint.
    assert "loadLibrary" in TestClient(app).get("/static/app.js").text


def test_the_help_page_has_a_summary_that_reaches_every_topic():
    page = TestClient(app).get("/").text
    anchors = re.findall(r'href="#(doc-[a-z-]+)"', page)
    ids = re.findall(r'article class="panel doc" id="(doc-[a-z-]+)"', page)
    # Nine panels one after the other is a long scroll on a phone, so every topic
    # needs a way in, and every link needs something to land on.
    assert len(ids) == 9
    assert anchors == ids


def test_the_status_row_keeps_the_icons_side_by_side_on_a_phone():
    css = TestClient(app).get("/static/styles.css").text
    mobile = css[css.index("@media(max-width:700px)"):]
    # The blocks are icon only now, so one per row wastes five rows of height.
    assert ".state-grid{grid-template-columns:1fr}" not in mobile
    assert ".state-grid{grid-template-columns:repeat(auto-fit,minmax(44px,1fr))" in mobile


def test_the_help_summary_is_not_squeezed_by_the_menu_nav_rule():
    css = TestClient(app).get("/static/styles.css").text
    # nav{display:flex} exists for the header and the menu. The index inherits it
    # unless it says otherwise, and then it collapses to the width of one chip and
    # nine links become nine rows instead of five.
    assert ".doc-summary{display:block" in css
    assert ".doc-summary ul{display:grid" in css


def test_there_is_a_floating_button_to_return_to_the_top():
    page = TestClient(app).get("/").text
    assert 'id="to-top"' in page
    assert 'data-icon="chevron-up"' in page
    css = TestClient(app).get("/static/styles.css").text
    assert ".to-top{position:fixed" in css
    # display:grid beats the hidden attribute unless a rule says otherwise.
    assert "[hidden]{display:none!important}" in css
    script = TestClient(app).get("/static/app.js").text
    assert "'#to-top'" in script
    assert "updateToTop" in script


def test_changing_page_returns_to_the_top():
    script = TestClient(app).get("/static/app.js").text
    # The plant and help pages are long, so keeping the offset of the page being
    # left drops the reader in the middle of the one being opened.
    assert "window.scrollTo({top: 0})" in script


def test_the_automatic_signal_uses_the_cycle_icon():
    script = TestClient(app).get("/static/app.js").text
    # The power symbol read as a switch somebody presses. Automatic means the
    # machine cycles on its own, which is what the two arrows say.
    assert "item.kind === 'auto') return 'refresh-cw'" in script
    assert "item.kind === 'auto') return 'power'" not in script


def test_the_header_leaves_room_for_the_content_on_a_phone_and_less_on_a_desktop():
    css = TestClient(app).get("/static/styles.css").text
    mobile = css[css.index("@media(max-width:700px)"):]
    # The fixed header is 113 px on a phone and 102 px on a desktop. The padding has
    # to clear it and still leave a gap, and the phone had the tighter one.
    assert "main{max-width:1240px;margin:0 auto;padding:142px 6vw 70px" in css
    assert "main{padding:152px 5vw 50px}" in mobile


def test_the_page_does_not_let_phones_inflate_the_text():
    css = TestClient(app).get("/static/styles.css").text
    # Font boosting grows the text on a real phone and pushes the settings buttons
    # past the card edge, which a desktop browser never reproduces.
    assert "text-size-adjust:100%" in css


def test_the_settings_rows_stack_on_a_phone():
    css = TestClient(app).get("/static/styles.css").text
    mobile = css[css.index("@media(max-width:700px)"):]
    # Three buttons beside the machine name run past the card on a narrow screen.
    assert ".machine-list-item{flex-direction:column" in mobile
    assert ".machine-actions{flex-wrap:wrap}" in mobile
    assert ".list-item{flex-wrap:wrap" in mobile


def test_the_build_ships_the_block_the_help_page_offers():
    """The download and the real folder have to agree, or the page offers nothing."""
    from app import libraries

    names = [item.filename for item in libraries.available_files()]
    assert "FB_SP-CLP.scl" in names, "a biblioteca versionada sumiu do repositório"
    source = (libraries.BUNDLED_DIR / "s7" / "FB_SP-CLP.scl").read_text()
    assert 'FUNCTION_BLOCK "FB_SP-CLP"' in source
    # The panel reads absolute addresses, so the block must arrive with the
    # optimized access already off instead of relying on the operator.
    assert "S7_Optimized_Access := 'FALSE'" in source
    # The count is an edge, not a level: one pulse of Counter is one piece, and
    # nothing is counted while the machine is stopped. A level count would inflate
    # production silently, and no Python test can see inside a compiled block.
    assert "#R_Trig(CLK := #Counter)" in source
    assert "CU := #R_Trig.Q & #Run" in source
    # The counter restarts at zero on every PLC restart, which is the decrease the
    # panel already treats as a new baseline instead of negative production.
    assert 'R := "FirstScan"' in source
    # A re-export from TIA drops the header silently, and the block arrives in the
    # customer's project without an author to call.
    assert "AUTHOR : 'Ruy Junior'" in source
    assert "FAMILY : SP_CLP" in source
    assert TestClient(app).get("/api/library/s7/FB_SP-CLP.scl").status_code == 200


# --- sessoes: logout e expiracao por inatividade -------------------------------

CONFIG_AREAS = "/api/config/areas"


@pytest.fixture
def admin_password():
    """Seed a known password so a test can log in over HTTP, then put the store back."""
    encoded = app_storage.get_setting("password_hash")
    app_storage.set_setting("password_hash", hash_password("senha-de-teste"))
    try:
        yield "senha-de-teste"
    finally:
        if encoded is None:
            with app_storage.connect() as connection:
                connection.execute("DELETE FROM settings WHERE key = ?", ("password_hash",))
        else:
            app_storage.set_setting("password_hash", encoded)


def _login(client: TestClient, password: str) -> str:
    response = client.post("/api/auth/login", json={"password": password})
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    assert token
    return token


def test_login_opens_a_session_that_reaches_a_config_endpoint(admin_password):
    client = TestClient(app)
    token = _login(client, admin_password)
    try:
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get(CONFIG_AREAS, headers=headers).status_code == 200
    finally:
        end_session(token)


def test_login_with_the_wrong_password_opens_nothing(admin_password):
    client = TestClient(app)
    response = client.post("/api/auth/login", json={"password": "errada"})
    assert response.status_code == 401
    assert "token" not in response.text


def test_logout_ends_the_session_it_was_given(admin_password):
    client = TestClient(app)
    token = _login(client, admin_password)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get(CONFIG_AREAS, headers=headers).status_code == 200

    assert client.post("/api/auth/logout", headers=headers).status_code == 204

    # The token is dropped, not merely marked as logged out.
    assert client.get(CONFIG_AREAS, headers=headers).status_code == 401


def test_logout_is_harmless_without_a_valid_token():
    """Ending a session that is already gone is normal: the idle deadline fires first."""
    client = TestClient(app)
    assert client.post("/api/auth/logout").status_code == 204
    assert client.post(
        "/api/auth/logout", headers={"Authorization": "Bearer nao-existe"}
    ).status_code == 204
    assert client.post(
        "/api/auth/logout", headers={"Authorization": "sem-bearer"}
    ).status_code == 204


def test_session_dies_by_itself_after_the_idle_window(monkeypatch):
    client = TestClient(app)
    token = start_session("test-token-idle")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        assert client.get(CONFIG_AREAS, headers=headers).status_code == 200
        # A negative window is already over the moment the session is checked.
        monkeypatch.setattr(main_module, "SESSION_IDLE_SECONDS", -1.0)
        assert client.get(CONFIG_AREAS, headers=headers).status_code == 401
        # Widening the window afterwards must not revive the token.
        monkeypatch.setattr(main_module, "SESSION_IDLE_SECONDS", 300.0)
        assert client.get(CONFIG_AREAS, headers=headers).status_code == 401
        assert token not in main_module.sessions
    finally:
        end_session(token)


def test_an_authenticated_request_pushes_the_idle_deadline(monkeypatch):
    client = TestClient(app)
    token = start_session("test-token-idle-refresh")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        monkeypatch.setattr(main_module, "SESSION_IDLE_SECONDS", 10.0)
        # Nine of the ten seconds are already spent when the request arrives.
        main_module.sessions[token] = time.monotonic() - 9.0
        assert client.get(CONFIG_AREAS, headers=headers).status_code == 200
        # The request reset the clock instead of leaving it one second from death.
        assert time.monotonic() - main_module.sessions[token] < 1.0
    finally:
        end_session(token)


def test_the_menu_offers_a_logout_button_that_waits_for_a_session():
    page = TestClient(app).get("/").text
    assert re.search(r'id="logout-button"[^>]*\bhidden\b', page), (
        "o botao Sair precisa comecar escondido"
    )
    assert 'data-icon="log-out"' in page


def test_the_script_ends_the_session_on_logout_and_after_five_idle_minutes():
    script = TestClient(app).get("/static/app.js").text
    assert "IDLE_LOGOUT_MS = 5 * 60 * 1000" in script
    assert "/api/auth/logout" in script
    assert "startUserSession" in script and "endUserSession" in script
    assert "logoutButton.hidden = false" in script
    assert "logoutButton.hidden = true" in script
    # A refused token means the server already ended the session, so the panel has to
    # drop it too instead of looking logged in.
    assert "response.status === 401 && token" in script
    # Only starting a session and real user input may re-arm the timer. The 5 s
    # refresh is not activity, or a wall panel would never log out.
    assert len(re.findall(r"\barmIdleLogout\(\)", script)) == 3


# --- troca de senha ----------------------------------------------------------


def _change_password(client: TestClient, headers: dict[str, str], current: str, new: str):
    return client.post(
        "/api/auth/password",
        headers=headers,
        json={"current_password": current, "new_password": new},
    )


def test_changing_the_password_replaces_the_stored_hash(admin_password):
    client = TestClient(app)
    token = _login(client, admin_password)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = _change_password(client, headers, admin_password, "senha-nova-123")
        assert response.status_code == 204
        assert verify_password("senha-nova-123", app_storage.get_setting("password_hash"))
        # The old password stops opening sessions, the new one opens them.
        assert client.post("/api/auth/login", json={"password": admin_password}).status_code == 401
        end_session(_login(client, "senha-nova-123"))
        # The panel that changed the password keeps working.
        assert client.get(CONFIG_AREAS, headers=headers).status_code == 200
    finally:
        end_session(token)


def test_a_wrong_current_password_is_refused_and_keeps_the_session(admin_password):
    client = TestClient(app)
    token = _login(client, admin_password)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = _change_password(client, headers, "nao-e-a-minha", "senha-nova-123")
        # 403 and not 401: the script reads 401 as a session that ended and would
        # drop the caller out of the panel over a typo.
        assert response.status_code == 403
        assert client.get(CONFIG_AREAS, headers=headers).status_code == 200
        # The stored password did not move.
        assert verify_password(admin_password, app_storage.get_setting("password_hash"))
    finally:
        end_session(token)


def test_changing_the_password_closes_the_other_sessions(admin_password):
    client = TestClient(app)
    first = _login(client, admin_password)
    second = _login(client, admin_password)
    second_headers = {"Authorization": f"Bearer {second}"}
    try:
        assert client.get(CONFIG_AREAS, headers=second_headers).status_code == 200
        change = _change_password(client, {"Authorization": f"Bearer {first}"}, admin_password, "senha-nova-123")
        assert change.status_code == 204
        # The other panel is out; the one that made the change stays in.
        assert client.get(CONFIG_AREAS, headers=second_headers).status_code == 401
        assert client.get(CONFIG_AREAS, headers={"Authorization": f"Bearer {first}"}).status_code == 200
    finally:
        end_session(first)
        end_session(second)


def test_changing_the_password_needs_a_session(admin_password):
    client = TestClient(app)
    assert client.post(
        "/api/auth/password",
        json={"current_password": admin_password, "new_password": "senha-nova-123"},
    ).status_code == 401
    assert client.post(
        "/api/auth/password",
        headers={"Authorization": "Bearer nao-existe"},
        json={"current_password": admin_password, "new_password": "senha-nova-123"},
    ).status_code == 401
    assert verify_password(admin_password, app_storage.get_setting("password_hash"))


def test_a_short_new_password_is_rejected(admin_password):
    client = TestClient(app)
    token = _login(client, admin_password)
    try:
        response = _change_password(
            client, {"Authorization": f"Bearer {token}"}, admin_password, "curta"
        )
        # Pydantic refuses it before the handler runs, so the hash never changes.
        assert response.status_code == 422
        assert verify_password(admin_password, app_storage.get_setting("password_hash"))
    finally:
        end_session(token)


def test_the_settings_page_offers_the_password_fields_inside_the_locked_area():
    page = TestClient(app).get("/").text
    for field in ("password-form", "password-current", "password-new", "password-confirm"):
        assert f'id="{field}"' in page, f"falta o campo {field}"
    assert 'type="password"' in page
    # The form belongs to the authenticated area, not to a public page.
    assert page.index('id="settings-page"') < page.index('id="password-form"') < page.index('id="help-page"')

    script = TestClient(app).get("/static/app.js").text
    assert "'/api/auth/password'" in script
    # The two fields have to match before anything is sent, because a typo here
    # locks the customer out with no way back.
    assert "password-confirm" in script
    assert "next !== repetition" in script


def test_the_help_page_explains_the_new_session_rules():
    page = TestClient(app).get("/").text
    assert "A sessão dura enquanto o servidor estiver rodando" not in page
    assert "5 minutos" in page
    assert "Trocar a senha" in page


# --- recuperacao de senha pela linha de comando -------------------------------


def _answers(*values):
    """A getpass replacement that hands back the values in order."""
    replies = list(values)

    def ask(_prompt):
        return replies.pop(0)

    return ask


def test_only_the_flag_asks_for_a_reset():
    assert parse_options(["--reset-password"]).reset_password is True
    assert parse_options([]).reset_password is False
    # The panel opens from a shortcut, and an unexpected argument there must never
    # be the reason it refuses to start.
    assert parse_options(["--nao-existe"]).reset_password is False


def test_the_panel_documents_the_flag_in_its_own_help(capsys):
    with pytest.raises(SystemExit) as exit_info:
        parse_options(["--help"])
    assert exit_info.value.code == 0
    assert "--reset-password" in capsys.readouterr().out


def test_the_reset_refuses_a_mismatch_and_changes_nothing(admin_password):
    written = []
    refused = apply_reset(
        app_storage, reader=_answers("senha-nova-123", "outra-senha-456"), writer=written.append
    )
    assert refused is False
    assert verify_password(admin_password, app_storage.get_setting("password_hash"))
    assert any("conferem" in line for line in written)
    # Nothing the command prints may carry the password it just read.
    assert not any("senha-nova-123" in line for line in written)


def test_the_reset_refuses_a_short_password(admin_password):
    written = []
    refused = apply_reset(app_storage, reader=_answers("curta", "curta"), writer=written.append)
    assert refused is False
    assert verify_password(admin_password, app_storage.get_setting("password_hash"))
    assert any("8 caracteres" in line for line in written)


def test_the_reset_replaces_the_password_the_login_uses(admin_password):
    client = TestClient(app)
    end_session(_login(client, admin_password))
    written = []
    changed = apply_reset(
        app_storage, reader=_answers("senha-nova-123", "senha-nova-123"), writer=written.append
    )
    assert changed is True
    # The key the command writes has to be the key the panel reads.
    assert client.post("/api/auth/login", json={"password": admin_password}).status_code == 401
    end_session(_login(client, "senha-nova-123"))
    assert written


def test_the_reset_flag_leaves_without_serving(monkeypatch):
    served = []
    monkeypatch.setattr(getpass, "getpass", _answers("senha-nova-123", "senha-nova-123"))
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: served.append(args))
    monkeypatch.setattr(sys, "argv", ["SP-CLP.exe", "--reset-password"])

    with pytest.raises(SystemExit) as exit_info:
        main_module.run()

    assert exit_info.value.code == 0
    # A recovery command, not another way to start the panel.
    assert served == []
    assert verify_password("senha-nova-123", app_storage.get_setting("password_hash"))


def test_a_refused_reset_leaves_with_an_error_code(monkeypatch, admin_password):
    monkeypatch.setattr(getpass, "getpass", _answers("curta", "curta"))
    monkeypatch.setattr(sys, "argv", ["SP-CLP.exe", "--reset-password"])

    with pytest.raises(SystemExit) as exit_info:
        main_module.run()

    assert exit_info.value.code == 1
    assert verify_password(admin_password, app_storage.get_setting("password_hash"))


def test_there_is_no_http_way_to_reset_the_password():
    """The panel answers on every interface, so a reset route would be a backdoor."""
    paths = [getattr(route, "path", "") for route in app.routes]
    assert not [path for path in paths if "reset" in path]


def test_the_help_page_explains_how_to_recover_the_password():
    page = TestClient(app).get("/").text
    assert "--reset-password" in page
