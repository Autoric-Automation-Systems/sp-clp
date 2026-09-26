from pydantic import BaseModel, Field, field_validator

from .timezones import is_known_zone


class SetupPassword(BaseModel):
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    password: str


class MachineInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    ip: str = Field(min_length=1, max_length=255)
    db_number: int = Field(ge=1, le=65535)
    timezone: str = "UTC"

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        if not is_known_zone(value):
            raise ValueError("Fuso horario IANA desconhecido; use por exemplo America/Sao_Paulo")
        return value


class Machine(MachineInput):
    id: int
    area_id: int
    rack: int = 0
    slot: int = 1


class AreaInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    plant_name: str = Field(min_length=1, max_length=120)


class SignalValue(BaseModel):
    name: str
    address: str
    value: bool


class HourlyCount(BaseModel):
    hour_start: str
    local_hour: str
    quantity: int


class MachineStatus(BaseModel):
    machine_id: int
    connected: bool
    stale: bool
    timestamp: str | None
    auto: bool | None
    run: bool | None
    fault: bool | None
    count: int | None
    signals: list[SignalValue]
