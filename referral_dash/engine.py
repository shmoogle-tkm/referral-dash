"""Validated export import and complete, isolated report runs."""
import csv
import hashlib
import io
import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .group import Mission, ReferralPerson, DATE_FORMAT
from .template import DashboardTemplate
from .settings import METRIC_KEYS, VERSION, validate_settings
from .district_summary import area_records, period_label, render_summary

DATE_COLUMNS = ["First Referral Event Date", "First Contact Attempt Event Date",
                "First Successful Contact Attempt Event Date", "First New Person Being Taught Date",
                "First Sacrament Date", "Latest Sacrament Date"]
REQUIRED_COLUMNS = ["Full Name", "Latest Zone Name", "Latest District Name",
                    "Latest Teaching Area Name", *DATE_COLUMNS]
TIMED_COLUMNS = DATE_COLUMNS[:2]
MAX_SKIPPED_SHARE = 0.10
PERIOD_COLUMN = "Event Date Selected"
DAY_FORMAT = "%m/%d/%Y"


def _parses(value, pattern):
    try:
        datetime.strptime(value, pattern)
        return True
    except ValueError:
        return False


def read_export(filename, settings):
    raw = Path(filename).read_bytes()
    if len(raw) > 75 * 1024 * 1024:
        raise ValueError("Export is larger than 75 MB. Check the Tableau date range and download again.")
    try:
        text = raw.decode("utf-16") if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ValueError("Cannot read this export. Download Crosstab > Detail > CSV directly from Tableau.") from exc
    if "\x00" in text:
        raise ValueError("Unsupported file encoding. Download a fresh CSV from Tableau.")
    first = text.splitlines()[0] if text.strip() else ""
    delimiter = "\t" if "\t" in first else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    headers = [h.strip() for h in (reader.fieldnames or [])]
    if len(headers) != len(set(headers)):
        raise ValueError("The export contains duplicate column names. Download the Detail crosstab again.")
    missing = sorted(set(REQUIRED_COLUMNS) - set(headers))
    if missing:
        raise ValueError("Missing columns: " + ", ".join(missing) + ". Use Mission Finding Detail > Crosstab > Detail > CSV.")
    reader.fieldnames = headers
    mission = Mission()
    rows = 0
    skipped = 0
    dates = []
    for number, original in enumerate(reader, 2):
        if None in original or any(v is None for v in original.values()):
            raise ValueError(f"Row {number}: column count does not match the header. Re-export the original file.")
        row = {k: v.strip() for k, v in original.items()}
        if not any(row.values()):
            continue
        for column in DATE_COLUMNS:
            if row[column].casefold() in ("null", "(null)"):
                row[column] = ""
            if row[column]:
                # Response times need the full timestamp; the other milestones are
                # only checked for being present, and Tableau exports some as dates only.
                formats = (DATE_FORMAT,) if column in TIMED_COLUMNS else (DATE_FORMAT, DAY_FORMAT)
                if not any(_parses(row[column], f) for f in formats):
                    expected = "MM/DD/YYYY hh:mm:ss AM/PM" if column in TIMED_COLUMNS else "MM/DD/YYYY"
                    raise ValueError(f"Row {number}: {column} has an unsupported date. Expected {expected} in UTC. Keep the original Tableau export.")
        # Like the Data Hub, a row that cannot be placed in an area or has no referral is left out.
        if not row[DATE_COLUMNS[0]] or any(row[c].casefold() in ("", "null", "(null)") for c in REQUIRED_COLUMNS[:4]):
            skipped += 1
            continue
        # The Dashboard download is filtered on Event Date Selected, so that range describes it best.
        selected = row.get(PERIOD_COLUMN, "")
        dates.append(datetime.strptime(selected, DAY_FORMAT) if _parses(selected, DAY_FORMAT)
                     else datetime.strptime(row[DATE_COLUMNS[0]], DATE_FORMAT))
        person = ReferralPerson(row, settings=settings)
        mission.add_person(person)
        rows += 1
    if not rows:
        raise ValueError("This export has no referral rows. Check the mission, date range, and filters in Tableau.")
    if skipped > MAX_SKIPPED_SHARE * (rows + skipped):
        raise ValueError(f"{skipped} of {rows + skipped} rows have no area assignment or referral date. "
                         "Check the Tableau filters; this does not look like the Dashboard detail download.")
    return mission, {"referrals": rows, "skipped_rows": skipped, "first_referral_date": min(dates).strftime("%Y-%m-%d"),
                     "last_referral_date": max(dates).strftime("%Y-%m-%d"),
                     "input_sha256": hashlib.sha256(raw).hexdigest()}

