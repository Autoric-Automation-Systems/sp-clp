from __future__ import annotations

import logging
import struct
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol


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
    type: str = "BOOL"


# Standard contract, which is exactly the layout the PLC block declares: the 16
# BOOLs fill 0.0 through 1.7 and the three DInts sit at 2.0, 6.0 and 10.0. The
# first five are the standard signals (AUTO, RUN, FAULT, SAFETY, COUNTER); their
# labels are part of the contract and cannot be renamed.
SIGNAL_LAYOUT: tuple[SignalSpec, ...] = (
    SignalSpec("0.0", "auto", "Automático"),
    SignalSpec("0.1", "run", "Produção"),
    SignalSpec("0.2", "fault", "Falha"),
    SignalSpec("0.3", "safety", "Segurança"),
    SignalSpec("0.4", "counter", "Contador"),
    *(SignalSpec(f"0.{bit}", "custom", f"Sinal 0.{bit}") for bit in range(5, 8)),
    *(SignalSpec(f"1.{bit}", "custom", f"Sinal 1.{bit}") for bit in range(8)),
    SignalSpec("2.0", "integer", "Int_1", "DINT"),
    SignalSpec("6.0", "integer", "Int_2", "DINT"),
    SignalSpec("10.0", "integer", "Int_3", "DINT"),
)

BOOL_LAYOUT = tuple(spec for spec in SIGNAL_LAYOUT if spec.type == "BOOL")
DINT_LAYOUT = tuple(spec for spec in SIGNAL_LAYOUT if spec.type == "DINT")
DINT_SIZE = 4

SIGNAL_ADDRESSES = [spec.address for spec in SIGNAL_LAYOUT]
KNOWN_ADDRESSES = frozenset(SIGNAL_ADDRESSES)
# The five standard signals drive the dashboard blocks and the hourly totals, so
# their labels belong to the contract. Every other signal accepts a user label.
LOCKED_KINDS = frozenset({"auto", "run", "fault", "safety", "counter"})
EDITABLE_ADDRESSES = frozenset(
    spec.address for spec in SIGNAL_LAYOUT if spec.kind not in LOCKED_KINDS
)
LOCKED_ADDRESSES = frozenset(SIGNAL_ADDRESSES) - EDITABLE_ADDRESSES
SIGNAL_TYPES = {spec.address: spec.type for spec in SIGNAL_LAYOUT}

# Absolute layout of the customer DB, read from the block in the field:
#
#   DBX0.0-1.7   16 BOOL signals (Auto, Run, Fault, Safety, Counter, Signal_5..15)
#   DBD2.0-13.0  Int_1, Int_2 and Int_3, signed DInts with editable labels
#   DBX14.0-18.0 Type, ARRAY[0..4] OF CHAR = 'S','P','C','L','P'
#   DBD20.0      Count (DInt, big-endian)
#
# The signature is what the configuration scan looks for, so a wrong DB number is
# caught before the machine is saved. A CHAR array is used instead of a numeric
# magic number because it is readable in TIA Portal and has no byte order to get
# wrong. Byte 19 is the alignment gap before the counter.
SIGNATURE_OFFSET = 14
SIGNATURE_TEXT = "SPCLP"
SIGNATURE = SIGNATURE_TEXT.encode("ascii")

# The dashboard reads the block up to the counter in one go: status word,
# signature and counter all arrive together.
COUNT_OFFSET = 20
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
    integers: dict[str, int]
    count: int


class PLCClient(Protocol):
    def read(self, db_number: int) -> PLCReading: ...


def bit_value(data: bytes, address: str) -> bool:
    byte, bit = (int(part) for part in address.split("."))
    return bool(data[byte] & (1 << bit))


def signal_offset(address: str) -> int:
    """Byte offset of an address. The bit part only means something for a BOOL."""
    return int(address.split(".")[0])


