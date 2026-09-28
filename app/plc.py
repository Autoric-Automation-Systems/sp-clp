from __future__ import annotations

import logging
import struct
import threading
import unicodedata
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
# 0.0-0.3 are the four standard status bits (AUTO, RUN, FAULT, SAFETY) that drive
# the dashboard logic. Their labels are part of the contract and cannot be renamed.
SIGNAL_LAYOUT: tuple[SignalSpec, ...] = (
    SignalSpec("0.0", "auto", "Automático"),
    SignalSpec("0.1", "run", "Produção"),
    SignalSpec("0.2", "fault", "Falha"),
    SignalSpec("0.3", "safety", "Segurança"),
    *(SignalSpec(f"0.{bit}", "custom", f"Sinal 0.{bit}") for bit in range(4, 8)),
    *(SignalSpec(f"1.{bit}", "custom", f"Sinal 1.{bit}") for bit in range(8)),
    SignalSpec("2.0", "counter", "Contador"),
)

SIGNAL_ADDRESSES = [spec.address for spec in SIGNAL_LAYOUT]
KNOWN_ADDRESSES = frozenset(SIGNAL_ADDRESSES)
# The four status bits drive the dashboard status blocks, so their labels belong
# to the contract. Every other signal, the counter included, accepts a user label.
LOCKED_KINDS = frozenset({"auto", "run", "fault", "safety"})
EDITABLE_ADDRESSES = frozenset(
    spec.address for spec in SIGNAL_LAYOUT if spec.kind not in LOCKED_KINDS
)
LOCKED_ADDRESSES = frozenset(SIGNAL_ADDRESSES) - EDITABLE_ADDRESSES
SIGNAL_TYPES = {spec.address: "BOOL" for spec in SIGNAL_LAYOUT}

# Absolute layout of the customer DB, read from the block in the field:
#
#   DBX0.0-1.7   BOOL signals (AUTO, RUN, FAULT, SAFETY and the free positions)
#   DBX2.0       Counter (BOOL)
#   DBX4.0-8.0   Type, ARRAY[0..4] OF CHAR = 'S','P','C','L','P'
#   DBD10.0      Count (DInt, big-endian)
#
# The signature is what the configuration scan looks for, so a wrong DB number is
# caught before the machine is saved. A CHAR array is used instead of a numeric
# magic number because it is readable in TIA Portal and has no byte order to get
# wrong. Byte 9 is the alignment gap before the counter.
SIGNATURE_OFFSET = 4
SIGNATURE_TEXT = "SPCLP"
SIGNATURE = SIGNATURE_TEXT.encode("ascii")

# The dashboard reads the status word, the signature and the counter in one go.
COUNT_OFFSET = 10
COUNT_SIZE = 4
READ_SIZE = COUNT_OFFSET + COUNT_SIZE

# Rack and slot of the CPU the contract targets. The scan uses the same values
# the dashboard will use once the machine is saved.
DEFAULT_RACK = 0
DEFAULT_SLOT = 1


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
    """Return the display label for one signal.

    The four status bits keep the contract label, so a stored override for them
    (for example a row left behind by an older version) is ignored.
    """
    if spec.address not in EDITABLE_ADDRESSES:
        return spec.default_label
    configured = (labels or {}).get(spec.address, "").strip()
    return configured or spec.default_label


