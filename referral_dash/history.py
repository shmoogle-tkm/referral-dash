"""Saved area numbers from finished runs, chosen automatically for comparisons."""
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .district_summary import ATTRIBUTES, key
from .settings import data_directory, metric_settings

HISTORY_VERSION = 1
KEEP = 60
# Like the Referral Care Data Hub: compare with last week's run when there is one.
WEEKLY = timedelta(days=5)


def validate_snapshot(data, settings):
    """Return the snapshot if it can be compared with runs made using these settings."""
    if "areas" not in data:
        raise ValueError("That report is from an older app version.")
    if metric_settings(data["settings"]) != metric_settings(settings):
        raise ValueError("It uses a different mission, time zone, or day window.")
    stamp = datetime.fromisoformat(data["generated_utc"])
    if stamp.tzinfo is None or stamp > datetime.now(timezone.utc):
        raise ValueError("It has an invalid or future date.")
    if not isinstance(data["areas"], list) or not data["areas"]:
        raise ValueError("It has no saved area numbers.")
    seen = set()
    for record in data["areas"]:
        identity = key(record)
        if identity in seen or any(not isinstance(n, str) or not n.strip() for n in identity):
            raise ValueError("It contains invalid or repeated areas.")
        seen.add(identity)
        if type(record["referrals"]) is not int or record["referrals"] < 0:
            raise ValueError("It contains an invalid referral count.")
        for attribute in ATTRIBUTES:
            value = record["stats"][attribute]
            if value is not None and (type(value) not in (float, int) or not math.isfinite(value) or value < 0):
                raise ValueError("It contains invalid saved numbers.")
    if not isinstance(data.get("input_sha256"), str):
        raise ValueError("It is missing its source fingerprint.")
    return data


def select_previous(snapshots, current_hash, now):
    """Newest run at least five days old; otherwise the newest earlier run of different data."""
    earlier = sorted((s for s in snapshots if s["input_sha256"] != current_hash
                      and datetime.fromisoformat(s["generated_utc"]) < now),
                     key=lambda s: s["generated_utc"], reverse=True)
    weekly = [s for s in earlier if datetime.fromisoformat(s["generated_utc"]) <= now - WEEKLY]
    return (weekly or earlier or [None])[0]


class History:
    def __init__(self, settings, root=None):
        self.settings = settings
        scope = hashlib.sha256(json.dumps(metric_settings(settings), sort_keys=True).encode()).hexdigest()[:24]
        self.folder = Path(root or data_directory() / "history") / scope

    def snapshots(self):
        found = []
        for path in sorted(self.folder.glob("*.json")):
            try:
                found.append(validate_snapshot(json.loads(path.read_text(encoding="utf-8")), self.settings))
            except (OSError, KeyError, TypeError, ValueError):
                continue  # A damaged or foreign file never blocks a run; it just isn't a candidate.
        return found

    def previous(self, current_hash, now=None):
        return select_previous(self.snapshots(), current_hash, now or datetime.now(timezone.utc))

    def save(self, facts):
        snapshot = {"version": HISTORY_VERSION, "generated_utc": facts["generated_utc"],
                    "input_sha256": facts["input_sha256"], "settings": metric_settings(self.settings),
                    "first_referral_date": facts["first_referral_date"],
                    "last_referral_date": facts["last_referral_date"], "areas": facts["areas"]}
        validate_snapshot(snapshot, self.settings)
        self.folder.mkdir(parents=True, exist_ok=True)
        name = datetime.fromisoformat(snapshot["generated_utc"]).strftime("%Y%m%dT%H%M%S") + "_" + snapshot["input_sha256"][:8]
        temporary = self.folder / (name + ".tmp")
        temporary.write_text(json.dumps(snapshot, indent=1), encoding="utf-8")
        temporary.replace(self.folder / (name + ".json"))
        for old in sorted(self.folder.glob("*.json"))[:-KEEP]:
            old.unlink(missing_ok=True)

    def import_reports(self, output_folder):
        """Adopt finished version 1.1 reports in the output folder as comparison baselines."""
        if not output_folder or not Path(output_folder).is_dir():
            return 0
        known = {(s["generated_utc"], s["input_sha256"]) for s in self.snapshots()}
        added = 0
        for summary in Path(output_folder).glob("Dashboard_*/run-summary.json"):
            try:
                data = validate_snapshot(json.loads(summary.read_text(encoding="utf-8")), self.settings)
                if (data["generated_utc"], data["input_sha256"]) not in known:
                    self.save(data)
                    added += 1
            except (OSError, KeyError, TypeError, ValueError):
                continue
        return added
