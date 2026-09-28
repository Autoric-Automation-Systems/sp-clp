from pydantic import BaseModel, Field, field_validator

from .plc import KNOWN_ADDRESSES
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
            raise ValueError("Fuso horário IANA desconhecido; use por exemplo America/Sao_Paulo")
        return value


class Machine(MachineInput):
    id: int
    area_id: int
    rack: int = 0
    slot: int = 1


class AreaInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    plant_name: str = Field(min_length=1, max_length=120)


class SignalDefinition(BaseModel):
    address: str
    label: str
    type: str
    kind: str
    # Fixed signals keep the contract label; the dashboard renders them read only.
    editable: bool


class SignalValue(SignalDefinition):
    # None means the PLC could not be read, not that the bit is false.
    value: bool | None = None


class SignalLabelInput(BaseModel):
    address: str
    label: str = Field(default="", max_length=60)

    @field_validator("address")
    @classmethod
    def _known_address(cls, value: str) -> str:
        if value not in KNOWN_ADDRESSES:
            raise ValueError("Endereço desconhecido: o endereço e o tipo não podem ser alterados")
        return value

    @field_validator("label")
    @classmethod
    def _single_line(cls, value: str) -> str:
        cleaned = value.strip()
        if "\n" in cleaned or "\r" in cleaned:
            raise ValueError("Rótulo deve ocupar uma única linha")
        return cleaned


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
