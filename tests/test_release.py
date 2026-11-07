import csv
import json
import os
import tempfile
import time
import unittest
import unittest.mock
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from PIL import Image, ImageChops

from referral_dash import engine
from referral_dash.district_summary import BAD, GOOD, MUTED, percent_change, period_label, trend
from referral_dash.group import ReferralPerson
from referral_dash.period import make_period
from referral_dash.settings import DEFAULTS, app_directory, validate_settings

SAMPLE = app_directory() / "sample-data" / "sample-referrals.csv"
SETTINGS = validate_settings({"mission": "Sample Mission"})
SAMPLE_PERIOD = make_period(SETTINGS, "2026-10-01", "2026-10-08")


class Isolated(unittest.TestCase):
    """Every test gets its own settings folder instead of the user's AppData."""

    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        patcher = patch.dict(os.environ, {"REFERRAL_DASH_HOME": self.home.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.home.cleanup)

    def rewrite_sample(self, folder, transform=lambda rows: rows, encoding="utf-8-sig", delimiter=","):
        with SAMPLE.open(encoding="utf-16", newline="") as f:
            rows = list(csv.DictReader(f, delimiter="\t"))
        rows = transform(rows)
        target = Path(folder) / "input.csv"
        with target.open("w", newline="", encoding=encoding) as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else engine.REQUIRED_COLUMNS, delimiter=delimiter)
            writer.writeheader()
            writer.writerows(rows)
        return target


