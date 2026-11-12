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

    def test_district_summaries_compare_the_last_week_with_the_week_before(self):
        from referral_dash.period import summary_period
        settings = validate_settings({**SETTINGS, "summary_days": 3, "compare_days": 3})
        period = make_period(settings, "2026-10-01", "2026-10-08")
        weekly = summary_period(period, settings)
        self.assertEqual((weekly.start, weekly.end), (date(2026, 10, 6), date(2026, 10, 8)))
        self.assertEqual((weekly.baseline_start, weekly.baseline_end), (date(2026, 10, 3), date(2026, 10, 5)))
        with tempfile.TemporaryDirectory() as folder:
            _, facts = engine.generate_reports(SAMPLE, folder, settings, period=period)
        self.assertEqual(facts["period"], "Oct 1, 2026 – Oct 8, 2026")          # Dashboards: the whole report.
        self.assertEqual(facts["summary_period"], "Oct 6, 2026 – Oct 8, 2026")  # Summaries: the recent days.
        self.assertEqual(facts["comparison_period"], "Oct 3, 2026 – Oct 5, 2026")
        # A chosen comparison range is used as it is.
        fixed = summary_period(make_period(settings, "2026-10-01", "2026-10-08", "2026-09-20", "2026-09-26"), settings)
        self.assertEqual((fixed.baseline_start, fixed.baseline_end), (date(2026, 9, 20), date(2026, 9, 26)))

    def test_the_speed_dial_puts_the_goal_at_the_top(self):
        from referral_dash import gauges
        self.assertEqual([t for _, t in gauges.speed_scale(5)], ["0", "1", "2", "3", "4", "5", "10", "20", "40", "80", "160+"])
        self.assertEqual([t for _, t in gauges.speed_scale(10)], ["0", "2", "4", "6", "8", "10", "20", "40", "80", "160", "320+"])
        self.assertEqual(gauges.speed_fraction(0, 5), 0)
        self.assertEqual(gauges.speed_fraction(5, 5), 0.5)       # The goal is straight up.
        self.assertAlmostEqual(gauges.speed_fraction(2.5, 5), 0.25)
        self.assertAlmostEqual(gauges.speed_fraction(10, 5), 0.6)  # Each doubling is one mark.
        self.assertEqual(gauges.speed_fraction(100000, 5), 1.0)  # Very slow pins at the end.
        self.assertIsNone(gauges.speed_fraction(None, 5))

    def test_the_found_dial_reads_in_tens(self):
        from referral_dash import gauges
        self.assertEqual([t for _, t in gauges.found_scale()], [str(i) for i in range(11)])
        self.assertAlmostEqual(gauges.found_fraction(45), 0.45)
        self.assertEqual((gauges.found_fraction(-3), gauges.found_fraction(140)), (0, 1))

    def test_the_needles_follow_the_numbers(self):
        # The needle is drawn in the dial ring, so two different values light up different pixels there.
        with tempfile.TemporaryDirectory() as folder:
            def ring(minutes, found):
                target = Path(folder) / f"{minutes}-{found}.png"
                engine.DashboardTemplate(5, 20).buildImage("Fictional sample", minutes, found, 8, 75, 50, 50, [], target)
                return Image.open(target).convert("RGB")
            from referral_dash import gauges
            slow, fast = ring(160, 90), ring(1, 5)
            # Halfway along where the slow needle should be: needle pink there in one picture, dark in the other.
            spot = tuple(round(v) for v in gauges._point((444, 436), 142, gauges.speed_fraction(160, 5)))
            red, green, blue = slow.getpixel(spot)
            self.assertGreater(red, 180)
            self.assertLess(green, 120)
            self.assertLess(sum(fast.getpixel(spot)), 200)

    def test_a_long_unattempted_list_stops_growing_after_twenty_names(self):
        heights = {}
        with tempfile.TemporaryDirectory() as folder:
            for count in (8, 21, 36, 200):
                image = Path(folder) / f"{count}.png"
                people = [{"name": f"Example Person {i}", "date": "10/01/2026 02:00:00 PM", "area": "Sample Area"} for i in range(count)]
                engine.DashboardTemplate().buildImage("Fictional sample", None, 0, count, 0, 0, 0, people, image)
                with Image.open(image) as value:
                    heights[count] = value.height
        self.assertGreater(heights[21], heights[8])  # More rows, up to the cap.
        self.assertEqual(heights[21], heights[36])  # Past 20 names, only "+ N more" changes.
        self.assertEqual(heights[36], heights[200])

    def test_the_longest_waiting_are_listed_first(self):
        from referral_dash import template
        drawn = []
        real = template.ImageDraw.ImageDraw.text

        def record(draw, xy, text, *args, **kwargs):
            drawn.append(text)
            return real(draw, xy, text, *args, **kwargs)
        people = [{"name": f"Waited {day}", "date": f"10/{day:02d}/2026 02:00:00 PM", "area": "Sample Area"} for day in range(30, 0, -1)]
        with tempfile.TemporaryDirectory() as folder, patch.object(template.ImageDraw.ImageDraw, "text", record):
            engine.DashboardTemplate().buildImage("Fictional sample", None, 0, 30, 0, 0, 0, people, Path(folder) / "x.png")
        names = [t for t in drawn if t.startswith("Waited ")]
        self.assertEqual(names[0], "Waited 1")
        self.assertEqual(len(names), 20)
        self.assertIn("30 Unattempted Referrals", drawn)
        self.assertIn("+ 10 more", drawn)