def dint_value(data: bytes, address: str) -> int:
    offset = signal_offset(address)
    return struct.unpack(">i", data[offset:offset + DINT_SIZE])[0]


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
        bits={spec.address: bit_value(data, spec.address) for spec in SIGNAL_LAYOUT},        integers={spec.address: dint_value(data, spec.address) for spec in DINT_LAYOUT},        count=struct.unpack(">i", data[COUNT_OFFSET:COUNT_OFFSET + COUNT_SIZE])[0],
    )


class FakePLCClient:
    def __init__(self, count: int = 1250, connected: bool = True) -> None:
        self.count = count
        self.connected = connected

    def read(self, db_number: int) -> PLCReading:
        if not self.connected:
            raise ConnectionError("Fake PLC disconnected")
        data = bytearray(READ_SIZE)
        # Byte 0 carries AUTO (0.0), RUN (0.1), SAFETY (0.3) and the counter (0.4);
        # FAULT (0.2) stays clear.
        data[0] = 0b00011011
        data[2:6] = (1500).to_bytes(DINT_SIZE, "big", signed=True)
        data[6:10] = (42).to_bytes(DINT_SIZE, "big", signed=True)
        data[10:14] = (7).to_bytes(DINT_SIZE, "big", signed=True)
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


# The sweep tells three different mistakes apart, because each one is fixed in a
# different place: the cable and the IP, the DB range, or the block contents.
PROBE_READY = "ready"
PROBE_UNSIGNED = "unsigned"
PROBE_UNREACHABLE = "unreachable"

# Long enough for a plant network, short enough that one click cannot hang the
# request thread while a machine with no PLC at that address is being scanned.
PROBE_TIMEOUT_MS = 2000

# The configuration scan walks the DB numbers looking for the signature block.
DEFAULT_DB_FIRST = 1
DEFAULT_DB_LAST = 1000
# Bounds so one request cannot pin a worker thread for minutes.
MAX_DB_RANGE = 2000
SCAN_DEADLINE_S = 25.0
# How many DB numbers are spelled out in the summary message.
REPORTED_DB_LIMIT = 10


@dataclass(frozen=True)
class DBScan:
    status: str
    message: str
    databases: tuple[int, ...] = ()
    scanned: int = 0
    answered: int = 0
    truncated: bool = False
    detail: str | None = None


class PLCProbeClient(Protocol):
    """What the sweep needs from a transport, so it can be faked without a PLC."""

    def open(self) -> None: ...

    def read_block(self, db_number: int, start: int, size: int) -> bytes: ...

    def connected(self) -> bool: ...

    def close(self) -> None: ...


def readable_signature(block: bytes) -> str:
    """Return only the text characters found in a block.

    A wrong offset usually points at numbers, and showing those bytes as text
    would print gibberish instead of admitting that nothing readable is there.
    """
    text = "".join(chr(byte) if 32 <= byte < 127 else " " for byte in block)
    return " ".join(text.split())


