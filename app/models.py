from pydantic import BaseModel, Field, field_validator, model_validator

from .plc import DEFAULT_DB_FIRST, DEFAULT_DB_LAST, MAX_DB_RANGE, KNOWN_ADDRESSES
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


class ScanInput(BaseModel):
    """An address and a range typed in the machine form, swept before saving."""

    ip: str = Field(min_length=1, max_length=255)
    first: int = Field(default=DEFAULT_DB_FIRST, ge=1, le=65535)
    last: int = Field(default=DEFAULT_DB_LAST, ge=1, le=65535)

    @field_validator("ip")
    @classmethod
    def _no_blank_address(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Informe o IP do CLP")
        return cleaned

    @model_validator(mode="after")
    def _usable_range(self) -> "ScanInput":
        if self.last < self.first:
            raise ValueError("A faixa de DB esta invertida")
        if self.last - self.first + 1 > MAX_DB_RANGE:
            raise ValueError(f"A varredura aceita no maximo {MAX_DB_RANGE} DBs por vez")
        return self


class ScanResult(BaseModel):
    status: str
    message: str
    databases: list[int] = []
    scanned: int = 0
    answered: int = 0
    truncated: bool = False
    detail: str | None = None


class AreaInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    plant_name: str = Field(min_length=1, max_length=120)


class PlantRenameInput(BaseModel):
    plant_name: str = Field(min_length=1, max_length=120)


class BrandingInput(BaseModel):
    company_name: str = Field(min_length=1, max_length=60)


class LogoUpload(BaseModel):
    # Base64 keeps the upload to JSON and avoids another runtime dependency just
    # for multipart parsing. The size is really checked after decoding.
    filename: str = Field(default="logo", max_length=200)
    content: str = Field(min_length=1, max_length=2_000_000)


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


class HourlySlot(BaseModel):
    local_hour: str
    quantity: int


class HourlyDay(BaseModel):
    day: str
    today: str
    first_day: str
    last_day: str
    slots: list[HourlySlot]


class MachineStatus(BaseModel):
    machine_id: int
    connected: bool
    stale: bool
    timestamp: str | None
    auto: bool | None
    run: bool | None
    fault: bool | None
    safety: bool | None
    count: int | None
    signals: list[SignalValue]