class ComparisonTests(Isolated):
    def test_first_ever_run_shows_change_from_the_week_before(self):
        """Regression: a mission's first run used to say "No prior data" on every row."""
        with tempfile.TemporaryDirectory() as folder:
            output, facts = engine.generate_reports(SAMPLE, folder, SETTINGS, period=SAMPLE_PERIOD)
            self.assertEqual(facts["period"], "Oct 1, 2026 – Oct 8, 2026")
            self.assertEqual(facts["comparison_period"], "Sep 25, 2026 – Oct 1, 2026")
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
        # Summaries cover the last 7 days of the report, against the 7 days before.
        self.assertEqual(summary.call_args.args[4:6], ("Oct 2, 2026 – Oct 8, 2026", "Sep 25, 2026 – Oct 1, 2026"))

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
        values = validate_settings({**DEFAULTS, "timezones": {"default": "UTC", " NORTH ": "america/new_york"}})
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
        # Older short names are kept as the zones they always meant.
        self.assertEqual(loaded["timezones"], {"default": "America/New_York", "hawaii": "Pacific/Honolulu"})
        self.assertFalse({"drive_folder", "drive_folder_name", "tableau_sso_url"} & loaded.keys())

    def test_time_zone_guess_comes_from_this_computer(self):
        from referral_dash import settings
        # Windows names the zone exactly, anywhere in the world.
        for reported, expected in (("America/Lima", "America/Lima"), ("Asia/Manila", "Asia/Manila"), ("europe/london", "Europe/London")):
            with self.subTest(reported=reported), patch.object(settings, "_windows_zone", return_value=reported):
                self.assertEqual(settings.suggested_timezone(), expected)
        # Without it, the clock's offset still finds the US zones.
        for offset, daylight, expected in ((18000, 1, "America/New_York"), (21600, 1, "America/Chicago"), (25200, 1, "America/Denver"),
                                           (25200, 0, "America/Phoenix"), (36000, 0, "Pacific/Honolulu"), (-3600, 1, "UTC")):
            with self.subTest(offset=offset, daylight=daylight), patch.object(settings, "_windows_zone", return_value=None), \
                    patch.object(settings.time, "timezone", offset), patch.object(settings.time, "daylight", daylight):
                self.assertEqual(settings.suggested_timezone(), expected)

    def test_world_zones_are_supported_and_old_names_still_work(self):
        from referral_dash import settings
        from referral_dash.group import TZ_LOOKUP
        for zone in ("America/Mexico_City", "America/Sao_Paulo", "Asia/Manila", "Africa/Lagos", "Europe/London", "Pacific/Auckland"):
            self.assertIn(zone, settings.TIMEZONES)
            self.assertEqual(validate_settings({**DEFAULTS, "timezones": {"default": zone}})["timezones"]["default"], zone)
            self.assertEqual(str(TZ_LOOKUP[zone]), zone)
        self.assertEqual(str(TZ_LOOKUP["EST"]), "America/New_York")
        self.assertEqual(settings.zone_name("mst_no_dst"), "America/Phoenix")

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

        def fake_download(driver, settings, credentials, work, destination, period, progress, verification, found=None):
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