def scan_databases(
    prober: PLCProbeClient,
    label: str,
    first: int = DEFAULT_DB_FIRST,
    last: int = DEFAULT_DB_LAST,
    deadline_s: float = SCAN_DEADLINE_S,
    clock: Callable[[], float] = time.monotonic,
) -> DBScan:
    """Walk the DB numbers of one PLC and report which ones carry the signature.

    Reads only, never writes. One round trip per DB is slower than a multi var
    read, but every answer stays unambiguous: a DB that does not exist, or is too
    short, refuses the read, and nothing is inferred from a response the PLC is
    allowed to reject.
    """
    try:
        prober.open()
    except Exception as error:
        return DBScan(
            PROBE_UNREACHABLE,
            f"Não consegui falar com o CLP em {label}. Confira o IP, a rede e se o CLP "
            f"aceita conexão no rack {DEFAULT_RACK} / slot {DEFAULT_SLOT}.",
            detail=describe_error(error),
        )

    started = clock()
    found: list[int] = []
    scanned = 0
    answered = 0
    sample: tuple[int, bytes] | None = None
    truncated = False
    lost: str | None = None
    try:
        for number in range(first, last + 1):
            if clock() - started >= deadline_s:
                truncated = True
                break
            scanned += 1
            try:
                block = bytes(prober.read_block(number, SIGNATURE_OFFSET, len(SIGNATURE)))
            except Exception as error:
                # A read fails either because that DB is not there or because the
                # link died, and only the second one is worth stopping for.
                if not prober.connected():
                    lost = describe_error(error)
                    truncated = True
                    break
                continue
            answered += 1
            if block == SIGNATURE:
                found.append(number)
            elif sample is None:
                # Kept so a range full of foreign DBs can say what it did find.
                sample = (number, block)
    finally:
        try:
            prober.close()
        except Exception:
            # A socket that refuses to close must not hide the sweep result.
            pass

    if lost is not None:
        message = f"A conexão com o CLP caiu durante a varredura, no DB {scanned}."
        if found:
            message += f" {len(found)} DB já haviam sido confirmados e estão listados."
        return DBScan(
            PROBE_UNREACHABLE, message, tuple(found), scanned, answered,
            truncated=True, detail=lost,
        )

    if found:
        listed = ", ".join(str(number) for number in found[:REPORTED_DB_LIMIT])
        if len(found) > REPORTED_DB_LIMIT:
            listed += f" e mais {len(found) - REPORTED_DB_LIMIT}"
        message = f"Assinatura {SIGNATURE_TEXT} em {len(found)} DB: {listed}."
        if truncated:
            # A partial sweep must never read as the whole answer.
            message += " A varredura foi interrompida antes do fim da faixa."
        return DBScan(PROBE_READY, message, tuple(found), scanned, answered, truncated)

    detail = None
    if answered and sample is not None:
        number, block = sample
        detail = f"DB {number}: {readable_signature(block) or 'vazio'}"
    if answered:
        message = (
            f"O CLP respondeu, mas nenhuma DB entre {first} e {last} tem a assinatura "
            f"{SIGNATURE_TEXT} no byte {SIGNATURE_OFFSET}. {answered} DBs aceitaram a leitura."
        )
    else:
        message = (
            f"O CLP respondeu, mas nenhuma DB entre {first} e {last} aceitou a leitura. "
            "Confira a faixa de números usada no CLP."
        )
    if truncated:
        message += " A varredura foi interrompida antes do fim da faixa."
    return DBScan(PROBE_UNSIGNED, message, (), scanned, answered, truncated, detail)


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

    def connected(self) -> bool:
        # Not exact: the library warns this can still say True on a dead link, so
        # it is only used to tell a missing DB apart from a lost connection.
        return self._client is not None and self._client.get_connected()

    def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            client.disconnect()


class FakePLCProbeClient:
    """Simulator for the sweep, so the flow can be explored with no PLC on the desk."""

    def __init__(self, signature: bytes = SIGNATURE, reachable: bool = True,
                 databases: tuple[int, ...] = (1,), drop_at: int | None = None) -> None:
        self.signature = signature
        self.reachable = reachable
        self.databases = databases
        # DB number where the simulated link dies, for the partial sweep tests.
        self.drop_at = drop_at
        self._lost = False

    def open(self) -> None:
        if not self.reachable:
            raise ConnectionError("Fake PLC unreachable")
        self._lost = False

    def read_block(self, db_number: int, start: int, size: int) -> bytes:
        if self._lost or (self.drop_at is not None and db_number >= self.drop_at):
            self._lost = True
            raise ConnectionError("Fake PLC connection lost")
        if db_number not in self.databases:
            raise RuntimeError("CPU : Address out of range")
        return self.signature[:size].ljust(size, b"\x00")

    def connected(self) -> bool:
        return self.reachable and not self._lost

    def close(self) -> None:
        pass