class ReportTests(Isolated):
    def test_complete_sample_and_repeat_preserve_previous_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            previous = Path(folder) / "areas"
            previous.mkdir()
            (previous / "keep.txt").write_text("existing output")
            output, facts = engine.generate_reports(SAMPLE, folder, SETTINGS, period=SAMPLE_PERIOD)
            self.assertEqual((facts["referrals"], facts["images"], facts["district_summaries"], len(facts["areas"])), (8, 11, 2, 4))
            self.assertEqual(sorted(p.name for p in output.iterdir() if p.is_dir()),
                             ["1. Areas", "2. Districts", "3. Zones", "4. Mission", "5. District Summaries"])
            self.assertEqual((facts["attempted_percent"], facts["successful_contact_percent"], facts["found_percent"],
                              facts["at_church_percent"], facts["median_response_minutes"]), (75, 50, 25, 50, 25))
            self.assertNotIn("output_folder", facts["settings"])
            self.assertEqual(len(list(output.rglob("*.jpg"))), 11)
            for image in output.rglob("*.jpg"):
                with Image.open(image) as value:
                    value.verify()
            again, _ = engine.generate_reports(SAMPLE, folder, SETTINGS, period=SAMPLE_PERIOD)
            self.assertNotEqual(output, again)
            self.assertEqual((previous / "keep.txt").read_text(), "existing output")

    def test_progress_reports_every_image(self):
        calls = []
        with tempfile.TemporaryDirectory() as folder:
            engine.generate_reports(SAMPLE, folder, SETTINGS, lambda m, done=None, total=None: calls.append((done, total)), period=SAMPLE_PERIOD)
        counted = [c for c in calls if c[1]]
        self.assertEqual(len(counted), 11)
        self.assertEqual(counted[-1], (10, 11))

    def test_failed_render_never_publishes_partial_folder(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(engine, "render_summary", side_effect=OSError("summary cannot save")):
            with self.assertRaisesRegex(OSError, "summary cannot save"):
                engine.generate_reports(SAMPLE, folder, SETTINGS)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_bad_exports_fail_without_output(self):
        cases = [lambda rows: [],
                 lambda rows: [{k: v for k, v in row.items() if k != "Latest Zone Name"} for row in rows],
                 lambda rows: [{**rows[0], "First Referral Event Date": "not a date"}],
                 lambda rows: [{**rows[0], "Latest Zone Name": "Null"}]]
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                target = self.rewrite_sample(folder, case)
                with self.assertRaises(ValueError):
                    engine.generate_reports(target, folder, SETTINGS)
                self.assertEqual(list(Path(folder).glob("Referral Reports*")), [])

    def test_real_tableau_shapes_date_only_milestones_and_unplaced_rows(self):
        def shape(rows):
            rows = [{**row, "First New Person Being Taught Date": "8/30/2026" if row["First New Person Being Taught Date"] else ""}
                    for row in rows if row["First Referral Event Date"].startswith("10/")] * 3
            return rows + [{**rows[0], "Latest Zone Name": "", "Latest District Name": "", "Latest Teaching Area Name": ""}]
        with tempfile.TemporaryDirectory() as folder:
            mission, _, facts = engine.read_export(self.rewrite_sample(folder, shape), SETTINGS)
            self.assertEqual((facts["referrals"], facts["skipped_rows"]), (24, 1))
            self.assertEqual(mission.percent_found, 25)
            mostly_unplaced = lambda rows: rows[:1] + [{**row, "Latest Zone Name": "Null"} for row in rows[1:]]
            with self.assertRaisesRegex(ValueError, "no area assignment"):
                engine.read_export(self.rewrite_sample(folder, mostly_unplaced), SETTINGS)
            timed_column_without_time = lambda rows: [{**rows[0], "First Contact Attempt Event Date": "10/01/2026"}]
            with self.assertRaisesRegex(ValueError, "hh:mm:ss"):
                engine.read_export(self.rewrite_sample(folder, timed_column_without_time), SETTINGS)

    def test_folders_are_named_for_their_dates(self):
        from referral_dash.district_summary import folder_label
        self.assertEqual(folder_label("2026-10-07", "2026-11-06"), "Oct 7 to Nov 6, 2026")
        self.assertEqual(folder_label("2025-12-20", "2026-01-05"), "Dec 20, 2025 to Jan 5, 2026")
        self.assertEqual(folder_label("2026-10-07", "2026-10-07"), "Oct 7, 2026")
        with tempfile.TemporaryDirectory() as folder:
            first, _ = engine.generate_reports(SAMPLE, folder, SETTINGS, period=SAMPLE_PERIOD)
            second, _ = engine.generate_reports(SAMPLE, folder, SETTINGS, period=SAMPLE_PERIOD)
            third, _ = engine.generate_reports(SAMPLE, folder, SETTINGS, period=SAMPLE_PERIOD)
            whole, _ = engine.generate_reports(SAMPLE, folder, SETTINGS)
            self.assertEqual((first.name, second.name, third.name, whole.name),
                             ("Referral Reports - Oct 1 to Oct 8, 2026", "Referral Reports - Oct 1 to Oct 8, 2026 (2)",
                              "Referral Reports - Oct 1 to Oct 8, 2026 (3)", "Referral Reports - Sep 24 to Oct 8, 2026"))
            self.assertEqual({p.name for p in Path(folder).iterdir()}, {first.name, second.name, third.name, whole.name})

    def test_utf8_comma_export(self):
        with tempfile.TemporaryDirectory() as folder:
            mission, _, _ = engine.read_export(self.rewrite_sample(folder), SETTINGS)
            self.assertEqual(mission.total_referrals, 16)

    def test_gauge_numbers_are_centered_independently_of_units(self):
        base = Image.open(app_directory() / "images" / "dashboard.png").convert("RGB")
        with tempfile.TemporaryDirectory() as folder:
            for value in (2, 25, 1000.5):
                target = Path(folder) / f"{value}.png"
                engine.DashboardTemplate(0, 101).buildImage("Demo", value, 25, 8, 75, 50, 50, [], target)
                rendered = Image.open(target).convert("RGB")
                for center, box in [((444, 428), (335, 395, 553, 467)), ((1096, 428), (986, 395, 1205, 467))]:
                    bounds = ImageChops.difference(base.crop(box), rendered.crop(box)).getbbox()
                    self.assertIsNotNone(bounds)
                    self.assertLessEqual(abs(box[0] + (bounds[0] + bounds[2]) / 2 - center[0]), 1)
                    self.assertLessEqual(abs(box[1] + (bounds[1] + bounds[3]) / 2 - center[1]), 1)

    def test_many_unattempted_referrals_extend_image(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "many.png"
            people = [{"name": f"Example Person {i}", "date": "10/01/2026 02:00:00 PM", "area": "Sample Area"} for i in range(36)]
            engine.DashboardTemplate().buildImage("Fictional sample", None, 0, 36, 0, 0, 0, people, image)
            with Image.open(image) as value:
                self.assertGreaterEqual(value.height, 1450)


class ComparisonTests(Isolated):
    def test_first_ever_run_shows_change_from_the_week_before(self):
        """Regression: a mission's first run used to say "No prior data" on every row."""
        with tempfile.TemporaryDirectory() as folder:
            output, facts = engine.generate_reports(SAMPLE, folder, SETTINGS, period=SAMPLE_PERIOD)
            self.assertEqual(facts["period"], "Oct 1, 2026 – Oct 8, 2026")
            self.assertEqual(facts["comparison_period"], "Sep 24, 2026 – Oct 1, 2026")
            summary = Image.open(next((output / "5. District Summaries").glob("*.jpg"))).convert("RGB")
            arrows = sum(1 for r, g, b in summary.get_flattened_data() if (g > 200 and r < 120 and b < 180) or (r > 220 and g < 120 and b < 120))
            self.assertGreater(arrows, 100)
            whole, facts = engine.generate_reports(SAMPLE, folder, SETTINGS)
            self.assertIsNone(facts["comparison_period"])
            summary = Image.open(next((whole / "5. District Summaries").glob("*.jpg"))).convert("RGB")
            self.assertLess(sum(1 for r, g, b in summary.get_flattened_data() if g > 200 and r < 120 and b < 180), 20)

    def test_reports_count_only_the_chosen_range_and_baseline_only_the_earlier_one(self):
        mission, baseline, facts = engine.read_export(SAMPLE, SETTINGS, SAMPLE_PERIOD)
        self.assertEqual(mission.total_referrals, 8)
        self.assertEqual(facts["first_referral_date"], "2026-10-01")
        self.assertEqual(baseline.total_referrals, 9)  # Sep 24 to Oct 1, including Oct 1's referral.
        # On Oct 1, that referral had been contacted but not yet taught (Oct 2).
        self.assertEqual(baseline.total_found, 1)

    def test_rewind_applies_the_rules_measured_on_real_exports(self):
        cutoff = datetime(2026, 10, 23, 18, 39, tzinfo=timezone.utc)
        zone = timezone.utc
        row = {"First Referral Event Date": "10/20/2026 02:00:00 PM", "First Contact Attempt Event Date": "10/23/2026 06:40:00 PM",
               "First Successful Contact Attempt Event Date": "10/21/2026 03:00:00 PM",
               "First New Person Being Taught Date": "10/25/2026", "First Lesson Date": "10/23/2026",
               "First Sacrament Date": "10/25/2026", "Latest Sacrament Date": "10/18/2026 12:00:00 AM"}
        earlier = engine.rewind(row, cutoff, zone)
        self.assertEqual(earlier["First Contact Attempt Event Date"], "")  # One minute after the cutoff.
        self.assertEqual(earlier["First Successful Contact Attempt Event Date"], row["First Successful Contact Attempt Event Date"])
        self.assertEqual(earlier["First New Person Being Taught Date"], "10/25/2026")  # The Sunday ending this week; lesson already held.
        self.assertEqual(earlier["First Sacrament Date"], "")  # This coming Sunday.
        self.assertEqual(earlier["Latest Sacrament Date"], row["Latest Sacrament Date"])
        later_lesson = engine.rewind({**row, "First Lesson Date": "10/24/2026"}, cutoff, zone)
        self.assertEqual(later_lesson["First New Person Being Taught Date"], "")
        no_lesson = engine.rewind({**row, "First Lesson Date": ""}, cutoff, zone)
        self.assertEqual(no_lesson["First New Person Being Taught Date"], "10/25/2026")
        next_week = engine.rewind({**row, "First Lesson Date": "", "First New Person Being Taught Date": "11/01/2026"}, cutoff, zone)
        self.assertEqual(next_week["First New Person Being Taught Date"], "")
        self.assertIsNone(engine.rewind({**row, "First Referral Event Date": "10/23/2026 07:00:00 PM"}, cutoff, zone))

    def test_past_range_measures_response_time_at_its_end_not_today(self):
        with tempfile.TemporaryDirectory() as folder:
            uncontacted = lambda rows: [{**row, "First Contact Attempt Event Date": ""} for row in rows if row["First Referral Event Date"].startswith("10/08")]
            target = self.rewrite_sample(folder, uncontacted)
            mission, _, _ = engine.read_export(target, SETTINGS, make_period(SETTINGS, "2026-10-08", "2026-10-08"))
            self.assertLess(mission.median_response_time, 24 * 60)

    def test_comparison_leaves_off_the_days_tableau_is_behind(self):
        with tempfile.TemporaryDirectory() as folder:
            trailing = self.rewrite_sample(folder, lambda rows: [row for row in rows if not row["First Referral Event Date"].startswith(("10/06", "10/07", "10/08"))])
            # The newest row is Oct 5, three days before the end date, so the comparison stops at Sep 28.
            self.assertEqual(engine.read_export(trailing, SETTINGS, SAMPLE_PERIOD)[1].total_referrals, 5)
            # A range the data fully reaches keeps every day of its comparison.
            self.assertEqual(engine.read_export(SAMPLE, SETTINGS, SAMPLE_PERIOD)[1].total_referrals, 9)

    def test_pictures_are_dated_by_the_report_not_today(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(engine.DashboardTemplate, "buildImage") as build, \
                patch.object(engine, "render_summary") as summary:
            engine.generate_reports(SAMPLE, folder, SETTINGS, period=SAMPLE_PERIOD)
        self.assertEqual(build.call_args.args[-1], date(2026, 10, 8))
        self.assertEqual(summary.call_args.args[4:6], ("Oct 1, 2026 – Oct 8, 2026", "Sep 24, 2026 – Oct 1, 2026"))

    def test_csv_without_the_earlier_week_has_no_comparison(self):
        with tempfile.TemporaryDirectory() as folder:
            only_october = self.rewrite_sample(folder, lambda rows: [row for row in rows if row["First Referral Event Date"].startswith("10/")])
            self.assertIsNone(engine.read_export(only_october, SETTINGS, SAMPLE_PERIOD)[1])
            # A download known to cover the earlier week trusts the requested range instead.
            self.assertIsNotNone(engine.read_export(only_october, SETTINGS, SAMPLE_PERIOD, baseline_covered=True)[1])
            with self.assertRaisesRegex(ValueError, "no referrals from"):
                engine.read_export(only_october, SETTINGS, make_period(SETTINGS, "2026-01-01", "2026-01-31"))

    def test_a_new_install_compares_with_seven_days_before(self):
        self.assertEqual(DEFAULTS["compare_days"], 7)
        self.assertEqual(DEFAULTS["compare_mode"], "days")
        period = make_period(validate_settings({"mission": "Any Mission"}), "2026-08-08", "2026-09-08")
        self.assertEqual((period.baseline_start, period.baseline_end), (date(2026, 8, 1), date(2026, 9, 1)))

    def test_range_and_week_earlier_dates(self):
        period = make_period(SETTINGS, "2026-08-08", "2026-09-08")
        self.assertEqual((period.baseline_start, period.baseline_end), (date(2026, 8, 1), date(2026, 9, 1)))
        self.assertEqual(period.download_start, date(2026, 8, 1))
        self.assertEqual(period.cutoff, datetime(2026, 9, 8, 23, 59, 59, tzinfo=timezone.utc))
        self.assertEqual(period.baseline_cutoff, datetime(2026, 9, 1, 23, 59, 59, tzinfo=timezone.utc))
        chosen = make_period(SETTINGS, "2026-08-08", "2026-09-08", "2026-06-01", "2026-07-01")
        self.assertEqual(chosen.download_start, date(2026, 6, 1))
        self.assertEqual(chosen.baseline_cutoff, datetime(2026, 7, 1, 23, 59, 59, tzinfo=timezone.utc))
        inside = make_period(SETTINGS, "2026-08-08", "2026-09-08", "2026-08-20", "2026-08-25")
        self.assertEqual(inside.download_start, date(2026, 8, 8))
        now = datetime(2026, 11, 6, 15, 0, tzinfo=timezone.utc)
        ending_today = make_period(SETTINGS, now=now)
        self.assertEqual((ending_today.start, ending_today.end, ending_today.cutoff), (date(2026, 10, 7), date(2026, 11, 6), now))
        for start, end in (("2026-09-09", "2026-09-08"), ("2026-11-01", "2026-11-07"), ("2025-01-01", "2026-09-08"), ("soon", "2026-09-08")):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                make_period(SETTINGS, start, end, now=now)
        for first, last in (("2026-08-01", None), (None, "2026-09-01"), ("2026-09-01", "2026-08-01"), ("2026-08-01", "2026-09-08"), ("2026-08-01", "2026-09-30")):
            with self.subTest(first=first, last=last), self.assertRaises(ValueError):
                make_period(SETTINGS, "2026-08-08", "2026-09-08", first, last, now=now)

    def test_a_chosen_comparison_range_replaces_the_week_before(self):
        period = make_period(SETTINGS, "2026-10-01", "2026-10-08", "2026-09-24", "2026-09-27")
        with tempfile.TemporaryDirectory() as folder:
            _, facts = engine.generate_reports(SAMPLE, folder, SETTINGS, period=period)
        self.assertEqual(facts["comparison_period"], "Sep 24, 2026 – Sep 27, 2026")
        self.assertEqual(engine.read_export(SAMPLE, SETTINGS, period)[1].total_referrals, 4)
        # Older than the file reaches, so it is reported as missing rather than as a decline.
        missing = make_period(SETTINGS, "2026-10-01", "2026-10-08", "2026-08-01", "2026-08-15")
        self.assertIsNone(engine.read_export(SAMPLE, SETTINGS, missing)[1])

    def test_trend_rules_match_the_data_hub(self):
        self.assertEqual(trend(percent_change(80, 40), False), ("100.0%", GOOD, True))
        self.assertEqual(trend(percent_change(10, 20), True), ("50.0%", GOOD, False))
        self.assertEqual(trend(percent_change(20, 10), True), ("100.0%", BAD, True))
        self.assertEqual(trend(percent_change(5, 5), False), ("— 0.0%", MUTED, None))
        self.assertEqual(trend(percent_change(5, 0), False), ("100.0%", GOOD, True))
        self.assertEqual(trend(None, False, prior_available=False), ("No prior data", MUTED, None))
        self.assertEqual(period_label("2026-09-23", "2026-10-03"), "Sep 23, 2026 – Oct 3, 2026")


class SettingsTests(Isolated):
    def test_zone_override_is_case_insensitive(self):
        values = validate_settings({**DEFAULTS, "timezones": {"default": "UTC", " NORTH ": "EST"}})
        row = {"Latest Zone Name": "North", "First Referral Event Date": "10/01/2026 10:00:00 AM",
               "First Contact Attempt Event Date": "10/01/2026 11:00:00 AM"}
        self.assertEqual(ReferralPerson(row, values).contact_speed, 30)
        self.assertEqual(ReferralPerson(row, validate_settings(DEFAULTS)).contact_speed, 60)

    def test_invalid_settings_and_windows_filenames(self):
        for data in ({"start_time": "22:00"}, {"pepper": 101}, {"timezones": {"default": "unknown"}},
                     {"tableau_view_url": "https://example.com"}, {"lookback_days": 0}, {"keep_reports": 101}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_settings({**DEFAULTS, **data})
        self.assertEqual(engine.safe_name("CON"), "_CON")
        self.assertEqual(engine.safe_name("A/B"), "A-B")

    def test_settings_from_the_drive_version_still_load(self):
        from referral_dash import settings
        old = {**DEFAULTS, "mission": "Old Mission", "drive_folder": "1xGTi7lEfAdrMMTrh", "drive_folder_name": "Dashboards",
               "tableau_sso_url": "", "timezones": {"default": "EST", "hawaii": "HST"}, "lookback_days": 60}
        old.pop("keep_reports")
        settings.settings_path().write_text(json.dumps(old))
        loaded = settings.load_settings()
        self.assertEqual((loaded["mission"], loaded["lookback_days"], loaded["keep_reports"]), ("Old Mission", 60, 8))
        self.assertEqual(loaded["timezones"], {"default": "EST", "hawaii": "HST"})
        self.assertFalse({"drive_folder", "drive_folder_name", "tableau_sso_url"} & loaded.keys())

    def test_time_zone_guess_comes_from_this_computer(self):
        from referral_dash import settings
        for offset, daylight, expected in ((18000, 1, "EST"), (21600, 1, "CST"), (25200, 1, "MST"),
                                           (25200, 0, "MST_NO_DST"), (36000, 0, "HST"), (-3600, 1, "UTC")):
            with self.subTest(offset=offset, daylight=daylight), \
                    patch.object(settings.time, "timezone", offset), patch.object(settings.time, "daylight", daylight):
                self.assertEqual(settings.suggested_timezone(), expected)

    def test_version_one_settings_beside_the_app_are_carried_forward(self):
        from referral_dash import settings
        with tempfile.TemporaryDirectory() as old:
            (Path(old) / "settings.json").write_text(json.dumps({**DEFAULTS, "mission": "Legacy Mission"}))
            with patch.object(settings, "install_directory", return_value=Path(old)):
                self.assertEqual(settings.load_settings()["mission"], "Legacy Mission")


class TableauTests(unittest.TestCase):
    def test_dashboard_filters_use_the_mission_from_settings(self):
        from referral_dash.tableau.dashboard import SOURCES, dashboard_entry_url, dashboard_filters
        filters = dict(dashboard_filters("Utah Provo Mission"))
        self.assertEqual(filters["Mission"], ("Utah Provo Mission",))
        self.assertEqual(filters["Finding Source"], SOURCES)
        self.assertEqual(dashboard_entry_url("https://x.online.tableau.com/t/site/views/Book/View/123/Dashboard?a=1"),
                         "https://x.online.tableau.com/t/site/views/Book/View")

    def test_unknown_mission_suggests_the_closest_tableau_names(self):
        from referral_dash.tableau.dashboard import missing_mission_message
        choices = ["(All)", "Tennessee Knoxville Mission", "Tennessee Nashville Mission", "Utah Provo Mission"]
        self.assertIn("Did you mean 'Tennessee Knoxville Mission'?", missing_mission_message("knoxville", choices))
        self.assertIn("'Tennessee Knoxville Mission'", missing_mission_message("Tennesee Knoxvile Mission", choices))
        message = missing_mission_message("Brazil Recife Mission", choices)
        self.assertNotIn("Did you mean", message)
        self.assertIn("Fix the mission name in Settings", message)

    def test_authenticator_reopens_a_hidden_browser_and_asks_the_person(self):
        from referral_dash.tableau import download
        from referral_dash.tableau.session import VerificationNeeded
        opened, asked = [], []

        def fake_download(driver, settings, credentials, work, destination, period, progress, verification):
            verification()  # The Church sign-in shows an authenticator prompt.
            return "export.csv"
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(download, "create_browser", side_effect=lambda work, visible: opened.append(visible) or unittest.mock.MagicMock()), \
                patch.object(download, "_download", side_effect=fake_download):
            result = download.download_dashboard(SETTINGS, None, folder, SAMPLE_PERIOD, attention=asked.append)
        self.assertEqual(result, "export.csv")
        self.assertEqual(opened, [False, True])  # Hidden first, then visible for the person.
        self.assertEqual(asked, [download.VERIFY_MESSAGE])
        self.assertTrue(issubclass(VerificationNeeded, RuntimeError))

    def test_download_date_check_rejects_unfiltered_exports(self):
        from referral_dash.tableau.download import validate_date_range
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "export.csv"
            path.write_text("Event Date Selected\tFull Name\n10/01/2026\tA\n10/20/2026\tB\n", encoding="utf-16")
            validate_date_range(path, date(2026, 10, 1), date(2026, 10, 20))
            with self.assertRaisesRegex(ValueError, "earliest row"):
                validate_date_range(path, date(2026, 10, 2), date(2026, 10, 20))
            with self.assertRaisesRegex(ValueError, "latest row"):
                validate_date_range(path, date(2026, 10, 1), date(2026, 10, 19))
            path.write_text("Full Name\nA\n", encoding="utf-16")
            with self.assertRaisesRegex(ValueError, "Event Date Selected"):
                validate_date_range(path, date(2026, 10, 2), date(2026, 10, 20))

    def test_tableau_is_asked_for_the_extra_week_through_the_end_date(self):
        from referral_dash.tableau import download
        typed = []
        driver = unittest.mock.MagicMock()
        with tempfile.TemporaryDirectory() as folder:
            export = Path(folder) / "work" / "crosstab.csv"
            export.parent.mkdir()
            export.write_text("Event Date Selected\n8/1/2026\n9/8/2026\n", encoding="utf-16")
            with patch.object(download, "login_tableau"), patch.object(download, "open_dashboard_view"), \
                    patch.object(download, "WebDriverWait"), patch.object(download.time, "sleep"), \
                    patch.object(download, "_wait_for_download", return_value=export), \
                    patch.object(download, "set_date_filter", side_effect=lambda d, element, value: typed.append(value)):
                final = download._download(driver, SETTINGS, unittest.mock.MagicMock(), export.parent, Path(folder),
                                           make_period(SETTINGS, "2026-08-08", "2026-09-08"), lambda m: None, lambda: None)
            self.assertTrue(final.exists())
        self.assertEqual(typed, ["8/1/2026", "9/8/2026"])


class CleanupTests(Isolated):
    def make_run(self, parent, name, finished=True):
        run = Path(parent) / name
        (run / "1. Areas").mkdir(parents=True)
        (run / "1. Areas" / "a.jpg").write_bytes(b"x")
        if finished:
            (run / "run-summary.json").write_text("{}")
        return run

    def test_keeps_only_the_newest_report_sets_it_made(self):
        from referral_dash.cleanup import prune_reports
        with tempfile.TemporaryDirectory() as folder:
            # Old and new folder names together; the finish time decides what is newest, not the name.
            names = ["Dashboard_2026-10-29_090000_abc123", "Referral Reports - Sep 1 to Sep 30, 2026",
                     "Dashboard_2026-10-15_090000_abc123", "Referral Reports - Aug 1 to Aug 31, 2026",
                     "Referral Reports - Jan 1 to Jan 31, 2026"]
            runs = [self.make_run(folder, name) for name in names]
            for age, run in enumerate(reversed(runs)):
                os.utime(run / "run-summary.json", (time.time() - age * 3600, time.time() - age * 3600))
            unfinished = self.make_run(folder, "Referral Reports - Feb 1 to Feb 28, 2026", finished=False)
            mine = Path(folder) / "My notes"
            mine.mkdir()
            removed = prune_reports(folder, 2)
            self.assertEqual(sorted(removed), sorted(runs[:3]))
            self.assertEqual([r.exists() for r in runs], [False, False, False, True, True])
            # Folders the app cannot prove it finished, and the person's own folders, are never touched.
            self.assertTrue(unfinished.exists() and mine.exists())
            self.assertEqual(prune_reports(folder, 0), [])
            self.assertTrue(runs[3].exists())

    def test_removes_abandoned_staging_folders_after_a_day(self):
        from referral_dash.cleanup import prune_reports
        with tempfile.TemporaryDirectory() as folder:
            old, fresh = Path(folder) / ".building_old", Path(folder) / ".building_new"
            old.mkdir()
            fresh.mkdir()
            day_ago = time.time() - 2 * 24 * 3600
            os.utime(old, (day_ago, day_ago))
            prune_reports(folder, 8)
            self.assertEqual((old.exists(), fresh.exists()), (False, True))

    def test_browser_caches_go_but_the_sign_in_stays(self):
        from referral_dash.cleanup import prune_app_data
        from referral_dash.settings import data_directory
        home = data_directory()
        profile = home / "browser" / "chrome"
        for cache in ("Default/Cache/Cache_Data", "Default/Code Cache/js", "component_crx_cache"):
            (profile / cache).mkdir(parents=True)
            (profile / cache / "blob").write_bytes(b"x" * 1024)
        (profile / "Default" / "Network").mkdir(parents=True)
        (profile / "Default" / "Network" / "Cookies").write_bytes(b"session")
        (home / "window" / "ProvenanceData").mkdir(parents=True)
        (home / "google-token.json").write_text("{}")
        prune_app_data()
        self.assertTrue((profile / "Default" / "Network" / "Cookies").exists())
        self.assertFalse((profile / "Default" / "Cache").exists())
        self.assertFalse((profile / "component_crx_cache").exists())
        self.assertFalse((home / "window" / "ProvenanceData").exists())
        self.assertFalse((home / "google-token.json").exists())


class AppTests(Isolated):
    """Drive the local control center the same way the window does."""

    def setUp(self):
        super().setUp()
        from http.server import ThreadingHTTPServer
        import threading
        from referral_dash import app
        self.app = app
        self.output = tempfile.TemporaryDirectory()
        self.addCleanup(self.output.cleanup)
        keyring = patch("referral_dash.credentials.keyring")
        self.keyring = keyring.start()
        self.keyring.get_password.return_value = None
        self.addCleanup(keyring.stop)
        self.controller = app.Controller()
        handler = type("H", (app.Handler,), {"controller": self.controller, "token": "secret"})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def call(self, path, body=None, token="secret"):
        data = None if body is None else json.dumps(body).encode()
        request = Request(self.base + path, data=data, headers={"X-Session": token, "Content-Type": "application/json"})
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read())
        except Exception as error:
            with error:
                return error.code, json.loads(error.read())

    def test_requires_session_token(self):
        self.assertEqual(self.call("/api/state", token="wrong")[0], 403)
        self.assertEqual(self.call("/api/run", {"source": "sample"}, token="")[0], 403)

    def test_real_runs_need_setup_first(self):
        status, payload = self.call("/api/run", {"source": "tableau"})
        self.assertEqual(status, 400)
        self.assertIn("mission name", payload["error"])
        self.call("/api/settings", {"mission": "Sample Mission"})
        status, payload = self.call("/api/run", {"source": "tableau"})
        self.assertIn("Church account", payload["error"])

    def finish(self, body):
        status, _ = self.call("/api/run", body)
        self.assertEqual(status, 200)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            state = self.call("/api/state")[1]
            if state["run"]["status"] != "running":
                break
            time.sleep(0.2)
        self.assertEqual(state["run"]["status"], "done", state["run"]["error"])
        return state

    def test_sample_runs_stay_out_of_the_real_reports_folder(self):
        from referral_dash.settings import data_directory, load_settings, save_settings
        save_settings({**load_settings(), "output_folder": self.output.name})
        state = self.finish({"source": "sample"})
        self.assertEqual(state["run"]["result"]["summaries"], 2)
        self.assertEqual([s["id"] for s in state["run"]["steps"]], ["download", "render", "summaries"])
        self.assertTrue(all(s["state"] == "done" for s in state["run"]["steps"]))
        self.assertEqual(state["recent"], [])
        self.assertEqual(list(Path(self.output.name).iterdir()), [])
        self.finish({"source": "sample"})
        self.assertEqual(len(list((data_directory() / "sample").glob("Referral Reports - Oct 1 to Oct 8, 2026*"))), 1)

    def test_csv_runs_keep_only_the_newest_report_sets(self):
        from referral_dash.settings import load_settings, save_settings
        save_settings({**load_settings(), "mission": "Sample Mission", "output_folder": self.output.name, "keep_reports": 2})
        inputs = []
        for keep in (8, 7, 6):
            folder = Path(tempfile.mkdtemp(dir=self.home.name))
            inputs.append(str(self.rewrite_sample(folder, lambda rows, keep=keep: rows[-keep:])))
        with patch.object(self.controller, "ask_ui", side_effect=inputs):
            results = [self.finish({"source": "file"})["run"]["result"] for _ in range(3)]
        self.assertEqual([r["removed"] for r in results], [0, 0, 1])
        state = self.call("/api/state")[1]
        self.assertEqual([r["name"] for r in state["recent"]], [Path(results[i]["folder"]).name for i in (2, 1)])
        self.assertIsNone(results[2]["compared_with"])  # A CSV without chosen dates is reported whole.

    def test_sample_run_shows_the_week_before_comparison(self):
        result = self.finish({"source": "sample"})["run"]["result"]
        self.assertEqual(result["compared_with"], "Sep 24, 2026 – Oct 1, 2026")
        self.assertEqual(result["referrals"], 8)

    def test_saved_dates_reach_the_download_and_the_report(self):
        from referral_dash.credentials import LoginCredentials
        from referral_dash.settings import load_settings, save_settings
        save_settings({**load_settings(), "mission": "Sample Mission", "output_folder": self.output.name})
        seen = []

        def download(settings, login, folder, period, progress, attention):
            seen.append(period)
            return SAMPLE
        with patch("referral_dash.tableau.download.download_dashboard", side_effect=download), \
                patch.object(self.app.credentials, "load_login", return_value=LoginCredentials("a@b.c", "user", "pw")):
            today = self.finish({"source": "tableau"})
            self.assertEqual(self.call("/api/settings", {"report_mode": "range", "report_start": "2026-10-01", "report_end": "2026-10-08"})[0], 200)
            fixed = self.finish({"source": "tableau"})
            self.assertEqual(self.call("/api/settings", {"compare_mode": "range", "compare_start": "2026-09-24", "compare_end": "2026-09-27"})[0], 200)
            chosen = self.finish({"source": "tableau"})
            self.assertEqual(self.call("/api/settings", {"compare_mode": "days", "compare_days": 3})[0], 200)
            shifted = self.finish({"source": "tableau"})
        self.assertEqual(seen[0].baseline_start, seen[0].start - timedelta(days=7))
        self.assertEqual((seen[1].start, seen[1].end), (date(2026, 10, 1), date(2026, 10, 8)))
        self.assertEqual(fixed["run"]["result"]["compared_with"], "Sep 24, 2026 – Oct 1, 2026")
        self.assertEqual(fixed["plan"]["period"], "Oct 1, 2026 – Oct 8, 2026")
        self.assertEqual(chosen["run"]["result"]["compared_with"], "Sep 24, 2026 – Sep 27, 2026")
        self.assertEqual(seen[2].download_start, date(2026, 9, 24))
        self.assertEqual((seen[3].baseline_start, seen[3].baseline_end), (date(2026, 9, 28), date(2026, 10, 5)))
        self.assertIn("compared", today["plan"])

    def test_the_run_request_cannot_override_the_saved_dates(self):
        from referral_dash.settings import load_settings, save_settings
        save_settings({**load_settings(), "mission": "Sample Mission", "output_folder": self.output.name})
        result = self.finish({"source": "sample", "start": "2020-01-01", "end": "2020-02-01"})["run"]["result"]
        self.assertEqual(result["period"], "Oct 1, 2026 – Oct 8, 2026")

    def test_impossible_dates_are_refused_when_saving(self):
        self.call("/api/settings", {"mission": "Sample Mission"})
        cases = [({"report_mode": "range", "report_start": "2026-10-08", "report_end": "2026-10-01"}, "on or before"),
                 ({"report_mode": "range", "report_start": "2026-10-01", "report_end": "2099-01-01"}, "future"),
                 ({"report_mode": "range", "report_start": "2026-10-01"}, "both"),
                 ({"report_mode": "range", "report_start": "2026-10-01", "report_end": "2026-10-08", "compare_mode": "range",
                   "compare_start": "2026-09-01", "compare_end": "2026-10-20"}, "must end before"),
                 ({"compare_days": 0}, "1 to 365")]
        for body, message in cases:
            with self.subTest(body=body):
                status, payload = self.call("/api/settings", body)
                self.assertEqual(status, 400)
                self.assertIn(message, payload["error"])
        state = self.call("/api/state")[1]
        self.assertEqual(state["settings"]["report_mode"], "days")
        self.assertEqual(state["settings"]["compare_days"], 7)

    def test_csv_uses_fixed_dates_but_otherwise_is_reported_whole(self):
        from referral_dash.settings import load_settings, save_settings
        save_settings({**load_settings(), "mission": "Sample Mission", "output_folder": self.output.name})
        with patch.object(self.controller, "ask_ui", return_value=str(SAMPLE)):
            whole = self.finish({"source": "file"})["run"]["result"]
            self.call("/api/settings", {"report_mode": "range", "report_start": "2026-10-01", "report_end": "2026-10-08"})
            fixed = self.finish({"source": "file"})["run"]["result"]
        self.assertEqual((whole["referrals"], whole["compared_with"]), (16, None))
        self.assertEqual((fixed["referrals"], fixed["compared_with"]), (8, "Sep 24, 2026 – Oct 1, 2026"))

    def test_lost_window_is_reopened_then_falls_back_to_the_default_browser(self):
        app = self.app
        with patch.object(app, "open_window") as edge, patch.object(app.webbrowser, "open") as default:
            watch = app.WindowWatch("url", now=0)
            watch.check(False, now=app.WINDOW_WAIT_SECONDS - 1)
            self.assertEqual((edge.call_count, default.call_count), (0, 0))
            watch.check(False, now=app.WINDOW_WAIT_SECONDS)
            self.assertEqual((edge.call_count, default.call_count), (1, 0))
            watch.check(False, now=2 * app.WINDOW_WAIT_SECONDS)
            self.assertEqual((edge.call_count, default.call_count), (1, 1))
            watch.check(False, now=10 * app.WINDOW_WAIT_SECONDS)
            self.assertTrue(watch.done)
            self.assertEqual((edge.call_count, default.call_count), (1, 1))
            loaded = app.WindowWatch("url", now=0)
            loaded.check(True, now=1)
            loaded.check(False, now=100)
            self.assertTrue(loaded.done)
            self.assertEqual(edge.call_count, 1)

    def test_a_second_launch_can_ask_without_counting_as_the_window(self):
        self.controller.last_seen -= 100
        status, payload = self.call("/api/seen")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(payload["idle"], 100)
        self.assertFalse(self.controller.window_connected)
        self.assertEqual(self.call("/api/seen", token="wrong")[0], 403)
        self.call("/api/state")
        self.assertTrue(self.controller.window_connected)
        self.assertLess(self.call("/api/seen")[1]["idle"], 5)

    def test_window_files_and_fonts_are_served_but_nothing_else(self):
        for path, expected in (("/", 200), ("/styles.css", 200), ("/fonts/Nunito-Regular.ttf", 200),
                               ("/fonts/../settings.py", 404), ("/../settings.py", 404)):
            with self.subTest(path=path):
                try:
                    with urlopen(self.base + path) as response:
                        status = response.status
                except Exception as error:
                    with error:
                        status = error.code
                self.assertEqual(status, expected)

    def test_saving_settings_ignores_unknown_fields(self):
        status, _ = self.call("/api/settings", {"mission": "Utah Provo Mission", "output_folder": "C:/not-allowed"})
        self.assertEqual(status, 200)
        state = self.call("/api/state")[1]
        self.assertEqual(state["settings"]["mission"], "Utah Provo Mission")
        self.assertEqual(state["settings"]["output_folder"], "")
        status, payload = self.call("/api/settings", {"start_time": "23:00"})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
