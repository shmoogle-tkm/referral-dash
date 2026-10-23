from datetime import datetime, time, timedelta
import os
import sys
from zoneinfo import ZoneInfo
import json

# ======================
# TIMEZONES
# ======================
UTC = ZoneInfo("UTC")
EST = ZoneInfo("America/New_York")
CST = ZoneInfo("America/Chicago")
MST = ZoneInfo("America/Denver")
MST_NO_DST = ZoneInfo("America/Phoenix")
PST = ZoneInfo("America/Los_Angeles")
AKST = ZoneInfo("America/Anchorage")
HST = ZoneInfo("Pacific/Honolulu")

TZ_LOOKUP = {
    "UTC": UTC,
    "EST": EST,
    "CST": CST,
    "MST": MST,
    "MST_NO_DST": MST_NO_DST,
    "PST": PST,
    "AKST": AKST,
    "HST": HST,
}

DATE_FORMAT = "%m/%d/%Y %I:%M:%S %p"


# ======================
# STATS MIXIN
# ======================
class StatsMixin:
    def calculate_from_people(self, people):
        # -----------------------
        # BASIC COUNTS
        # -----------------------
        self.total_referrals = len(people)

        self.total_contacted = sum(1 for p in people if p.has_contact_attempt)
        self.total_successful_contact = sum(1 for p in people if p.has_successful_contact)
        self.total_attended_church = sum(1 for p in people if p.has_attended_church)
        self.total_found = sum(1 for p in people if p.is_found)

        # -----------------------
        # RAW LISTS
        # -----------------------
        self.referrals_without_contact_attempt = [
            p for p in people if p.no_contact_attempt
        ]

        # -----------------------
        # PERCENTAGES
        # -----------------------
        def pct(part, whole):
            return round((part / whole) * 100, 2) if whole else 0

        self.percent_contacted = pct(self.total_contacted, self.total_referrals)
        self.percent_successful_contact = pct(self.total_successful_contact, self.total_referrals)
        self.percent_attended_church = pct(self.total_attended_church, self.total_found)
        self.percent_found = pct(self.total_found, self.total_referrals)

        # -----------------------
        # RESPONSE SPEED STATS
        # -----------------------
        speeds = [p.contact_speed for p in people if p.contact_speed is not None]

        if not speeds:
            self.average_response_time = None
            self.median_response_time = None
            self.first_quartile_response_time = None
            self.third_quartile_response_time = None
            self.percent_contacted_within_5_min = 0
            return

        speeds.sort()

        self.average_response_time = sum(speeds) / len(speeds)
        self.median_response_time = self._median(speeds)

        q1, q3 = self._quartiles(speeds)
        self.first_quartile_response_time = q1
        self.third_quartile_response_time = q3

        self.percent_contacted_within_5_min = pct(
            sum(1 for s in speeds if s <= 5),
            len(speeds)
        )

    def _median(self, values):
        n = len(values)
        mid = n // 2
        return values[mid] if n % 2 else (values[mid - 1] + values[mid]) / 2

    def _quartiles(self, values):
        n = len(values)
        if n < 2:
            return None, None

        mid = n // 2
        lower = values[:mid]
        upper = values[mid:] if n % 2 == 0 else values[mid + 1:]

        return self._median(lower), self._median(upper)


