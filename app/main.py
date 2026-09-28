from __future__ import annotations

import logging
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .models import (
    AreaInput,
    HourlyCount,
    LoginRequest,
    MachineInput,
    MachineStatus,
    PlantRenameInput,
    SetupPassword,
    SignalDefinition,
    SignalLabelInput,
    SignalValue,
)
from .plc import (
    EDITABLE_ADDRESSES,
    SIGNAL_LAYOUT,
    SIGNAL_TYPES,
    FakePLCClient,
    PLCClient,
    Snap7PLCClient,
    describe_error,
    duplicate_labels,
    signal_label,
)
from .security import hash_password, verify_password
from .slugs import RESERVED_PAGES, plant_name_error, plant_slugs, slugify
from .storage import Storage
from .timezones import local_hour


BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="SP-CLP Dashboard", version="0.1.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
storage = Storage()
sessions: set[str] = set()
plc_clients: dict[tuple[str, int, int], PLCClient] = {}
logger = logging.getLogger("sp_clp")

# Shown verbatim in the dashboard, so keep the user facing messages accented.
MACHINE_NOT_FOUND = "Máquina não encontrada"
AREA_NOT_FOUND = "Área não encontrada"


def require_admin(authorization: str | None = Header(default=None)) -> None:
    if not authorization or not authorization.startswith("Bearer ") or authorization[7:] not in sessions:
        raise HTTPException(status_code=401, detail="Autenticação necessária")


def client_for(machine) -> PLCClient:
    ip = machine["ip"].lower()
    key = (machine["ip"], machine["rack"], machine["slot"])
    if ip in {"fake", "simulator", "simulador"}:
        plc_clients.setdefault(key, FakePLCClient())
        return plc_clients[key]
    plc_clients.setdefault(key, Snap7PLCClient(machine["ip"], machine["rack"], machine["slot"]))
    return plc_clients[key]


@app.get("/", response_class=FileResponse)
def dashboard() -> Path:
    return BASE_DIR / "static" / "index.html"


def _known_pages() -> set[str]:
    """Addresses served by the single segment route.

    A plant answers as soon as it has an area, even before any machine exists.
    """
    return {slug.casefold() for slug in plant_slugs(storage.plant_names())}


@app.get("/{page}", response_class=FileResponse)
def named_page(page: str) -> Path:
    """Every plant answers on its own address, and so does every menu page.

    The router only sees one segment here, so /api/... and /static/... keep
    their own handlers.
    """
    allowed = {item.casefold() for item in RESERVED_PAGES} | _known_pages()
    if page.casefold() not in allowed:
        raise HTTPException(status_code=404, detail="Pagina nao encontrada")
    return BASE_DIR / "static" / "index.html"


@app.get("/api/setup/status")
def setup_status() -> dict[str, bool]:
    return {"password_configured": storage.get_setting("password_hash") is not None}


@app.post("/api/setup/password", status_code=204)
def setup_password(payload: SetupPassword) -> None:
    if storage.get_setting("password_hash") is not None:
        raise HTTPException(status_code=409, detail="Senha já configurada")
    storage.set_setting("password_hash", hash_password(payload.password))


@app.post("/api/auth/login")
def login(payload: LoginRequest) -> dict[str, str]:
    encoded = storage.get_setting("password_hash")
    if not encoded or not verify_password(payload.password, encoded):
        raise HTTPException(status_code=401, detail="Senha inválida")
    token = secrets.token_urlsafe(32)
    sessions.add(token)
    return {"token": token}


def _other_plant_names(plant_name: str) -> set[str]:
    """Every plant except the one being written, for the address clash check."""
    return {name for name in storage.plant_names() if name != plant_name}


@app.post("/api/config/areas", status_code=201, dependencies=[Depends(require_admin)])
def create_area(payload: AreaInput) -> dict[str, int]:
    error = plant_name_error(payload.plant_name, _other_plant_names(payload.plant_name))
    if error:
        raise HTTPException(status_code=422, detail=error)
    return {"id": storage.add_area(payload.plant_name, payload.name)}


