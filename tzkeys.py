"""UTC hour keys for a weekly availability block. Mirrors the helpers in index.html."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

WEEK = 10080


def tz_offsets(name):
    """Minutes east of UTC; two entries where the zone observes DST, one where it does not."""
    zone = ZoneInfo(name)
    year = datetime.now(timezone.utc).year
    probes = (datetime(year, 1, 15, tzinfo=zone), datetime(year, 7, 15, tzinfo=zone))
    return sorted({int(p.utcoffset().total_seconds() // 60) for p in probes})


def utc_hour_keys(start, end, offsets):
    keys = set()
    for offset in offsets:
        minute, until = (start - offset) // 60 * 60, end - offset
        while minute < until:
            u = minute % WEEK
            keys.add(f'{u // 1440}-{(u // 60) % 24:02d}')
            minute += 60
    return ','.join(sorted(keys))


def weekly_keys(day, start_minute, end_minute, tz):
    return utc_hour_keys(day * 1440 + start_minute, day * 1440 + end_minute, tz_offsets(tz))
