from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


SIGNAL_ADDRESSES = [f"{byte}.{bit}" for byte in (0, 1) for bit in range(8)]
DEFAULT_SIGNAL_NAMES = {
    "0.0": "AUTO", "0.1": "RUN", "0.2": "FAULT", "2.0": "COUNTER",
}


@dataclass(frozen=True)
class PLCReading:
    timestamp: datetime
    bits: dict[str, bool]
    count: int


class PLCClient(Protocol):
    def read(self, db_number: int) -> PLCReading: ...


def parse_db(data: bytes, signal_names: dict[str, str] | None = None) -> PLCReading:
    if len(data) < 8:
        raise ValueError("PLC DB data must contain at least 8 bytes")
    names = signal_names or {}
    bits = {
        address: bool(data[int(address.split(".")[0])] & (1 << int(address.split(".")[1])))
        for address in SIGNAL_ADDRESSES
    }
    bits["2.0"] = bool(data[2] & 1)
    return PLCReading(
        timestamp=datetime.now(timezone.utc),
        bits={names.get(address, DEFAULT_SIGNAL_NAMES.get(address, f"Signal_{address.replace('.', '_')}")): value
              for address, value in bits.items()},
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

    def read(self, db_number: int) -> PLCReading:
        import snap7
        client = snap7.client.Client()
        try:
            client.connect(self.ip, self.rack, self.slot)
            return parse_db(bytes(client.db_read(db_number, 0, 6)))
        finally:
            client.disconnect()