def effective_labels(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Resolve the label of every address in the contract."""
    return {spec.address: signal_label(spec, overrides) for spec in SIGNAL_LAYOUT}


def fold_label(label: str) -> str:
    """Comparison key for labels.

    Ignores case and accents because "Seguranca" and "Segurança" would be read as
    the same word on the dashboard.
    """
    decomposed = unicodedata.normalize("NFKD", label.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def duplicate_labels(overrides: dict[str, str] | None = None) -> list[str]:
    """Return every label that would end up used by more than one address."""
    seen: dict[str, str] = {}
    repeated: set[str] = set()
    for label in effective_labels(overrides).values():
        key = fold_label(label)
        if key in seen:
            repeated.add(seen[key])
        else:
            seen[key] = label
    return sorted(repeated)


def parse_db(data: bytes) -> PLCReading:
    """Readings are keyed by address: two signals may never share a label."""
    if len(data) < READ_SIZE:
        raise ValueError(f"PLC DB data must contain at least {READ_SIZE} bytes")
    return PLCReading(
        timestamp=datetime.now(timezone.utc),
        bits={spec.address: bit_value(data, spec.address) for spec in SIGNAL_LAYOUT},
        count=struct.unpack(">i", data[COUNT_OFFSET:COUNT_OFFSET + COUNT_SIZE])[0],
    )


class FakePLCClient:
    def __init__(self, count: int = 1250, connected: bool = True) -> None:
        self.count = count
        self.connected = connected

    def read(self, db_number: int) -> PLCReading:
        if not self.connected:
            raise ConnectionError("Fake PLC disconnected")
        data = bytearray(READ_SIZE)
        # AUTO and RUN set, SAFETY clear of pending, counter counting.
        data[0] = 0b00001011
        data[2] = 0b00000001
        # The simulator carries the same signature and offsets as the real block,
        # so a machine registered as "fake" behaves like a prepared PLC.
        data[SIGNATURE_OFFSET:SIGNATURE_OFFSET + len(SIGNATURE)] = SIGNATURE
        data[COUNT_OFFSET:COUNT_OFFSET + COUNT_SIZE] = self.count.to_bytes(COUNT_SIZE, "big", signed=True)
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
                return parse_db(bytes(client.db_read(db_number, 0, READ_SIZE)))
            except Exception:
                if self._client is not None:
                    self._client.disconnect()
                    self._client = None
                raise


# The scan tells three different mistakes apart, because each one is fixed in a
# different place: the cable and the IP, the DB number, or the DB contents.
PROBE_READY = "ready"
PROBE_UNSIGNED = "unsigned"
PROBE_MISSING = "missing"
PROBE_UNREACHABLE = "unreachable"

# Long enough for a plant network, short enough that one click cannot hang the
# request thread while a machine with no PLC at that address is being scanned.
PROBE_TIMEOUT_MS = 2000


@dataclass(frozen=True)
class PLCProbe:
    status: str
    message: str
    detail: str | None = None


class PLCProbeClient(Protocol):
    """What the scan needs from a transport, so it can be faked without a PLC."""

    def open(self) -> None: ...

    def read_block(self, db_number: int, start: int, size: int) -> bytes: ...

    def close(self) -> None: ...


def readable_signature(block: bytes) -> str:
    """Return only the text characters found in a block.

    A wrong offset usually points at numbers, and showing those bytes as text
    would print gibberish instead of admitting that nothing readable is there.
    """
    text = "".join(chr(byte) if 32 <= byte < 127 else " " for byte in block)
    return " ".join(text.split())


def probe_plc(prober: PLCProbeClient, db_number: int, label: str) -> PLCProbe:
    """Answer whether a DB is really an SP-CLP DB. Reads only, never writes.

    The steps are reported separately on purpose: "the PLC did not answer" and
    "the PLC answered but that DB is not ours" send the customer to different
    places, and a wrong DB number would otherwise show plausible nonsense values
    on the dashboard.
    """
    try:
        prober.open()
    except Exception as error:
        return PLCProbe(
            PROBE_UNREACHABLE,
            f"Não consegui falar com o CLP em {label}. Confira o IP, a rede e se o CLP "
            f"aceita conexão no rack {DEFAULT_RACK} / slot {DEFAULT_SLOT}.",
            describe_error(error),
        )
    try:
        try:
            block = bytes(prober.read_block(db_number, SIGNATURE_OFFSET, len(SIGNATURE)))
        except Exception as error:
            return PLCProbe(
                PROBE_MISSING,
                f"O CLP respondeu, mas a leitura da DB {db_number} foi recusada. Confira o "
                "número do DB e se o bloco não está com acesso otimizado.",
                describe_error(error),
            )
        if block == SIGNATURE:
            return PLCProbe(
                PROBE_READY,
                f"DB {db_number} verificada: assinatura {SIGNATURE_TEXT} encontrada.",
            )
        # What is actually written in the block is the actionable part here, so it
        # goes in the message and not only in the technical detail.
        found = readable_signature(block)
        return PLCProbe(
            PROBE_UNSIGNED,
            f"O CLP respondeu, mas a DB {db_number} não tem a assinatura do SP-CLP. "
            f"Encontrado: {found or 'vazio'}; esperado: {SIGNATURE_TEXT}.",
            found or None,
        )
    finally:
        try:
            prober.close()
        except Exception:
            # A socket that refuses to close must not hide the scan result.
            pass


class Snap7ProbeClient:
    """Short lived connection used only by the configuration scan.

    The polling clients stay connected for the whole session; this one opens,
    reads and closes, so a wrong IP leaves no half open socket behind and the
    machine already being polled is never disturbed.
    """

    def __init__(self, ip: str, rack: int = DEFAULT_RACK, slot: int = DEFAULT_SLOT,
                 timeout_ms: int = PROBE_TIMEOUT_MS) -> None:
        self.ip, self.rack, self.slot, self.timeout_ms = ip, rack, slot, timeout_ms
        self._client = None

    def open(self) -> None:
        import snap7
        from snap7.error import check_error
        from snap7.type import Parameter

        client = snap7.client.Client()
        for parameter in (Parameter.PingTimeout, Parameter.SendTimeout, Parameter.RecvTimeout):
            # Without this the library waits for the OS default, which is far
            # longer than an operator will wait for a button click.
            check_error(client.set_param(parameter, self.timeout_ms))
        client.connect(self.ip, self.rack, self.slot)
        self._client = client

    def read_block(self, db_number: int, start: int, size: int) -> bytes:
        return bytes(self._client.db_read(db_number, start, size))

    def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            client.disconnect()


class FakePLCProbeClient:
    """Simulator for the scan, so the flow can be explored with no PLC on the desk."""

    def __init__(self, signature: bytes = SIGNATURE, reachable: bool = True,
                 databases: tuple[int, ...] = (1,)) -> None:
        self.signature = signature
        self.reachable = reachable
        self.databases = databases

    def open(self) -> None:
        if not self.reachable:
            raise ConnectionError("Fake PLC unreachable")

    def read_block(self, db_number: int, start: int, size: int) -> bytes:
        if db_number not in self.databases:
            raise RuntimeError("CPU : Address out of range")
        return self.signature[:size].ljust(size, b"\x00")

    def close(self) -> None:
        pass
