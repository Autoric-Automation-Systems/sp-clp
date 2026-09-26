from __future__ import annotations

import logging
import struct
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


# python-snap7 calls logger.error() with raw bytes on every failed connect, and the
# logger has no handler, so Python's lastResort handler dumps b' TCP : Unreachable peer'
# to stderr. Silence the library and report failures from application code instead.
logging.getLogger("snap7").setLevel(logging.CRITICAL)

@dataclass(frozen=True)
class SignalSpec:
    """One fixed position in the PLC DB. Address and type are never configurable."""

    address: str
    kind: str
    default_label: str


# Standard contract: BOOL signals occupy 0.0 through 1.7, COUNTER is a BOOL at 2.0.
# 0.0/0.1/0.2 are the standard AUTO/RUN/FAULT status bits used by the dashboard logic.
SIGNAL_LAYOUT: tuple[SignalSpec, ...] = (
    SignalSpec("0.0", "auto", "Automatico"),
    SignalSpec("0.1", "run", "Producao"),
    SignalSpec("0.2", "fault", "Saude"),
    *(SignalSpec(f"0.{bit}", "custom", f"Sinal 0.{bit}") for bit in range(3, 8)),
    *(SignalSpec(f"1.{bit}", "custom", f"Sinal 1.{bit}") for bit in range(8)),
    SignalSpec("2.0", "counter", "Contador"),
)

SIGNAL_ADDRESSES = [spec.address for spec in SIGNAL_LAYOUT]
KNOWN_ADDRESSES = frozenset(SIGNAL_ADDRESSES)
SIGNAL_TYPES = {spec.address: "BOOL" for spec in SIGNAL_LAYOUT}


def describe_error(error: BaseException) -> str:
    """Return a readable message; snap7 raises RuntimeError with a raw bytes argument."""
    if not error.args:
        return error.__class__.__name__
    detail = error.args[0]
    if isinstance(detail, bytes):
        return detail.decode("utf-8", "replace").strip()
    return str(detail)



@dataclass(frozen=True)
class PLCReading:
    timestamp: datetime
    bits: dict[str, bool]
    count: int


class PLCClient(Protocol):
    def read(self, db_number: int) -> PLCReading: ...


def bit_value(data: bytes, address: str) -> bool:
    byte, bit = (int(part) for part in address.split("."))
    return bool(data[byte] & (1 << bit))


def signal_label(spec: SignalSpec, labels: dict[str, str] | None = None) -> str:
    """Return the configured label, falling back to the documented default."""
    configured = (labels or {}).get(spec.address, "").strip()
    return configured or spec.default_label


def parse_db(data: bytes) -> PLCReading:
    """Readings are keyed by address: labels are user editable and may repeat."""
    if len(data) < 8:
        raise ValueError("PLC DB data must contain at least 8 bytes")
    return PLCReading(
        timestamp=datetime.now(timezone.utc),
        bits={spec.address: bit_value(data, spec.address) for spec in SIGNAL_LAYOUT},
        count=struct.unpack(">i", data[4:8])[0],
    )


class FakePLCClient:
    def __init__(self, count: int = 1250, connected: bool = True) -> None:
        self.count = count
        self.connected = connected

    def read(self, db_number: int) -> PLCReading:
        if not self.connected:
            raise ConnectionError("Fake PLC disconnected")
        data = bytearray(8)
        data[0] = 0b00000011
        data[2] = 0b00000001
        data[4:8] = self.count.to_bytes(4, "big", signed=True)
        self.count += 1
        return parse_db(bytes(data))


class Snap7PLCClient:
    def __init__(self, ip: str, rack: int = 0, slot: int = 1) -> None:
        self.ip, self.rack, self.slot = ip, rack, slot
        self._client = None
        self._lock = threading.Lock()

    def _connect(self):
        import snap7
        client = snap7.client.Client()
        client.connect(self.ip, self.rack, self.slot)
        self._client = client
        return client

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                self._client.disconnect()
                self._client = None

    def read(self, db_number: int) -> PLCReading:
        with self._lock:
            try:
                client = self._client or self._connect()
                return parse_db(bytes(client.db_read(db_number, 0, 8)))
            except Exception:
                if self._client is not None:
                    self._client.disconnect()
                    self._client = None
                raise
