import logging
import re
from datetime import datetime, timezone

import pytest
from app.branding import remove_logos
from app.models import MachineInput
from app.plc import (
    BOOL_LAYOUT,
    DINT_LAYOUT,
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
from app.security import hash_password, verify_password
from app.storage import Storage
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app, storage as app_storage


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
    from app.main import sessions

    client = TestClient(app)
    area_id = app_storage.add_area("Test Plant", "API Labels Area")
    machine_id = app_storage.add_machine(area_id, "API Labels", "fake", 53, "UTC")
    token = "test-token-signal-labels"
    headers = {"Authorization": f"Bearer {token}"}
    sessions.add(token)
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
        sessions.discard(token)
        app_storage.delete_machine(machine_id)


def test_signal_labels_for_unknown_machine_are_rejected():
    from app.main import sessions

    client = TestClient(app)
    token = "test-token-signal-labels-missing"
    headers = {"Authorization": f"Bearer {token}"}
    sessions.add(token)
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
        sessions.discard(token)


def _admin_client(token: str) -> TestClient:
    from app.main import sessions

    sessions.add(token)
    return TestClient(app)


def _temp_machine(name: str = "Signals") -> int:
    area_id = app_storage.add_area("Test Plant", "Signals Area")
    return app_storage.add_machine(area_id, name, "fake", 53, "UTC")


def test_locked_signals_cannot_be_renamed_through_the_api():
    from app.main import describe_machine_signals, sessions

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
        sessions.discard("test-token-locked")
        app_storage.delete_machine(machine_id)


def test_duplicate_signal_labels_are_rejected_through_the_api():
    from app.main import sessions

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
        sessions.discard("test-token-duplicates")
        app_storage.delete_machine(machine_id)


def test_the_three_integers_can_be_renamed_through_the_api():
    from app.main import sessions

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
        sessions.discard("test-token-integers")
        app_storage.delete_machine(machine_id)


def test_the_counter_cannot_be_renamed_through_the_api():
    from app.main import describe_machine_signals, sessions

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
        sessions.discard("test-token-counter")
        app_storage.delete_machine(machine_id)


def test_dashboard_icons_are_vendored_and_used():
    client = TestClient(app)
    icons = client.get("/static/icons.js")
    assert icons.status_code == 200, "the vendored icon module is not served"
    assert "/static/icons.js" in client.get("/").text

    defined = set(re.findall(r'^\s{2}"([a-z0-9-]+)":', icons.text, re.MULTILINE))
    assert "circle-check" in defined and "lock" in defined

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
        from app.main import sessions

        sessions.discard("test-token-branding")
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
        from app.main import sessions

        sessions.discard("test-token-branding-bad")


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
        from app.main import sessions

        sessions.discard("test-token-area-edit")


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
        from app.main import sessions

        sessions.discard("test-token-area-plant")


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
        from app.main import sessions

        sessions.discard("test-token-area-delete")


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
        from app.main import sessions

        sessions.discard("test-token-plant-rename")


def test_plant_names_must_own_a_usable_address():
    client = _admin_client("test-token-plant-clash")
    headers = {"Authorization": "Bearer test-token-plant-clash"}
    areas: list[int] = []
    try:
        # "Rio Claro" and "rio-claro" reduce to the same address.
        areas.append(app_storage.add_area("colisao base", "Area Um"))
        response = client.post(
            "/api/config/areas",
            headers=headers,
            json={"plant_name": "colisao-base", "name": "Area Dois"},
        )
        assert response.status_code == 422
        assert "colisao base" in response.json()["detail"]

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
        from app.main import sessions

        sessions.discard("test-token-plant-clash")


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
        from app.main import sessions

        sessions.discard("test-token-plant-case")


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
        from app.main import sessions

        sessions.discard("test-token-scan")


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
        from app.main import sessions

        sessions.discard("test-token-scan-range")


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
        from app.main import sessions

        sessions.discard("test-token-scan-blank")


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
        from app.main import sessions

        sessions.discard("test-token-scan-bad-range")


def test_the_card_lists_the_counter_bit_beside_the_other_signals():
    """The counter has no block of its own, so dropping it from the list hides it."""
    script = TestClient(app).get("/static/app.js").text
    assert "listedSignals" in script
    assert "item.kind === 'counter'" in script
    # The big number is Count from the DB, which is a different thing entirely.
    assert "CONTADOR ATUAL" in script


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
