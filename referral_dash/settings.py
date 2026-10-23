"""Mission settings, stored per Windows user so app updates keep them."""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

VERSION = "2.0.0"
APP_NAME = "Referral Dash"
TIMEZONES = ["UTC", "EST", "CST", "MST", "MST_NO_DST", "PST", "AKST", "HST"]
# The mission finding workbook used by the Referral Care Data Hub.
DEFAULT_TABLEAU_VIEW = ("https://prod-useast-b.online.tableau.com/t/churchofjesuschrist/views/"
                        "MissionFindingSummaryStephen/MissionFindingDetail")
DEFAULTS = {"mission": "Your Mission", "lightning": 5, "pepper": 20,
            "start_time": "06:30", "cutoff_time": "22:00", "timezones": {"default": "UTC"},
            "tableau_view_url": DEFAULT_TABLEAU_VIEW, "tableau_sso_url": "", "lookback_days": 30,
            "show_browser": False, "output_folder": "", "drive_folder": "", "drive_folder_name": ""}
# Only these settings change the numbers, so only these must match between compared runs.
METRIC_KEYS = ("mission", "start_time", "cutoff_time", "timezones")
DRIVE_FOLDER = re.compile(r"(?:folders/|id=)([A-Za-z0-9_-]{10,})|^([A-Za-z0-9_-]{10,})$")


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


def drive_folder_id(value):
    """Accept a Google Drive folder link or a bare folder ID."""
    value = (value or "").strip()
    if not value:
        return ""
    match = DRIVE_FOLDER.search(value)
    if not match:
        raise ValueError("Paste the Google Drive folder link from your browser's address bar.")
    return match.group(1) or match.group(2)


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
    result["lookback_days"] = int(result["lookback_days"])
    if not 1 <= result["lookback_days"] <= 365:
        raise ValueError("Download between 1 and 365 days of referrals.")
    url = str(result["tableau_view_url"]).strip()
    if not re.match(r"https://[^/]+\.online\.tableau\.com/t/[^/]+/views/[^/]+/[^/]+", url):
        raise ValueError("The Tableau link must look like https://...online.tableau.com/t/SITE/views/WORKBOOK/VIEW.")
    result["tableau_view_url"] = url
    result["tableau_sso_url"] = str(result["tableau_sso_url"]).strip()
    result["show_browser"] = bool(result["show_browser"])
    result["output_folder"] = str(result["output_folder"]).strip()
    result["drive_folder"] = drive_folder_id(result["drive_folder"])
    result["drive_folder_name"] = str(result["drive_folder_name"]).strip() if result["drive_folder"] else ""
    return result


def metric_settings(settings):
    validated = validate_settings(settings)
    return {key: validated[key] for key in METRIC_KEYS}


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
