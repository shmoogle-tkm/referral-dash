"""The date range a report covers, and the earlier range its change numbers compare with.

One Tableau download covers both ranges. Each is then measured as it stood at the
end of its range, so the comparison matches what a report made back then said.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from .group import TZ_LOOKUP

MAX_DAYS = 365


def mission_zone(settings):
    return TZ_LOOKUP[settings["timezones"]["default"]]


def default_range(settings, now=None):
    """The last `lookback_days` days, ending today in the mission's time zone."""
    today = (now or datetime.now(timezone.utc)).astimezone(mission_zone(settings)).date()
    return today - timedelta(days=settings["lookback_days"]), today


def _day(value, label):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as error:
        raise ValueError(f"Choose a valid {label} date.") from error


def _check_range(first, last, today, label):
    if first > last:
        raise ValueError(f"The {label} start date must be on or before its end date.")
    if last > today:
        raise ValueError(f"The {label} end date cannot be in the future.")
    if (last - first).days > MAX_DAYS:
        raise ValueError(f"Choose a {label} range of {MAX_DAYS} days or fewer.")


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    cutoff: datetime  # The moment, in UTC, this range is measured at.
    zone: object
    baseline_start: date
    baseline_end: date

    @property
    def baseline_cutoff(self):
        return self.cutoff - (self.end - self.baseline_end)

    @property
    def download_start(self):
        """The download reaches back to whichever range starts first."""
        return min(self.start, self.baseline_start)


def make_period(settings, start=None, end=None, compare_start=None, compare_end=None, now=None):
    """Validate the ranges. Missing report dates use the default range; a missing
    comparison uses the same range `compare_days` earlier."""
    now = now or datetime.now(timezone.utc)
    zone = mission_zone(settings)
    default_start, today = default_range(settings, now)
    start = _day(start, "start") if start else default_start
    end = _day(end, "end") if end else today
    _check_range(start, end, today, "report")
    if bool(compare_start) != bool(compare_end):
        raise ValueError("Choose both a start and an end date to compare with.")
    earlier = timedelta(days=settings["compare_days"])
    baseline_start = _day(compare_start, "comparison start") if compare_start else start - earlier
    baseline_end = _day(compare_end, "comparison end") if compare_end else end - earlier
    _check_range(baseline_start, baseline_end, today, "comparison")
    if baseline_end >= end:
        raise ValueError("The comparison dates must end before the report's end date.")
    end_of_day = datetime.combine(end + timedelta(days=1), time.min, tzinfo=zone) - timedelta(seconds=1)
    return Period(start, end, min(end_of_day.astimezone(timezone.utc), now.astimezone(timezone.utc)), zone,
                  baseline_start, baseline_end)


def summary_period(period, settings):
    """What district summaries cover: the report's last `summary_days`, against the matching days earlier.

    With a fixed comparison range, that range is used as it is. The report's download already reaches
    back far enough, because the summary's comparison never starts before the report's.
    """
    start = max(period.start, period.end - timedelta(days=settings["summary_days"] - 1))
    # A chosen comparison range is any comparison that isn't simply the report moved back compare_days.
    chosen = settings["compare_mode"] == "range" or period.end - period.baseline_end != timedelta(days=settings["compare_days"])
    if chosen:
        return Period(start, period.end, period.cutoff, period.zone, period.baseline_start, period.baseline_end)
    shift = period.end - period.baseline_end
    return Period(start, period.end, period.cutoff, period.zone, start - shift, period.baseline_end)


def settings_period(settings, now=None):
    """The period the saved date settings describe."""
    report = (settings["report_start"], settings["report_end"]) if settings["report_mode"] == "range" else (None, None)
    compare = (settings["compare_start"], settings["compare_end"]) if settings["compare_mode"] == "range" else (None, None)
    return make_period(settings, *report, *compare, now=now)