class FakeGoogle:
    """A stand-in for Google's sign-in and Drive servers that records what they were asked."""

    def __init__(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        fake = self
        self.items = {"picked": {"name": "Weekly Dashboards", "mimeType": "application/vnd.google-apps.folder",
                                 "capabilities": {"canAddChildren": True}, "parents": []}}
        self.token_requests, self.fail_uploads, self.next_id, self.lock = [], 0, 0, threading.Lock()
        self.flaky, self.refused_token, self.refuse_trash = 0, False, False

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def reply(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def body(self):
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))

            def add(self, item):
                with fake.lock:
                    fake.next_id += 1
                    file_id = f"id{fake.next_id}"
                    fake.items[file_id] = item
                return self.reply(200, {"id": file_id})

            def do_POST(self):
                body = self.body()
                if self.path.startswith("/token"):
                    from urllib.parse import parse_qs
                    fields = {k: v[0] for k, v in parse_qs(body.decode()).items()}
                    fake.token_requests.append(fields)
                    if fake.refused_token:
                        return self.reply(400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
                    return self.reply(200, {"access_token": "access", "expires_in": 3600, "refresh_token": "refresh"})
                if self.headers.get("Authorization") != "Bearer access":
                    return self.reply(401, {"error": {"message": "no token"}})
                if self.path.startswith("/upload"):
                    if fake.flaky:
                        fake.flaky -= 1
                        return self.reply(503, {"error": {"message": "try later"}})
                    if fake.fail_uploads:
                        return self.reply(403, {"error": {"message": "storage quota exceeded"}})
                    meta = json.loads(body.split(b"\r\n\r\n", 2)[1].split(b"\r\n")[0])
                    meta["size"] = len(body)
                    return self.add(meta)
                return self.add(json.loads(body))

            def do_GET(self):
                item = fake.items.get(self.path.split("/files/")[-1].split("?")[0])
                return self.reply(200, item) if item else self.reply(404, {"error": {"message": "not found"}})

            def do_PATCH(self):
                body = json.loads(self.body())
                item = fake.items.get(self.path.split("/files/")[-1].split("?")[0])
                if item is None:
                    return self.reply(404, {"error": {"message": "not found"}})
                if fake.refuse_trash:
                    return self.reply(403, {"error": {"message": "The user does not have sufficient permissions for this file."}})
                item.update(body)
                return self.reply(200, {"id": "ok"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class DriveTests(Isolated):
    def setUp(self):
        super().setUp()
        from referral_dash import drive
        self.drive = drive
        self.google = FakeGoogle()
        self.addCleanup(self.google.close)
        self.vault = {}
        keyring = patch("referral_dash.credentials.keyring")
        fake = keyring.start()
        self.addCleanup(keyring.stop)
        fake.get_password.side_effect = lambda service, name: self.vault.get(name)
        fake.set_password.side_effect = lambda service, name, value: self.vault.__setitem__(name, value)
        fake.delete_password.side_effect = lambda service, name: self.vault.pop(name, None)
        fake.errors.KeyringError = RuntimeError
        fake.errors.PasswordDeleteError = KeyError
        client = Path(self.home.name) / "client"
        client.mkdir()
        (client / drive.CLIENT_FILE).write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "sec"}}))
        for patcher in (patch.object(drive, "app_directory", return_value=client),
                        patch.object(drive, "TOKEN_URL", self.google.base + "/token"),
                        patch.object(drive, "API_URL", self.google.base + "/files"),
                        patch.object(drive, "UPLOAD_URL", self.google.base + "/upload"),
                        patch.object(drive.time, "sleep")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def browser(self, **answer):
        """Plays the person's browser: Google sends it back to the app after the Picker."""
        import threading
        from urllib.parse import parse_qs, urlencode, urlsplit
        self.asked = {}

        def open_browser(url):
            self.asked.update({k: v[0] for k, v in parse_qs(urlsplit(url).query).items()})
            query = urlencode({"state": self.asked["state"], **answer})
            threading.Thread(target=lambda: urlopen(f"{self.asked['redirect_uri']}?{query}").read(), daemon=True).start()
        return open_browser

    def connect(self):
        return self.drive.connect(self.browser(code="abc", picked_file_ids="picked"))

    def make_run(self, parent, name="Referral Reports - Oct 1 to Oct 8, 2026"):
        run = Path(parent) / name
        for folder, pictures in (("1. Areas", 3), ("5. District Summaries", 2)):
            (run / folder).mkdir(parents=True)
            for index in range(pictures):
                (run / folder / f"{index}.jpg").write_bytes(b"picture" * 50)
        (run / "run-summary.json").write_text("{}")
        return run

    def test_sign_in_asks_only_for_the_permission_without_a_warning(self):
        self.assertEqual(self.connect(), {"id": "picked", "name": "Weekly Dashboards"})
        self.assertEqual(self.asked["scope"], "https://www.googleapis.com/auth/drive.file")
        self.assertEqual((self.asked["trigger_onepick"], self.asked["allow_folder_selection"], self.asked["prompt"], self.asked["access_type"]),
                         ("true", "true", "consent", "offline"))
        self.assertEqual(self.asked["mimetypes"], self.drive.FOLDER_TYPE)
        self.assertTrue(self.asked["redirect_uri"].startswith("http://127.0.0.1:"))
        self.assertEqual(self.google.token_requests[0]["redirect_uri"], self.asked["redirect_uri"])
        self.assertIn("code_verifier", self.google.token_requests[0])
        self.assertEqual(self.vault["google-drive.v1"], "refresh")  # In Credential Manager, not a file.
        self.assertFalse((Path(self.home.name) / "google-token.json").exists())

    def test_cancelling_or_picking_nothing_connects_nothing(self):
        with self.assertRaisesRegex(self.drive.DriveError, "cancelled"):
            self.drive.connect(self.browser(error="access_denied"))
        with self.assertRaisesRegex(self.drive.DriveError, "No folder"):
            self.drive.connect(self.browser(code="abc"))
        self.assertEqual(self.vault, {})

    def test_cancelling_stops_waiting_for_the_browser(self):
        import threading
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaisesRegex(self.drive.DriveError, "cancelled"):
            self.drive.connect(lambda url: None, cancelled=cancelled)
        self.assertEqual(self.vault, {})

    def test_a_wrong_state_is_ignored(self):
        with patch.object(self.drive, "SIGN_IN_SECONDS", 2):
            def forged(url):
                from urllib.parse import parse_qs, urlsplit
                redirect = parse_qs(urlsplit(url).query)["redirect_uri"][0]
                import threading
                def visit():
                    try:
                        urlopen(f"{redirect}?state=forged&code=evil&picked_file_ids=picked").read()
                    except Exception:
                        pass
                threading.Thread(target=visit, daemon=True).start()
            with self.assertRaisesRegex(self.drive.DriveError, "timed out"):
                self.drive.connect(forged)
        self.assertEqual(self.google.token_requests, [])

    def test_picking_a_file_or_a_read_only_folder_is_refused(self):
        self.google.items["picked"]["mimeType"] = "image/jpeg"
        with self.assertRaisesRegex(self.drive.DriveError, "isn't a folder"):
            self.connect()
        self.assertEqual(self.vault, {})
        self.google.items["picked"].update(mimeType=self.drive.FOLDER_TYPE, capabilities={"canAddChildren": False})
        with self.assertRaisesRegex(self.drive.DriveError, "can't add files"):
            self.connect()

    def live_folders(self):
        return {k: v for k, v in self.google.items.items()
                if v.get("mimeType") == self.drive.FOLDER_TYPE and k != "picked" and not v.get("trashed")}

    def test_the_five_folders_go_straight_into_the_chosen_folder(self):
        self.connect()
        calls = []
        with tempfile.TemporaryDirectory() as folder:
            link = self.drive.upload_run(self.make_run(folder), "picked", lambda m, done=None, total=None: calls.append((done, total)))
        folders = self.live_folders()
        self.assertEqual({v["name"] for v in folders.values()}, {"1. Areas", "5. District Summaries"})
        self.assertTrue(all(v["parents"] == ["picked"] for v in folders.values()))
        files = [v for v in self.google.items.values() if "size" in v]
        self.assertEqual(len(files), 5)
        self.assertNotIn("run-summary.json", {f["name"] for f in files})
        self.assertEqual(calls[-1], (5, 5))
        self.assertEqual(link, self.drive.folder_link("picked"))

    def test_temporary_google_errors_are_retried(self):
        self.connect()
        self.google.flaky = 3
        with tempfile.TemporaryDirectory() as folder:
            self.drive.upload_run(self.make_run(folder), "picked")
        self.assertEqual(sum("size" in v for v in self.google.items.values()), 5)

    def test_rate_limits_reported_as_403_are_retried(self):
        from urllib.error import HTTPError
        import io
        attempts = []

        def fake_open(request, timeout):
            attempts.append(request.full_url)
            if len(attempts) < 3:
                raise HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(b'{"error": {"message": "User Rate Limit Exceeded"}}'))
            return unittest.mock.MagicMock(**{"__enter__.return_value.read.return_value": b'{"id": "ok"}'})
        with patch.object(self.drive, "urlopen", side_effect=fake_open):
            self.assertEqual(self.drive._send("POST", "https://example.test/x")["id"], "ok")
        self.assertEqual(len(attempts), 3)

    def test_failed_upload_trashes_the_partial_copy_and_keeps_the_last_one(self):
        self.connect()
        with tempfile.TemporaryDirectory() as folder:
            self.drive.upload_run(self.make_run(folder, "Week 1"), "picked")
            last = set(self.live_folders())
            self.google.fail_uploads = 1
            with self.assertRaisesRegex(self.drive.DriveError, "quota"):
                self.drive.upload_run(self.make_run(folder, "Week 2"), "picked")
        self.assertEqual(set(self.live_folders()), last)  # The last good copy is still there.
        self.assertEqual(self.drive.leftover_uploads("picked"), 0)

    def test_each_upload_replaces_the_last_copy_and_nothing_else(self):
        self.connect()
        self.google.items["theirs"] = {"name": "Someone's notes", "mimeType": self.drive.FOLDER_TYPE, "parents": ["picked"]}
        with tempfile.TemporaryDirectory() as folder:
            self.drive.upload_run(self.make_run(folder, "Week 1"), "picked")
            first = set(self.live_folders()) - {"theirs"}
            self.drive.upload_run(self.make_run(folder, "Week 2"), "picked")
        second = set(self.live_folders()) - {"theirs"}
        self.assertTrue(first and second and not first & second)
        self.assertTrue(all(self.google.items[k]["trashed"] for k in first))
        self.assertNotIn("trashed", self.google.items["theirs"])

    def test_sets_from_the_older_dated_layout_are_replaced_too(self):
        self.connect()
        self.google.items["oldset"] = {"name": "Referral Reports - Oct 1 to Oct 8, 2026", "mimeType": self.drive.FOLDER_TYPE, "parents": ["picked"]}
        (Path(self.home.name) / "drive-uploads.json").write_text(json.dumps({"picked": [{"id": "oldset", "name": "Referral Reports - Oct 1 to Oct 8, 2026"}]}))
        with tempfile.TemporaryDirectory() as folder:
            self.drive.upload_run(self.make_run(folder), "picked")
        self.assertTrue(self.google.items["oldset"]["trashed"])

    def test_a_copy_deleted_by_hand_is_forgotten_and_a_refused_one_is_retried(self):
        self.connect()
        with tempfile.TemporaryDirectory() as folder:
            self.drive.upload_run(self.make_run(folder, "Week 1"), "picked")
            gone = next(iter(self.live_folders()))
            del self.google.items[gone]
            self.google.refuse_trash = True
            self.drive.upload_run(self.make_run(folder, "Week 2"), "picked")
            self.assertEqual(self.drive.leftover_uploads("picked"), 1)  # One left, one deleted by hand.
            self.google.refuse_trash = False
            self.drive.upload_run(self.make_run(folder, "Week 3"), "picked")
        self.assertEqual(self.drive.leftover_uploads("picked"), 0)
        self.assertEqual(len(self.live_folders()), 2)  # Only the newest copy's two folders.

    def test_lost_connection_says_to_reconnect(self):
        self.connect()
        self.google.refused_token = True
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(self.drive.DriveError, "connected again"):
            self.drive.upload_run(self.make_run(folder), "picked")
        self.vault.clear()
        self.google.refused_token = False
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(self.drive.DriveError, "connected again"):
            self.drive.upload_run(self.make_run(folder), "picked")

    def test_a_copy_without_the_client_file_hides_the_option(self):
        with patch.object(self.drive, "app_directory", return_value=Path(self.home.name)), \
                patch.object(self.drive, "install_directory", return_value=Path(self.home.name)):
            self.assertFalse(self.drive.available())
            with self.assertRaisesRegex(self.drive.DriveError, "isn't set up"):
                self.drive.connect(lambda url: None)


