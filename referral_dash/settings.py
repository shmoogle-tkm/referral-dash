"""Mission settings, stored per Windows user so app updates keep them."""
import json
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

VERSION = "2.3.0"
APP_NAME = "Referral Dash"
TIMEZONES = ["UTC", "EST", "CST", "MST", "MST_NO_DST", "PST", "AKST", "HST"]
# The mission finding workbook used by the Referral Care Data Hub.
DEFAULT_TABLEAU_VIEW = ("https://prod-useast-b.online.tableau.com/t/churchofjesuschrist/views/"
                        "MissionFindingSummaryStephen/MissionFindingDetail")
DEFAULTS = {"mission": "Your Mission", "lightning": 5, "pepper": 20,
            "start_time": "06:30", "cutoff_time": "22:00", "timezones": {"default": "UTC"},
            "tableau_view_url": DEFAULT_TABLEAU_VIEW, "lookback_days": 30,
            "show_browser": False, "output_folder": "", "keep_reports": 8,
            # What reports cover: the last `lookback_days` days, or fixed dates.
            "report_mode": "days", "report_start": "", "report_end": "",
            # What the change arrows compare with: the same range `compare_days` earlier, or fixed dates.
            "compare_mode": "days", "compare_days": 7, "compare_start": "", "compare_end": "",
            # The Google Drive folder picked when connecting; blank means no uploads.
            "drive_upload_folder_id": "", "drive_upload_folder_name": ""}
# The settings that change the numbers; a report's run-summary.json records them.
METRIC_KEYS = ("mission", "start_time", "cutoff_time", "timezones")
# Retired settings, dropped when an older settings file is read.
RETIRED_KEYS = ("tableau_sso_url", "drive_folder", "drive_folder_name")
# Standard UTC offsets (seconds west, as time.timezone reports them) of the supported zones.
ZONE_OFFSETS = {18000: "EST", 21600: "CST", 25200: "MST", 28800: "PST", 32400: "AKST", 36000: "HST"}


def app_directory():
    """Read-only bundled files: the PyInstaller unpack folder or the source package."""
    return Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def install_directory():
    """The folder holding Referral Dash.exe (or the repository when run from source)."""
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent


def data_directory():
    base = os.getenv("REFERRAL_DASH_HOME") or os.path.join(os.getenv("LOCALAPPDATA") or Path.home(), APP_NAME)
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path():
    return data_directory() / "settings.json"


def suggested_timezone():
    """This computer's time zone, as the closest supported choice; a first guess for setup."""
    zone = ZONE_OFFSETS.get(time.timezone, "UTC")
    # Arizona keeps Mountain Standard Time all year.
    return "MST_NO_DST" if zone == "MST" and not time.daylight else zone


def validate_settings(data):
    result = {**DEFAULTS, **data}
    result["mission"] = str(result["mission"]).strip()
    if not result["mission"] or len(result["mission"]) > 55:
        raise ValueError("Enter a mission name between 1 and 55 characters.")
    for key in ("lightning", "pepper"):
        result[key] = int(result[key])
        if not 0 <= result[key] <= (100 if key == "pepper" else 1440):
            raise ValueError("Lightning must be 0-1440 minutes; pepper must be 0-100 percent.")
    for key in ("start_time", "cutoff_time"):
        result[key] = datetime.strptime(result[key], "%H:%M").strftime("%H:%M")
    if result["start_time"] >= result["cutoff_time"]:
        raise ValueError("Morning time must be earlier than night time.")
    zones = result["timezones"]
    if not isinstance(zones, dict) or "default" not in zones:
        raise ValueError("Select a default time zone.")
    result["timezones"] = {str(k).strip().casefold(): v for k, v in zones.items()}
    if any(v not in TIMEZONES for v in zones.values()):
        raise ValueError("Select one of the supported time zones.")
    for mode, first, last, label in (("report_mode", "report_start", "report_end", "report"),
                                     ("compare_mode", "compare_start", "compare_end", "comparison")):
        if result[mode] not in ("days", "range"):
            raise ValueError(f"Choose how the {label} dates are set.")
        for key in (first, last):
            result[key] = str(result[key] or "").strip()
            if result[key]:
                try:
                    date.fromisoformat(result[key])
                except ValueError as error:
                    raise ValueError(f"Choose valid {label} dates.") from error
        if result[mode] == "range" and not (result[first] and result[last]):
            raise ValueError(f"Choose both a start and an end date for the {label}.")
    result["compare_days"] = int(result["compare_days"])
    if not 1 <= result["compare_days"] <= 365:
        raise ValueError("Compare with 1 to 365 days before.")
    result["lookback_days"] = int(result["lookback_days"])
    if not 1 <= result["lookback_days"] <= 365:
        raise ValueError("Download between 1 and 365 days of referrals.")
    url = str(result["tableau_view_url"]).strip()
    if not re.match(r"https://[^/]+\.online\.tableau\.com/t/[^/]+/views/[^/]+/[^/]+", url):
        raise ValueError("The Tableau link must look like https://...online.tableau.com/t/SITE/views/WORKBOOK/VIEW.")
    result["tableau_view_url"] = url
    for key in RETIRED_KEYS:
        result.pop(key, None)
    result["drive_upload_folder_id"] = str(result["drive_upload_folder_id"]).strip()
    result["drive_upload_folder_name"] = str(result["drive_upload_folder_name"]).strip()
    result["show_browser"] = bool(result["show_browser"])
    result["output_folder"] = str(result["output_folder"]).strip()
    result["keep_reports"] = int(result["keep_reports"])
    if not 0 <= result["keep_reports"] <= 100:
        raise ValueError("Keep between 1 and 100 report sets, or 0 to keep them all.")
    return result


def is_configured(settings):
    return settings["mission"] != DEFAULTS["mission"]


def load_settings():
    path = settings_path()
    if not path.exists():
        # Version 1.x kept settings beside the executable; carry them forward once.
        legacy = install_directory() / "settings.json"
        path = legacy if legacy.exists() else None
    if path is None:
        return validate_settings(DEFAULTS)
    try:
        return validate_settings(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, TypeError, OSError) as exc:
        raise ValueError("Settings could not be read. Check settings.json or move it aside to reset. " + str(exc)) from exc


def save_settings(data):
    validated = validate_settings(data)
    target = settings_path()
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(validated, indent=2), encoding="utf-8")
    temporary.replace(target)
    return validated
