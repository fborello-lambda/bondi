"""A small 5-field cron matcher (minute hour day-of-month month day-of-week), evaluated in local time."""

from datetime import datetime

RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
NAMES = ["minute", "hour", "day of month", "month", "day of week"]


class CronError(ValueError):
    pass


def _field(text: str, lo: int, hi: int, what: str) -> set[int]:
    values: set[int] = set()
    for part in text.split(","):
        base, _, step = part.partition("/")
        if step and (not step.isdigit() or int(step) == 0):
            raise CronError(f"bad step in the {what} field: '{part}'")
        if base == "*":
            a, b = lo, hi
        elif "-" in base:
            x, _, y = base.partition("-")
            if not (x.isdigit() and y.isdigit()):
                raise CronError(f"bad range in the {what} field: '{part}'")
            a, b = int(x), int(y)
        elif base.isdigit():
            a = b = int(base)
            if step:
                b = hi
        else:
            raise CronError(f"bad value in the {what} field: '{part}'")
        if not (lo <= a <= b <= hi):
            raise CronError(f"the {what} field allows {lo}-{hi}, got '{part}'")
        values |= set(range(a, b + 1, int(step) if step else 1))
    return values


def parse_cron(expr: str) -> list[set[int]]:
    fields = expr.split()
    if len(fields) != 5:
        raise CronError(f"schedule '{expr}' needs 5 fields: minute hour day-of-month month day-of-week")
    parsed = [_field(f, lo, hi, n) for f, (lo, hi), n in zip(fields, RANGES, NAMES)]
    if 7 in parsed[4]:
        parsed[4] = (parsed[4] - {7}) | {0}
    return parsed


def matches(expr: str, when: datetime) -> bool:
    minute, hour, dom, month, dow = parse_cron(expr)
    fields = expr.split()
    day_ok_dom = when.day in dom
    day_ok_dow = (when.isoweekday() % 7) in dow
    # Standard cron: when both day fields are restricted, either one matching is enough.
    if fields[2] != "*" and fields[4] != "*":
        day_ok = day_ok_dom or day_ok_dow
    else:
        day_ok = day_ok_dom and day_ok_dow
    return when.minute in minute and when.hour in hour and when.month in month and day_ok
