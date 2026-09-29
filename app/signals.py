"""Turning the stored signal events into the bars the trend screen draws.

The panel reads the PLC every few seconds and stores a row only when a bit moves,
so the state between two readings close together is known, and the state across a
stretch with no reading at all is not. Keeping those two apart is the whole point
of this module: a machine that stopped and a panel that was switched off must never
look the same on screen.

Readings come from ``count_samples``, which the recorder writes on every successful
read, so the counter table doubles as the heartbeat of the wiring.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

# How far apart two readings may be and still count as the panel watching. Three
# times the 5 s the recorder waits, which covers a slow read without turning one
# missed cycle into a hole in the day.
MATCH_TOLERANCE = timedelta(seconds=20)


@dataclass(frozen=True)
class Segment:
    start: datetime
    end: datetime
    # None means nobody was reading here, which is not the same as the bit at 0.
    value: bool | None

    @property
    def seconds(self) -> int:
        return max(0, int((self.end - self.start).total_seconds()))


@dataclass(frozen=True)
class SignalDay:
    address: str
    segments: list[Segment]
    on_seconds: int
    off_seconds: int
    unknown_seconds: int
    # The part of the day that has already happened: the whole day for a past date,
    # and up to now for today, which is the denominator of the percentages.
    elapsed_seconds: int

    def percent(self, seconds: int) -> int:
        if self.elapsed_seconds <= 0:
            return 0
        return round(100 * seconds / self.elapsed_seconds)


def state_at(events: list[tuple[datetime, bool]], moment: datetime) -> bool | None:
    """The value in force at that instant, or None when nothing was recorded yet."""
    value: bool | None = None
    for changed_at, bit in events:
        if changed_at > moment:
            break
        value = bit
    return value


def _known_spans(
    samples: list[datetime], start: datetime, limit: datetime, tolerance: timedelta
) -> list[tuple[datetime, datetime]]:
    """Stretches where two readings are close enough to trust the state between."""
    spans: list[tuple[datetime, datetime]] = []
    ordered = sorted(moment for moment in samples if moment <= limit)
    for previous, following in zip(ordered, ordered[1:]):
        if following - previous <= tolerance:
            left, right = max(previous, start), min(following, limit)
            if right > left:
                spans.append((left, right))
    if ordered:
        # The newest reading still speaks for the moment being watched, as far as
        # trusting it goes.
        left = max(ordered[-1], start)
        right = min(limit, ordered[-1] + tolerance)
        if right > left:
            spans.append((left, right))
    return spans


def _fill_gaps(segments: list[Segment], start: datetime, limit: datetime) -> list[Segment]:
    """Close the stretches with nothing known, so the bar covers the whole day."""
    filled: list[Segment] = []
    cursor = start
    for segment in sorted(segments, key=lambda item: item.start):
        if segment.start > cursor:
            filled.append(Segment(cursor, segment.start, None))
        filled.append(segment)
        cursor = max(cursor, segment.end)
    if limit > cursor:
        filled.append(Segment(cursor, limit, None))
    return filled


def _merge(segments: list[Segment]) -> list[Segment]:
    """Glue neighbours that hold the same state.

    One reading is one interval to start with, so a day read every five seconds
    would arrive as seventeen thousand slivers with the same value. The bar needs
    the stretches, not the readings.
    """
    merged: list[Segment] = []
    for segment in segments:
        previous = merged[-1] if merged else None
        if previous is not None and previous.end == segment.start and previous.value == segment.value:
            merged[-1] = Segment(previous.start, segment.end, previous.value)
        else:
            merged.append(segment)
    return merged


def build_day(
    address: str,
    samples: list[datetime],
    events: list[tuple[datetime, bool]],
    start: datetime,
    end: datetime,
    now: datetime,
    tolerance: timedelta = MATCH_TOLERANCE,
) -> SignalDay:
    """One signal across one day, split into the stretches it held each state."""
    limit = min(now, end)
    segments: list[Segment] = []
    if limit > start:
        known = [
            Segment(left, right, state_at(events, left))
            for left, right in _known_spans(samples, start, limit, tolerance)
        ]
        segments = _merge(_fill_gaps(known, start, limit))

    on = off = unknown = 0
    for segment in segments:
        if segment.value is None:
            unknown += segment.seconds
        elif segment.value:
            on += segment.seconds
        else:
            off += segment.seconds
    return SignalDay(
        address=address,
        segments=segments,
        on_seconds=on,
        off_seconds=off,
        unknown_seconds=unknown,
        elapsed_seconds=max(0, int((limit - start).total_seconds())),
    )