@app.get("/api/config/areas", dependencies=[Depends(require_admin)])
def list_areas() -> list[dict]:
    # plant_slug lets the dashboard link and rename a plant without duplicating
    # the slug rules in JavaScript.
    return [
        {**dict(row), "plant_slug": slugify(row["plant_name"])}
        for row in storage.list_areas()
    ]


@app.put("/api/config/areas/{area_id}", dependencies=[Depends(require_admin)])
def update_area(area_id: int, payload: AreaInput) -> dict[str, int]:
    """Edit an area.

    The plant name cannot change here: a plant is the set of areas sharing the
    name, so renaming one area would leave two plants fighting over the same
    address. The plant endpoint renames all of them together.
    """
    area = storage.get_area(area_id)
    if area is None:
        raise HTTPException(status_code=404, detail=AREA_NOT_FOUND)
    if payload.plant_name != area["plant_name"]:
        raise HTTPException(
            status_code=422,
            detail="Para mudar o nome da planta use o painel Planta; isso renomeia todas as áreas dela",
        )
    storage.update_area(area_id, area["plant_name"], payload.name)
    return {"id": area_id}


@app.delete("/api/config/areas/{area_id}", dependencies=[Depends(require_admin)])
def remove_area(area_id: int) -> None:
    if storage.get_area(area_id) is None:
        raise HTTPException(status_code=404, detail=AREA_NOT_FOUND)
    remaining = storage.machines_in_area(area_id)
    if remaining:
        raise HTTPException(
            status_code=409,
            detail=f"Exclua as {remaining} máquinas desta área antes de excluir a área",
        )
    storage.delete_area(area_id)


@app.put("/api/config/plants/{slug}", dependencies=[Depends(require_admin)])
def rename_plant(slug: str, payload: PlantRenameInput) -> dict[str, int]:
    """Rename a plant, which in practice renames every area that belongs to it."""
    plants = plant_slugs(storage.plant_names())
    current = plants.get(slug.casefold())
    if current is None:
        raise HTTPException(status_code=404, detail="Planta não encontrada")
    # Compare against the name being replaced, so changing only the spelling of
    # an existing address is allowed.
    error = plant_name_error(payload.plant_name, _other_plant_names(current))
    if error:
        raise HTTPException(status_code=422, detail=error)
    return {"areas": storage.rename_plant(current, payload.plant_name)}


@app.post("/api/config/areas/{area_id}/machines", status_code=201, dependencies=[Depends(require_admin)])
def create_machine(area_id: int, payload: MachineInput) -> dict[str, int]:
    if storage.get_area(area_id) is None:
        raise HTTPException(status_code=400, detail="Área inválida")
    return {"id": storage.add_machine(area_id, payload.name, payload.ip, payload.db_number, payload.timezone)}


@app.put("/api/config/machines/{machine_id}", dependencies=[Depends(require_admin)])
def update_machine(machine_id: int, payload: MachineInput) -> dict[str, int]:
    if storage.get_machine(machine_id) is None:
        raise HTTPException(status_code=404, detail=MACHINE_NOT_FOUND)
    storage.update_machine(machine_id, payload.name, payload.ip, payload.db_number, payload.timezone)
    return {"id": machine_id}


@app.delete("/api/config/machines/{machine_id}", dependencies=[Depends(require_admin)])
def delete_machine(machine_id: int) -> None:
    if not storage.delete_machine(machine_id):
        raise HTTPException(status_code=404, detail=MACHINE_NOT_FOUND)


@app.get("/api/machines")
def list_machines() -> list[dict]:
    # plant_slug lets the dashboard link each plant to its own address without
    # duplicating the slug rules in JavaScript.
    return [
        {**dict(row), "plant_slug": slugify(row["plant_name"])}
        for row in storage.list_machines()
    ]


def build_signals(labels: dict[str, str], bits: dict[str, bool] | None) -> list[SignalValue]:
    """Full layout in address order. A None value means the PLC could not be read."""
    return [
        SignalValue(
            address=spec.address,
            label=signal_label(spec, labels),
            type=SIGNAL_TYPES[spec.address],
            kind=spec.kind,
            editable=spec.address in EDITABLE_ADDRESSES,
            value=None if bits is None else bool(bits.get(spec.address)),
        )
        for spec in SIGNAL_LAYOUT
    ]