class MissionListTests(unittest.TestCase):
    def test_the_list_ships_and_includes_real_missions(self):
        from referral_dash import missions
        names = missions.load()
        self.assertGreater(len(names), 400)
        self.assertIn("Tennessee Knoxville Mission", names)
        self.assertTrue(all(name.endswith("Mission") for name in names))
        self.assertEqual(len(names), len(set(names)))

    def test_accents_and_capitals_still_find_tableaus_spelling(self):
        from referral_dash.tableau.dashboard import tableau_spelling
        choices = ["(All)", "Mexico Mexico City East Mission", "Idaho Coeur d'Alene Mission", "Missouri St. Louis Mission"]
        self.assertEqual(tableau_spelling(("México México City East Mission",), choices), ("Mexico Mexico City East Mission",))
        self.assertEqual(tableau_spelling(("idaho coeur d’alene mission",), choices), ("Idaho Coeur d'Alene Mission",))
        self.assertEqual(tableau_spelling(("Missouri St Louis Mission",), choices), ("Missouri St. Louis Mission",))
        self.assertEqual(tableau_spelling(("Nowhere Mission",), choices), ("Nowhere Mission",))


class TimeZoneTests(Isolated):
    def test_a_new_install_follows_this_computer(self):
        from referral_dash import settings
        with patch.object(settings, "suggested_timezone", return_value="America/Lima"):
            loaded = settings.load_settings()
        self.assertTrue(loaded["timezone_auto"])
        self.assertEqual(loaded["timezones"]["default"], "America/Lima")

    def test_a_picked_zone_stays_even_on_another_computer(self):
        from referral_dash import settings
        settings.save_settings({**settings.load_settings(), "timezone_auto": False, "timezones": {"default": "Pacific/Honolulu"}})
        with patch.object(settings, "suggested_timezone", return_value="America/New_York"):
            self.assertEqual(settings.load_settings()["timezones"]["default"], "Pacific/Honolulu")

    def test_older_settings_keep_a_chosen_zone_and_follow_the_computer_otherwise(self):
        from referral_dash import settings
        path = settings.settings_path()
        older = {k: v for k, v in settings.DEFAULTS.items() if k != "timezone_auto"}
        path.write_text(json.dumps({**older, "timezones": {"default": "PST"}}))
        with patch.object(settings, "suggested_timezone", return_value="America/New_York"):
            self.assertEqual(settings.load_settings()["timezones"]["default"], "America/Los_Angeles")
            self.assertFalse(settings.load_settings()["timezone_auto"])
        path.write_text(json.dumps({**older, "timezones": {"default": "EST"}}))
        with patch.object(settings, "suggested_timezone", return_value="America/New_York"):
            self.assertTrue(settings.load_settings()["timezone_auto"])
        path.write_text(json.dumps(older))
        with patch.object(settings, "suggested_timezone", return_value="America/New_York"):
            self.assertEqual(settings.load_settings()["timezones"]["default"], "America/New_York")


