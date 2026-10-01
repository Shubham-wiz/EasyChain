"""Cron schedules: ``minute hour day-of-month month day-of-week``, in a time zone.

Supports ``*``, numbers, ranges (``1-5``), steps (``*/15``, ``0-30/10``), lists (``1,15``),
month and weekday names (``jan``, ``mon``), and the shortcuts ``@hourly``, ``@daily``,
``@weekly``, ``@monthly`` and ``@yearly``. As in standard cron, when both day fields are
restricted a day matches if either does.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SHORTCUTS = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
DAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
# (name, low, high, names)
FIELDS = [
    ("minute", 0, 59, None),
    ("hour", 0, 23, None),
    ("day of month", 1, 31, None),
    ("month", 1, 12, MONTHS),
    ("day of week", 0, 7, DAYS),
]


class CronError(ValueError):
    pass


def _value(text: str, low: int, names: list[str] | None, label: str) -> int:
    text = text.strip().lower()
    if names and text[:3] in names and not text.isdigit():
        return names.index(text[:3]) + (1 if label == "month" else 0)
    if not text.isdigit():
        raise CronError(f"“{text}” isn't a valid {label}.")
    return int(text)


def _field(text: str, low: int, high: int, names: list[str] | None, label: str) -> set[int]:
    values: set[int] = set()
    for part in text.split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            if not step_text.isdigit() or int(step_text) == 0:
                raise CronError(f"“/{step_text}” isn't a valid step for the {label}.")
            step = int(step_text)
        if part in ("*", ""):
            start, end = low, high
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = _value(a, low, names, label), _value(b, low, names, label)
        else:
            start = _value(part, low, names, label)
            end = high if step > 1 else start
        if not (low <= start <= high and low <= end <= high) or start > end:
            raise CronError(f"The {label} must be between {low} and {high}.")
        values.update(range(start, end + 1, step))
    if label == "day of week" and 7 in values:
        values.discard(7)
        values.add(0)
    return values


@dataclass(frozen=True)
class Cron:
    expression: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    day_restricted: bool
    weekday_restricted: bool

    @classmethod
    def parse(cls, expression: str) -> Cron:
        text = SHORTCUTS.get(expression.strip().lower(), expression.strip())
        parts = text.split()
        if len(parts) != 5:
            raise CronError(
                "A schedule has five parts: minute hour day-of-month month day-of-week "
                "(for example “0 9 * * mon-fri” is 9:00 on weekdays)."
            )
        sets = [
            _field(p, low, high, names, label)
            for p, (label, low, high, names) in zip(parts, FIELDS, strict=True)
        ]
        return cls(
            expression,
            frozenset(sets[0]),
            frozenset(sets[1]),
            frozenset(sets[2]),
            frozenset(sets[3]),
            frozenset(sets[4]),
            parts[2] != "*",
            parts[4] != "*",
        )

    def matches(self, moment: datetime) -> bool:
        if moment.minute not in self.minutes or moment.hour not in self.hours:
            return False
        if moment.month not in self.months:
            return False
        day_ok = moment.day in self.days
        weekday_ok = (moment.isoweekday() % 7) in self.weekdays
        if self.day_restricted and self.weekday_restricted:
            return day_ok or weekday_ok
        return day_ok and weekday_ok

    def next_after(self, moment: datetime) -> datetime:
        """The first matching minute strictly after ``moment`` (in its time zone)."""
        candidate = moment.replace(second=0, microsecond=0) + timedelta(minutes=1)
        # Jump by days and hours where possible; four years covers every valid schedule.
        limit = candidate + timedelta(days=366 * 4 + 1)
        while candidate < limit:
            if candidate.month not in self.months:
                candidate = (
                    candidate.replace(day=1, hour=0, minute=0) + timedelta(days=32)
                ).replace(day=1)
                continue
            day_ok = candidate.day in self.days
            weekday_ok = (candidate.isoweekday() % 7) in self.weekdays
            both = self.day_restricted and self.weekday_restricted
            if not ((day_ok or weekday_ok) if both else (day_ok and weekday_ok)):
                candidate = candidate.replace(hour=0, minute=0) + timedelta(days=1)
                continue
            if candidate.hour not in self.hours:
                candidate = candidate.replace(minute=0) + timedelta(hours=1)
                continue
            if candidate.minute not in self.minutes:
                candidate += timedelta(minutes=1)
                continue
            return candidate
        raise CronError(f"“{self.expression}” never happens.")


def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        raise CronError(f"“{name}” isn't a time zone I know, such as Europe/London.") from None


def next_fire(expression: str, timezone: str | None, after: float) -> float:
    """Epoch seconds of the next run after ``after`` (epoch seconds)."""
    tz = zone(timezone)
    moment = datetime.fromtimestamp(after, tz)
    nxt = Cron.parse(expression).next_after(moment)
    # Re-attach the zone so daylight-saving changes resolve to the right instant.
    return nxt.replace(tzinfo=tz).timestamp()


def describe(expression: str) -> str:
    """A short plain-language reading of common schedules."""
    text = SHORTCUTS.get(expression.strip().lower(), expression.strip())
    parts = text.split()
    if len(parts) != 5:
        return expression
    minute, hour, dom, month, dow = parts
    if text == "* * * * *":
        return "Every minute"
    if minute.startswith("*/") and hour == dom == month == dow == "*":
        return f"Every {minute[2:]} minutes"
    if minute.isdigit() and hour == dom == month == dow == "*":
        return f"Every hour at :{int(minute):02d}"
    if minute.isdigit() and hour.isdigit() and dom == month == "*":
        at = f"{int(hour):02d}:{int(minute):02d}"
        if dow == "*":
            return f"Every day at {at}"
        if dow.lower() in ("1-5", "mon-fri"):
            return f"Weekdays at {at}"
        return f"At {at} on days {dow}"
    return expression