# ======================
# REFERRAL PERSON
# ======================
class ReferralPerson:
    def __init__(self, stats=None, settings=None):
        self.stats = stats if stats else {}
        from .settings import load_settings
        self.settings = settings if settings is not None else load_settings()

        self.zone = self.stats.get("Latest Zone Name")
        self.district = self.stats.get("Latest District Name")
        self.area = self.stats.get("Latest Teaching Area Name")
        self.name = self.stats.get("Full Name")

        self.first_referral_event_date = self.stats.get("First Referral Event Date")
        self.first_contact_attempt_event_date = self.stats.get("First Contact Attempt Event Date")
        self.first_successful_contact_attempt_date = self.stats.get("First Successful Contact Attempt Event Date")
        self.first_new_person_being_taught_date = self.stats.get("First New Person Being Taught Date")
        self.first_sacrament_date = self.stats.get("First Sacrament Date")
        self.latest_sacrament_date = self.stats.get("Latest Sacrament Date")

        self.contact_speed = self.get_referral_speed()

    # -----------------------
    # FUNNEL FLAGS
    # -----------------------
    @property
    def has_contact_attempt(self):
        return bool(self.first_contact_attempt_event_date)

    @property
    def has_successful_contact(self):
        return bool(self.first_successful_contact_attempt_date)

    @property
    def has_attended_church(self):
        return bool(self.first_sacrament_date or self.latest_sacrament_date)

    @property
    def is_found(self):
        return bool(self.first_new_person_being_taught_date)

    @property
    def no_contact_attempt(self):
        return not self.has_contact_attempt

    # -----------------------
    # RESPONSE SPEED
    # -----------------------
    def get_user_path(self):
        if getattr(sys, 'frozen', False):
            return os.path.dirname(sys.executable)
        return os.path.dirname(os.path.abspath(__file__))

    def get_referral_speed(self):
        if not self.first_referral_event_date:
            return None

        if not self.first_contact_attempt_event_date:
            contact_time_raw = datetime.now(UTC).strftime(DATE_FORMAT)
        else:
            contact_time_raw = self.first_contact_attempt_event_date

        # with open("timezones.json", "r", encoding="utf-8") as f:
        #     timezone_keys = json.load(f)

        timezone_keys = self.settings['timezones']

        tz_key = timezone_keys.get(self.zone.strip().casefold(), timezone_keys["default"])
        tz = TZ_LOOKUP[tz_key]

        received = datetime.strptime(
            self.first_referral_event_date, DATE_FORMAT
        ).replace(tzinfo=UTC).astimezone(tz)

        contacted = datetime.strptime(
            contact_time_raw, DATE_FORMAT
        ).replace(tzinfo=UTC).astimezone(tz)

        def validate_time_input(value: str, default: str) -> str:
            try:
                value = value.strip()
                hour, minute = map(int, value.split(":"))
                time(hour, minute)
                return f"{hour:02d}:{minute:02d}"
            except Exception:
                return default

        def load_time_settings():
            defaults = {
                "cutoff_time": time(22, 0),
                "start_time": time(6, 30)
            }

            try:
                settings = self.settings

                cutoff_str = validate_time_input(
                    settings.get("cutoff_time", "22:00"),
                    "22:00"
                )

                start_str = validate_time_input(
                    settings.get("start_time", "06:30"),
                    "06:30"
                )

                cutoff_hour, cutoff_min = map(int, cutoff_str.split(":"))
                start_hour, start_min = map(int, start_str.split(":"))

                return (
                    time(cutoff_hour, cutoff_min),
                    time(start_hour, start_min)
                )

            except Exception:
                return (
                    defaults["cutoff_time"],
                    defaults["start_time"]
                )

        CUTOFF_TIME, START_TIME = load_time_settings()


        # CUTOFF_TIME = time(22, 0)
        # START_TIME = time(6, 30)

        start_time = received

        if received.time() >= CUTOFF_TIME:
            start_time = datetime.combine(
                received.date() + timedelta(days=1),
                START_TIME,
                tzinfo=tz
            )
        elif received.time() < START_TIME:
            start_time = datetime.combine(
                received.date(),
                START_TIME,
                tzinfo=tz
            )

        if contacted < start_time:
            return None

        delta = contacted - start_time
        return round(delta.total_seconds() / 60, 2)


# ======================
# ORG STRUCTURE
# ======================
class Area(StatsMixin):
    def __init__(self, name, parent):
        self.name = name
        self.parent = parent
        self.people = []
        self.calculate_from_people([])

    def add_person(self, person):
        self.people.append(person)
        self.calculate_from_people(self.people)
        self.parent.update_stats()


class District(StatsMixin):
    def __init__(self, name, parent):
        self.name = name
        self.parent = parent
        self.areas = {}
        self.calculate_from_people([])

    def update_stats(self):
        people = []
        for area in self.areas.values():
            people.extend(area.people)
        self.calculate_from_people(people)
        self.parent.update_stats()


class Zone(StatsMixin):
    def __init__(self, name, parent):
        self.name = name
        self.parent = parent
        self.districts = {}
        self.calculate_from_people([])

    def update_stats(self):
        people = []
        for district in self.districts.values():
            for area in district.areas.values():
                people.extend(area.people)
        self.calculate_from_people(people)
        self.parent.update_stats()


class Mission(StatsMixin):
    def __init__(self):
        self.zones = {}
        self.calculate_from_people([])

    def update_stats(self):
        people = []
        for zone in self.zones.values():
            for district in zone.districts.values():
                for area in district.areas.values():
                    people.extend(area.people)
        self.calculate_from_people(people)

    def add_person(self, person):
        zone = self.zones.setdefault(person.zone, Zone(person.zone, self))
        district = zone.districts.setdefault(person.district, District(person.district, zone))
        area = district.areas.setdefault(person.area, Area(person.area, district))
        area.add_person(person)