class MissionDetectionTests(unittest.TestCase):
    def test_the_only_mission_tableau_shows_is_used(self):
        from referral_dash.tableau.dashboard import choose_mission
        self.assertEqual(choose_mission(["Tennessee Knoxville Mission"]), "Tennessee Knoxville Mission")

    def test_several_missions_need_the_typed_one(self):
        from referral_dash.tableau.dashboard import choose_mission
        choices = ["Mexico Mexico City East Mission", "Tennessee Knoxville Mission"]
        self.assertEqual(choose_mission(choices, "México Mexico City East Mission"), "Mexico Mexico City East Mission")
        with self.assertRaisesRegex(RuntimeError, "can see 2 missions"):
            choose_mission(choices)
        with self.assertRaisesRegex(RuntimeError, "no missions"):
            choose_mission([])


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
        # The mission is found in Tableau, so a Tableau run only needs the Church account.
        status, payload = self.call("/api/run", {"source": "tableau"})
        self.assertEqual(status, 400)
        self.assertIn("Church account", payload["error"])
        # A CSV can't say which mission it is until a Tableau run has found it.
        with patch.object(self.app.Controller, "ask_ui", return_value=None):
            status, payload = self.call("/api/run", {"source": "file"})
        self.assertEqual(status, 400)
        self.assertIn("find your mission", payload["error"])
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
        self.assertEqual(result["compared_with"], "Sep 25, 2026 – Oct 1, 2026")
        self.assertEqual(result["referrals"], 8)

    def connected(self):
        from referral_dash.settings import load_settings, save_settings
        save_settings({**load_settings(), "mission": "Sample Mission", "output_folder": self.output.name,
                       "drive_upload_folder_id": "folder1", "drive_upload_folder_name": "Weekly"})

    def test_runs_upload_after_saving_and_a_failure_keeps_the_reports(self):
        self.connected()
        with patch.object(self.app.drive, "upload_run", return_value=self.app.drive.folder_link("new1")) as upload, \
                patch.object(self.app.drive, "leftover_uploads", return_value=0), \
                patch.object(self.controller, "ask_ui", return_value=str(SAMPLE)):
            state = self.finish({"source": "file"})
        result = state["run"]["result"]
        self.assertEqual(result["drive_link"], self.app.drive.folder_link("new1"))
        self.assertEqual(upload.call_args.args[1], "folder1")
        self.assertEqual(state["run"]["steps"][-1]["detail"], "Weekly")
        with patch.object(self.app.drive, "upload_run", side_effect=self.app.drive.DriveError("Drive said no.")), \
                patch.object(self.controller, "ask_ui", return_value=str(SAMPLE)):
            state = self.finish({"source": "file"})
        result = state["run"]["result"]
        self.assertEqual((result["drive_link"], result["drive_error"]), (None, "Drive said no."))
        self.assertTrue(Path(result["folder"]).is_dir())
        self.assertEqual(state["run"]["steps"][-1]["state"], "failed")

    def test_sample_data_never_uploads(self):
        self.connected()
        with patch.object(self.app.drive, "upload_run") as upload:
            state = self.finish({"source": "sample"})
        upload.assert_not_called()
        self.assertNotIn("upload", [s["id"] for s in state["run"]["steps"]])

    def test_settings_cannot_set_the_drive_folder_and_disconnect_clears_it(self):
        self.call("/api/settings", {"mission": "Sample Mission", "drive_upload_folder_id": "sneaky"})
        self.assertFalse(self.call("/api/state")[1]["drive"]["connected"])
        self.connected()
        drive_state = self.call("/api/state")[1]["drive"]
        self.assertEqual((drive_state["connected"], drive_state["folder_name"]), (True, "Weekly"))
        with patch("referral_dash.credentials.keyring.errors.PasswordDeleteError", KeyError):
            self.assertEqual(self.call("/api/drive/disconnect", {})[0], 200)
        self.assertFalse(self.call("/api/state")[1]["drive"]["connected"])

    def wait_for_drive(self):
        import time
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            drive_state = self.call("/api/state")[1]["drive"]
            if not drive_state["connecting"]:
                return drive_state
            time.sleep(0.02)
        self.fail("Drive stayed in connecting.")

    def test_cancel_clears_connecting_without_showing_an_error(self):
        import threading
        started = threading.Event()

        def waits(open_browser=None, cancelled=None):
            started.set()
            cancelled.wait(10)
            raise self.app.drive.DriveError("Google sign-in was cancelled.")
        with patch.object(self.app.drive, "connect", side_effect=waits):
            self.call("/api/drive/connect", {})
            self.assertTrue(started.wait(5))
            self.assertTrue(self.call("/api/state")[1]["drive"]["connecting"])
            self.assertEqual(self.call("/api/drive/cancel", {})[0], 200)
            drive_state = self.wait_for_drive()
        self.assertEqual((drive_state["error"], drive_state["connected"]), ("", False))

    def test_the_window_gets_the_mission_list(self):
        status, payload = self.call("/api/missions")
        self.assertEqual(status, 200)
        self.assertIn("Tennessee Knoxville Mission", payload["missions"])

    def test_the_window_starts_the_folder_picker(self):
        with patch.object(self.app.drive, "connect", return_value={"id": "shared", "name": "Zone Reports"}):
            self.assertEqual(self.call("/api/drive/connect", {})[0], 200)
            drive_state = self.wait_for_drive()
        self.assertEqual((drive_state["connected"], drive_state["folder_name"]), (True, "Zone Reports"))

    def test_only_drive_links_open_in_the_browser(self):
        with patch.object(self.app.webbrowser, "open") as opened, patch.object(self.app.os, "startfile", create=True) as started:
            self.call("/api/open", {"target": self.app.drive.folder_link("abc")})
            self.call("/api/open", {"target": "https://evil.example/"})
        opened.assert_called_once_with(self.app.drive.folder_link("abc"))
        started.assert_not_called()

    def test_saved_dates_reach_the_download_and_the_report(self):
        from referral_dash.credentials import LoginCredentials
        from referral_dash.settings import load_settings, save_settings
        # A year of "past days" keeps the October sample inside the default range whatever today's date is.
        save_settings({**load_settings(), "mission": "Sample Mission", "output_folder": self.output.name, "lookback_days": 365})
        seen = []

        def download(settings, login, folder, period, progress, attention, found=None):
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
        self.assertEqual(fixed["run"]["result"]["compared_with"], "Sep 25, 2026 – Oct 1, 2026")
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
        self.assertEqual((fixed["referrals"], fixed["compared_with"]), (8, "Sep 25, 2026 – Oct 1, 2026"))

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
