"""Reading the PLCs on a schedule, so the history does not depend on a browser.

The recordings used to be a side effect of the status endpoint, which the page calls
while it is open. Close the panel and the plant stops being recorded, which is fine
for a screen somebody watches and useless for a machine that runs at night. The
recorder takes that job over: one thread, the whole list of machines, one read each,
for as long as the application runs.

It only reads. Nothing here writes to a PLC, and the count delta keeps the same
policy as before: a value that went down starts a new baseline instead of producing
a negative.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from .plc import READ_ERRORS, SIGNAL_LAYOUT, describe_error
from .storage import Storage

DEFAULT_INTERVAL_SECONDS = 5.0
MIN_INTERVAL_SECONDS = 1.0

# How much history the panel keeps, for counts and for signals alike. The courtesy
# version is not a historian; the window is short on purpose.
RETENTION_DAYS = 7
PRUNE_EVERY_SECONDS = 3600.0

# The bits the trend screen draws. COUNTER is a diagnostic pulse, so a bar of it
# would be noise, and the free signals belong to the customer's own vocabulary.
RECORDED_KINDS = ("auto", "run", "fault", "safety")

logger = logging.getLogger("sp_clp.recorder")


def interval_seconds() -> float:
    """How long to wait between two reads, from SP_CLP_POLL_SECONDS."""
    try:
        configured = float(os.environ.get("SP_CLP_POLL_SECONDS") or DEFAULT_INTERVAL_SECONDS)
    except ValueError:
        return DEFAULT_INTERVAL_SECONDS
    return max(MIN_INTERVAL_SECONDS, configured)


def recorded_addresses() -> list[str]:
    return [spec.address for spec in SIGNAL_LAYOUT if spec.kind in RECORDED_KINDS]


def record_once(storage: Storage, client_for, now: datetime | None = None) -> int:
    """One pass over every machine: read it, keep the count and the changes.

    Returns how many machines answered, which is what a test can assert on without
    waiting for a clock.
    """
    moment = now or datetime.now(timezone.utc)
    answered = 0
    addresses = recorded_addresses()
    for machine in storage.list_machines():
        try:
            reading = client_for(machine).read(machine["db_number"])
        except READ_ERRORS as error:
            # A PLC that is off or a cable that is out is routine here; the next
            # cycle tries again and the day chart shows the hole.
            logger.debug("Leitura de %s falhou: %s", machine["name"], describe_error(error))
            continue
        storage.save_sample(machine["id"], reading.count, reading.timestamp)
        storage.save_transitions(
            machine["id"],
            {address: reading.bits[address] for address in addresses},
            reading.timestamp,
        )
        answered += 1
    return answered


def _run(storage: Storage, client_for, stop: threading.Event, interval: float) -> None:
    next_prune = 0.0
    while not stop.is_set():
        try:
            record_once(storage, client_for)
        except Exception:  # noqa: BLE001 - the thread has to outlive any surprise
            logger.exception("Falha no ciclo de leitura")
        if time.monotonic() >= next_prune:
            try:
                limit = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
                removed = storage.prune(limit)
                if removed:
                    logger.info("Historico: %d linha(s) fora dos %d dias.", removed, RETENTION_DAYS)
            except Exception:  # noqa: BLE001
                logger.exception("Falha ao limpar o historico")
            next_prune = time.monotonic() + PRUNE_EVERY_SECONDS
        stop.wait(interval)


def start(
    storage: Storage,
    client_for,
    interval: float | None = None,
    stop: threading.Event | None = None,
) -> tuple[threading.Thread, threading.Event]:
    """Start the recording thread; set the returned event to end it."""
    halt = stop or threading.Event()
    thread = threading.Thread(
        target=_run,
        args=(storage, client_for, halt, interval or interval_seconds()),
        name="sp-clp-recorder",
        daemon=True,
    )
    thread.start()
    return thread, halt
