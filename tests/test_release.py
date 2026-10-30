import csv
import json
import os
import tempfile
import time
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from PIL import Image, ImageChops

from referral_dash import engine
from referral_dash.district_summary import BAD, GOOD, MUTED, percent_change, period_label, trend
from referral_dash.group import ReferralPerson
from referral_dash.history import History, select_previous
from referral_dash.settings import DEFAULTS, app_directory, validate_settings

SAMPLE = app_directory() / "sample-data" / "sample-referrals.csv"
SETTINGS = validate_settings({"mission": "Sample Mission"})


class Isolated(unittest.TestCase):
    """Every test gets its own settings/history folder instead of the user's AppData."""

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
            output, facts = engine.generate_reports(SAMPLE, folder, SETTINGS)
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
            again, _ = engine.generate_reports(SAMPLE, folder, SETTINGS)
            self.assertNotEqual(output, again)
            self.assertEqual((previous / "keep.txt").read_text(), "existing output")

    def test_progress_reports_every_image(self):
        calls = []
        with tempfile.TemporaryDirectory() as folder:
            engine.generate_reports(SAMPLE, folder, SETTINGS, lambda m, done=None, total=None: calls.append((done, total)))
        counted = [c for c in calls if c[1]]
        self.assertEqual(len(counted), 11)
        self.assertEqual(counted[-1], (10, 11))

    def test_failed_render_never_publishes_partial_folder_or_history(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(engine, "render_summary", side_effect=OSError("summary cannot save")):
            history = History(SETTINGS)
            with self.assertRaisesRegex(OSError, "summary cannot save"):
                engine.generate_reports(SAMPLE, folder, SETTINGS, history=history)
            self.assertEqual(list(Path(folder).iterdir()), [])
            self.assertEqual(history.snapshots(), [])

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
                self.assertEqual(list(Path(folder).glob("Dashboard_*")), [])

    def test_real_tableau_shapes_date_only_milestones_and_unplaced_rows(self):
        def shape(rows):
            rows = [{**row, "First New Person Being Taught Date": "8/30/2026" if row["First New Person Being Taught Date"] else ""}
                    for row in rows] * 3
            return rows + [{**rows[0], "Latest Zone Name": "", "Latest District Name": "", "Latest Teaching Area Name": ""}]
        with tempfile.TemporaryDirectory() as folder:
            mission, facts = engine.read_export(self.rewrite_sample(folder, shape), SETTINGS)
            self.assertEqual((facts["referrals"], facts["skipped_rows"]), (24, 1))
            self.assertEqual(mission.percent_found, 25)
            mostly_unplaced = lambda rows: rows[:1] + [{**row, "Latest Zone Name": "Null"} for row in rows[1:]]
            with self.assertRaisesRegex(ValueError, "no area assignment"):
                engine.read_export(self.rewrite_sample(folder, mostly_unplaced), SETTINGS)
            timed_column_without_time = lambda rows: [{**rows[0], "First Contact Attempt Event Date": "10/01/2026"}]
            with self.assertRaisesRegex(ValueError, "hh:mm:ss"):
                engine.read_export(self.rewrite_sample(folder, timed_column_without_time), SETTINGS)

    def test_utf8_comma_export(self):
        with tempfile.TemporaryDirectory() as folder:
            mission, _ = engine.read_export(self.rewrite_sample(folder), SETTINGS)
            self.assertEqual(mission.total_referrals, 8)

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
    def snapshot(self, days_ago, digest):
        return {"generated_utc": (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(), "input_sha256": digest}

    def test_prefers_last_weeks_run_then_latest_earlier_run(self):
        now = datetime.now(timezone.utc)
        runs = [self.snapshot(1, "a"), self.snapshot(6, "b"), self.snapshot(13, "c")]
        self.assertEqual(select_previous(runs, "new", now)["input_sha256"], "b")
        self.assertEqual(select_previous(runs[:1], "new", now)["input_sha256"], "a")
        self.assertIsNone(select_previous([self.snapshot(6, "same")], "same", now))
        self.assertIsNone(select_previous([], "new", now))

    def test_second_run_compares_automatically_and_history_is_scoped_to_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            history = History(SETTINGS)
            earlier = self.rewrite_sample(folder, lambda rows: [{**row, "First Contact Attempt Event Date": ""} for row in rows])
            _, first = engine.generate_reports(earlier, folder, SETTINGS, history=history)
            self.assertIsNone(first["comparison_generated_utc"])
            _, second = engine.generate_reports(SAMPLE, folder, SETTINGS, history=history)
            self.assertEqual(second["comparison_generated_utc"], first["generated_utc"])
            self.assertEqual(len(history.snapshots()), 2)
            # Same CSV again: never compared with itself, so it compares with the first run.
            _, third = engine.generate_reports(SAMPLE, folder, SETTINGS, history=history)
            self.assertEqual(third["comparison_generated_utc"], first["generated_utc"])
            other = History(validate_settings({"mission": "Other Mission"}))
            self.assertEqual(other.snapshots(), [])
            # Where reports are saved and how many are kept do not change the numbers, so they keep the same history.
            moved = History(validate_settings({**SETTINGS, "output_folder": "D:/elsewhere", "keep_reports": 2}))
            self.assertEqual(len(moved.snapshots()), 3)

    def test_version_one_reports_in_the_output_folder_become_baselines(self):
        with tempfile.TemporaryDirectory() as folder:
            old, facts = engine.generate_reports(self.rewrite_sample(folder), folder, SETTINGS)
            summary = json.loads((old / "run-summary.json").read_text())
            summary["generated_utc"] = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            summary["input_sha256"] = "0" * 64
            (old / "run-summary.json").write_text(json.dumps(summary))
            history = History(SETTINGS)
            self.assertEqual(history.import_reports(folder), 1)
            self.assertEqual(history.import_reports(folder), 0)
            _, new = engine.generate_reports(SAMPLE, folder, SETTINGS, history=history)
            self.assertEqual(new["comparison_generated_utc"], summary["generated_utc"])

    def test_damaged_history_file_is_ignored(self):
        history = History(SETTINGS)
        history.folder.mkdir(parents=True)
        (history.folder / "broken.json").write_text("{not json")
        self.assertEqual(history.snapshots(), [])

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

        def fake_download(driver, settings, credentials, work, destination, progress, verification):
            verification()  # The Church sign-in shows an authenticator prompt.
            return "export.csv"
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(download, "create_browser", side_effect=lambda work, visible: opened.append(visible) or unittest.mock.MagicMock()), \
                patch.object(download, "_download", side_effect=fake_download):
            result = download.download_dashboard(SETTINGS, None, folder, attention=asked.append)
        self.assertEqual(result, "export.csv")
        self.assertEqual(opened, [False, True])  # Hidden first, then visible for the person.
        self.assertEqual(asked, [download.VERIFY_MESSAGE])
        self.assertTrue(issubclass(VerificationNeeded, RuntimeError))

    def test_download_date_check_rejects_unfiltered_exports(self):
        from referral_dash.tableau.download import validate_date_range
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "export.csv"
            path.write_text("Event Date Selected\tFull Name\n10/01/2026\tA\n10/20/2026\tB\n", encoding="utf-16")
            validate_date_range(path, datetime(2026, 10, 1).date())
            with self.assertRaisesRegex(ValueError, "not applied"):
                validate_date_range(path, datetime(2026, 10, 2).date())
            path.write_text("Full Name\nA\n", encoding="utf-16")
            with self.assertRaisesRegex(ValueError, "Event Date Selected"):
                validate_date_range(path, datetime(2026, 10, 2).date())


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
            runs = [self.make_run(folder, f"Dashboard_2026-10-{day:02d}_090000_abc123") for day in (1, 8, 15, 22, 29)]
            unfinished = self.make_run(folder, "Dashboard_2026-09-01_090000_old000", finished=False)
            mine = Path(folder) / "My notes"
            mine.mkdir()
            removed = prune_reports(folder, 2)
            self.assertEqual(sorted(removed), runs[:3])
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
        self.assertEqual(len(list((data_directory() / "sample").glob("Dashboard_*"))), 1)

    def test_csv_runs_keep_only_the_newest_report_sets(self):
        from referral_dash.settings import load_settings, save_settings
        save_settings({**load_settings(), "mission": "Sample Mission", "output_folder": self.output.name, "keep_reports": 2})
        # Three different exports, so each run is its own comparison baseline.
        inputs = []
        for keep in (8, 7, 6):
            folder = Path(tempfile.mkdtemp(dir=self.home.name))
            inputs.append(str(self.rewrite_sample(folder, lambda rows, keep=keep: rows[:keep])))
        with patch.object(self.controller, "ask_ui", side_effect=inputs):
            results = [self.finish({"source": "file"})["run"]["result"] for _ in range(3)]
        self.assertEqual([r["removed"] for r in results], [0, 0, 1])
        state = self.call("/api/state")[1]
        self.assertEqual([r["name"] for r in state["recent"]], [Path(results[i]["folder"]).name for i in (2, 1)])
        # Comparison history is kept separately, so deleting report sets loses no baselines.
        self.assertEqual(len(History(SETTINGS).snapshots()), 3)
        self.assertIsNotNone(results[2]["compared_with"])

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