def safe_name(value):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", value).strip(" .")[:95] or "Unnamed"
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1,10)], *[f"LPT{i}" for i in range(1,10)]}:
        name = "_" + name
    return name

def generate_reports(filename, output_parent, settings, progress=lambda message, done=None, total=None: None, history=None):
    """Render every report into a new run folder; `history` supplies and keeps comparisons.

    `progress(message, done, total)` reports each image as it starts.
    """
    settings = validate_settings(settings)
    progress("Checking the export...")
    mission, facts = read_export(filename, settings)
    previous = history.previous(facts["input_sha256"]) if history else None
    records = area_records(mission)
    period = period_label(facts["first_referral_date"], facts["last_referral_date"])
    parent = Path(output_parent).resolve()
    if not parent.is_dir():
        raise ValueError("Choose an existing output folder that you can write to.")
    run_id = datetime.now().strftime("%Y-%m-%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    staging = parent / (".building_" + run_id)
    final = parent / ("Dashboard_" + run_id)
    template = DashboardTemplate(settings["lightning"], settings["pepper"])
    districts = sorted({(r["zone"], r["district"]) for r in records + (previous["areas"] if previous else [])})
    total = 1 + len(mission.zones) + sum(len(z.districts) + sum(len(d.areas) for d in z.districts.values())
                                         for z in mission.zones.values()) + len(districts)
    used = set()
    image_count = 0
    def render(group, title, folder, stem):
        nonlocal image_count
        directory = staging / folder
        directory.mkdir(parents=True, exist_ok=True)
        name = safe_name(stem)
        candidate = directory / (name + ".png")
        sequence = 2
        while str(candidate).casefold() in used:
            candidate = directory / (name + f" ({sequence}).png")
            sequence += 1
        used.add(str(candidate).casefold())
        progress(f"Creating {title}...", image_count, total)
        uncontacted = [{"name": p.name, "date": p.first_referral_event_date, "area": p.area}
                       for p in group.referrals_without_contact_attempt]
        template.buildImage(title, group.median_response_time, group.percent_found,
                            group.total_referrals, group.percent_contacted,
                            group.percent_successful_contact, group.percent_attended_church,
                            uncontacted, str(candidate))
        image_count += 1
    try:
        render(mission, settings["mission"], "4. Mission", settings["mission"])
        for zone in mission.zones.values():
            render(zone, f"{zone.name} Zone", "3. Zones", zone.name)
            for district in zone.districts.values():
                render(district, f"{district.name} District", "2. Districts", f"{zone.name} - {district.name}")
                for area in district.areas.values():
                    render(area, f"{area.name} Area", "1. Areas", f"{zone.name} - {district.name} - {area.name}")
        summary_folder = staging / "5. District Summaries"
        summary_folder.mkdir()
        for index,(zone,district) in enumerate(districts,1):
            progress(f"Creating district summary: {district}...", image_count, total)
            render_summary(zone, district, records, previous, period,
                           summary_folder / f"{index:02d} - {safe_name(zone + ' - ' + district)}.png")
            image_count += 1
        facts.update({"version": VERSION, "generated_utc": datetime.now(timezone.utc).isoformat(),
                      "input_name": Path(filename).name, "images": image_count,
                      "settings": {k: settings[k] for k in (*METRIC_KEYS, "lightning", "pepper")},
                      "zones": len(mission.zones),
                      "attempted_percent": mission.percent_contacted,
                      "successful_contact_percent": mission.percent_successful_contact,
                      "found_percent": mission.percent_found, "at_church_percent": mission.percent_attended_church,
                      "median_response_minutes": mission.median_response_time,
                      "areas":records,"district_summaries":len(districts),
                      "comparison_generated_utc":previous["generated_utc"] if previous else None})
        (staging / "run-summary.json").write_text(json.dumps(facts, indent=2), encoding="utf-8")
        staging.rename(final)
    except Exception:
        # Only our unique, incomplete run directory is removed; previous output is untouched.
        if staging.exists():
            shutil.rmtree(staging)
        raise
    if history:
        history.save(facts)
    return final, facts