@app.get("/api/machines/{machine_id}/status", response_model=MachineStatus)
def machine_status(machine_id: int) -> MachineStatus:
    machine = storage.get_machine(machine_id)
    if machine is None:
        raise HTTPException(status_code=404, detail=MACHINE_NOT_FOUND)
    labels = storage.signal_labels(machine_id)
    try:
        reading = client_for(machine).read(machine["db_number"])
        storage.save_sample(machine_id, reading.count, reading.timestamp)
        bits = reading.bits
        return MachineStatus(
            machine_id=machine_id, connected=True, stale=False,
            timestamp=reading.timestamp.isoformat(),
            auto=bits["0.0"], run=bits["0.1"], fault=bits["0.2"], safety=bits["0.3"],
            count=reading.count,
            signals=build_signals(labels, bits),
        )
    except (ConnectionError, OSError, RuntimeError, ValueError, ImportError) as error:
        # Polled every few seconds per machine, so keep this at debug level.
        logger.debug("Maquina %s (%s) indisponivel: %s", machine_id, machine["ip"], describe_error(error))
        return MachineStatus(
            machine_id=machine_id, connected=False, stale=True, timestamp=None,
            auto=None, run=None, fault=None, safety=None, count=None,
            signals=build_signals(labels, None),
        )


@app.get("/api/machines/{machine_id}/hourly-counts", response_model=list[HourlyCount])
def hourly_counts(machine_id: int, _: None = Query(default=None)) -> list[HourlyCount]:
    machine = storage.get_machine(machine_id)
    if machine is None:
        raise HTTPException(status_code=404, detail=MACHINE_NOT_FOUND)
    return [
        HourlyCount(
            hour_start=row["hour_start"],
            local_hour=local_hour(row["hour_start"], machine["timezone"]),
            quantity=row["quantity"],
        )
        for row in storage.hourly_counts(machine_id)
    ]


def describe_machine_signals(machine_id: int) -> list[SignalDefinition]:
    labels = storage.signal_labels(machine_id)
    return [
        SignalDefinition(
            address=spec.address,
            label=signal_label(spec, labels),
            type=SIGNAL_TYPES[spec.address],
            kind=spec.kind,
            editable=spec.address in EDITABLE_ADDRESSES,
        )
        for spec in SIGNAL_LAYOUT
    ]


@app.get("/api/config/machines/{machine_id}/signals", dependencies=[Depends(require_admin)])
def get_machine_signals(machine_id: int) -> list[SignalDefinition]:
    if storage.get_machine(machine_id) is None:
        raise HTTPException(status_code=404, detail=MACHINE_NOT_FOUND)
    return describe_machine_signals(machine_id)


@app.put("/api/config/machines/{machine_id}/signals", dependencies=[Depends(require_admin)])
def update_machine_signals(machine_id: int, payload: list[SignalLabelInput]) -> list[SignalDefinition]:
    if storage.get_machine(machine_id) is None:
        raise HTTPException(status_code=404, detail="Maquina nao encontrada")
    locked = sorted({item.address for item in payload if item.address not in EDITABLE_ADDRESSES})
    if locked:
        raise HTTPException(
            status_code=422,
            detail="Sinais fixos não podem ser renomeados: " + ", ".join(locked),
        )
    overrides = {item.address: item.label for item in payload}
    repeated = duplicate_labels(overrides)
    if repeated:
        raise HTTPException(
            status_code=422,
            detail="Cada sinal precisa de um rótulo próprio. Repetidos: " + ", ".join(repeated),
        )
    storage.set_signal_labels(machine_id, overrides)
    return describe_machine_signals(machine_id)


def run() -> None:
    import threading
    import uvicorn
    import webbrowser

    threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:8000")).start()
    uvicorn.run(app, host="127.0.0.1", port=8000)
