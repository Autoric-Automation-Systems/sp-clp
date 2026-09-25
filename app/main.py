from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .models import AreaInput, LoginRequest, MachineInput, MachineStatus, SetupPassword
from .plc import FakePLCClient, PLCClient, Snap7PLCClient
from .security import hash_password, verify_password
from .storage import Storage


BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="SP-CLP Dashboard", version="0.1.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
storage = Storage()
sessions: set[str] = set()


def require_admin(authorization: str | None = Header(default=None)) -> None:
    if not authorization or not authorization.startswith("Bearer ") or authorization[7:] not in sessions:
        raise HTTPException(status_code=401, detail="Autenticacao necessaria")


def client_for(machine) -> PLCClient:
    if machine["ip"].lower() in {"fake", "simulator", "simulador"}:
        return FakePLCClient()
    return Snap7PLCClient(machine["ip"], machine["rack"], machine["slot"])


@app.get("/", response_class=FileResponse)
def dashboard() -> Path:
    return BASE_DIR / "static" / "index.html"


@app.get("/api/setup/status")
def setup_status() -> dict[str, bool]:
    return {"password_configured": storage.get_setting("password_hash") is not None}


@app.post("/api/setup/password", status_code=204)
def setup_password(payload: SetupPassword) -> None:
    if storage.get_setting("password_hash") is not None:
        raise HTTPException(status_code=409, detail="Senha ja configurada")
    storage.set_setting("password_hash", hash_password(payload.password))


@app.post("/api/auth/login")
def login(payload: LoginRequest) -> dict[str, str]:
    encoded = storage.get_setting("password_hash")
    if not encoded or not verify_password(payload.password, encoded):
        raise HTTPException(status_code=401, detail="Senha invalida")
    token = secrets.token_urlsafe(32)
    sessions.add(token)
    return {"token": token}


@app.post("/api/config/areas", status_code=201, dependencies=[Depends(require_admin)])
def create_area(payload: AreaInput) -> dict[str, int]:
    return {"id": storage.add_area(payload.plant_name, payload.name)}


@app.get("/api/config/areas", dependencies=[Depends(require_admin)])
def list_areas() -> list[dict]:
    return [dict(row) for row in storage.list_areas()]


@app.post("/api/config/areas/{area_id}/machines", status_code=201, dependencies=[Depends(require_admin)])
def create_machine(area_id: int, payload: MachineInput) -> dict[str, int]:
    if storage.get_area(area_id) is None:
        raise HTTPException(status_code=400, detail="Area invalida")
    return {"id": storage.add_machine(area_id, payload.name, payload.ip, payload.db_number, payload.timezone)}


@app.put("/api/config/machines/{machine_id}", dependencies=[Depends(require_admin)])
def update_machine(machine_id: int, payload: MachineInput) -> dict[str, int]:
    if storage.get_machine(machine_id) is None:
        raise HTTPException(status_code=404, detail="Maquina nao encontrada")
    storage.update_machine(machine_id, payload.name, payload.ip, payload.db_number, payload.timezone)
    return {"id": machine_id}


@app.delete("/api/config/machines/{machine_id}", dependencies=[Depends(require_admin)])
def delete_machine(machine_id: int) -> None:
    if not storage.delete_machine(machine_id):
        raise HTTPException(status_code=404, detail="Maquina nao encontrada")


@app.get("/api/machines")
def list_machines() -> list[dict]:
    return [dict(row) for row in storage.list_machines()]


@app.get("/api/machines/{machine_id}/status", response_model=MachineStatus)
def machine_status(machine_id: int) -> MachineStatus:
    machine = storage.get_machine(machine_id)
    if machine is None:
        raise HTTPException(status_code=404, detail="Maquina nao encontrada")
    try:
        reading = client_for(machine).read(machine["db_number"])
        storage.save_sample(machine_id, reading.count, reading.timestamp)
        named = reading.bits
        signals = [
            {"name": name, "address": address, "value": value}
            for address, (name, value) in zip(
                [f"{byte}.{bit}" for byte in (0, 1) for bit in range(8)] + ["2.0"], named.items()
            )
        ]
        return MachineStatus(
            machine_id=machine_id, connected=True, stale=False,
            timestamp=reading.timestamp.isoformat(), auto=named.get("AUTO"),
            run=named.get("RUN"), fault=named.get("FAULT"), count=reading.count,
            signals=signals,
        )
    except (ConnectionError, OSError, RuntimeError, ValueError, ImportError):
        return MachineStatus(
            machine_id=machine_id, connected=False, stale=True, timestamp=None,
            auto=None, run=None, fault=None, count=None, signals=[],
        )


@app.get("/api/machines/{machine_id}/hourly-counts")
def hourly_counts(machine_id: int, _: None = Query(default=None)) -> list[dict]:
    if storage.get_machine(machine_id) is None:
        raise HTTPException(status_code=404, detail="Maquina nao encontrada")
    return [dict(row) for row in storage.hourly_counts(machine_id)]


def run() -> None:
    import threading
    import uvicorn
    import webbrowser

    threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:8000")).start()
    uvicorn.run(app, host="127.0.0.1", port=8000)
