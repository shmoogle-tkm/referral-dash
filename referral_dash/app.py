"""Local control center: a loopback web server shown in an Edge/Chrome app window.

Modeled on the Referral Care Data Hub control center.
"""
from __future__ import annotations

import json
import mimetypes
import os
import queue
import secrets
import shutil
import subprocess
import threading
import time
import traceback
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from . import credentials
from .cleanup import prune_app_data, prune_reports, report_runs
from .engine import generate_reports
from .district_summary import period_label
from .period import default_range, make_period
from .settings import (TIMEZONES, VERSION, app_directory, data_directory, is_configured, load_settings,
                       save_settings, suggested_timezone, validate_settings)

HOST = "127.0.0.1"
WEB_ROOT = app_directory() / "web"
FONT_ROOT = app_directory() / "fonts"
SAMPLE = app_directory() / "sample-data" / "sample-referrals.csv"
# The sample's rows run from Sep 24; this range leaves the week before it for the comparison.
SAMPLE_RANGE = ("2026-10-01", "2026-10-08")
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
IDLE_SECONDS = 150
# A window that has not loaded the page by then is reopened; the last try uses the default browser.
WINDOW_WAIT_SECONDS = 8
WINDOW_ATTEMPTS = 3
KEEP_DOWNLOADS = 3
RECENT_RUNS = 8
STEPS = [("download", "Get referrals"), ("render", "Make dashboards"), ("summaries", "District summaries")]


def default_output_folder():
    return Path.home() / "Documents" / "Referral Dash reports"


def sample_folder():
    """Sample runs stay out of the real reports folder, and only the latest is kept."""
    return data_directory() / "sample"


class Controller:
    """All state the window shows; every mutation happens under one lock."""

    def __init__(self):
        self.lock = threading.RLock()
        self.run = None
        self.ui_tasks = queue.Queue()
        self.last_seen = time.monotonic()
        self.window_connected = False

    def seen(self):
        self.last_seen = time.monotonic()
        self.window_connected = True

    # ----- state -----
    def state(self):
        settings = load_settings()
        login = credentials.load_login()
        output = settings["output_folder"] or str(default_output_folder())
        with self.lock:
            run = json.loads(json.dumps(self.run)) if self.run else None
        return {
            "version": VERSION, "timezones": TIMEZONES, "settings": settings,
            "configured": is_configured(settings), "suggested_timezone": suggested_timezone(),
            "output_folder": output,
            "account": {"email": login.email, "username": login.username, "saved": login.complete},
            "run": run, "recent": self.recent(output),
            "default_range": [day.isoformat() for day in default_range(settings)],
        }

    def recent(self, output):
        runs = []
        for folder in report_runs(output)[:RECENT_RUNS]:
            try:
                facts = json.loads((folder / "run-summary.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            runs.append({"folder": str(folder), "name": folder.name, "generated_utc": facts.get("generated_utc"),
                         "referrals": facts.get("referrals"), "images": facts.get("images"),
                         "district_summaries": facts.get("district_summaries"),
                         "period": facts.get("period") or self._legacy_period(facts),
                         "compared_with": facts.get("comparison_period") or self._legacy_comparison(facts),
                         "mission": facts.get("settings", {}).get("mission")})
        return runs

    @staticmethod
    def _legacy_period(facts):
        """Reports made before 2.2 only recorded the dates of their rows."""
        first, last = facts.get("first_referral_date"), facts.get("last_referral_date")
        return period_label(first, last) if first and last else None

    @staticmethod
    def _legacy_comparison(facts):
        """Reports made before 2.2 compared with an earlier run, recorded by when it was made."""
        made = facts.get("comparison_generated_utc")
        return f"run of {datetime.fromisoformat(made):%b %d}" if made else None

    # ----- settings -----
    def save_settings(self, body):
        current = load_settings()
        allowed = {"mission", "lightning", "pepper", "start_time", "cutoff_time", "timezones",
                   "tableau_view_url", "lookback_days", "show_browser", "keep_reports"}
        return save_settings({**current, **{k: v for k, v in body.items() if k in allowed}})

    def save_account(self, body):
        credentials.save_login(str(body.get("email", "")), str(body.get("username", "")), str(body.get("password", "")))

    def pick_output_folder(self):
        folder = self.ask_ui("folder")
        if folder:
            save_settings({**load_settings(), "output_folder": folder})
        return folder

    def ask_ui(self, kind):
        """Run a native picker on the main thread (Tk must stay on one thread) and wait for it."""
        answer = queue.Queue(maxsize=1)
        self.ui_tasks.put((kind, answer))
        return answer.get()

    # ----- runs -----
    def start_run(self, source, watch=False, start=None, end=None, compare_start=None, compare_end=None):
        """`watch` shows the Tableau browser for this run only, e.g. to see why sign-in failed.

        The dates apply to this run only, so next week's run is not stuck on an old range.
        A CSV without them is reported whole, since only its author knows what it covers.
        """
        settings = load_settings()
        if watch:
            settings = {**settings, "show_browser": True}
        login = credentials.load_login()
        if source != "sample":
            if not is_configured(settings):
                raise ValueError("Open Settings and enter your mission name exactly as Tableau shows it.")
            if source == "tableau" and not login.complete:
                raise ValueError("Open Settings and save the Church account used for Tableau.")
        if source == "sample":
            period = make_period(settings, *SAMPLE_RANGE)
        else:
            period = (make_period(settings, start, end, compare_start, compare_end)
                      if source == "tableau" or start or end else None)
        filename = None
        if source == "file":
            filename = self.ask_ui("file")
            if not filename:
                return None
        with self.lock:
            if self.run and self.run["status"] == "running":
                raise ValueError("A run is already in progress.")
            self.run = {"id": secrets.token_hex(4), "status": "running", "source": source,
                        "started": datetime.now(timezone.utc).isoformat(), "finished": None,
                        "message": "Starting...", "attention": "", "percent": 1, "error": "", "result": None,
                        "steps": [{"id": i, "label": label, "state": "pending", "detail": ""} for i, label in STEPS]}
            if source != "tableau":
                self._step("download", "done", "Sample data" if source == "sample" else Path(filename).name)
        threading.Thread(target=self._work, args=(source, filename, settings, login, period), daemon=True).start()
        return self.run["id"]

    def _step(self, step_id, state, detail=None):
        with self.lock:
            for step in self.run["steps"]:
                if step["id"] == step_id:
                    step["state"] = state
                    if detail is not None:
                        step["detail"] = detail

    def _progress(self, message, percent=None):
        with self.lock:
            self.run["message"] = message
            self.run["attention"] = ""
            if percent is not None:
                self.run["percent"] = max(self.run["percent"], min(99, round(percent)))

    def _attention(self, message):
        """The run is waiting on the person, e.g. for an authenticator approval."""
        with self.lock:
            self.run["attention"] = message

    def _work(self, source, filename, settings, login, period):
        current = "download"
        try:
            output = sample_folder() if source == "sample" else Path(settings["output_folder"] or default_output_folder())
            output.mkdir(parents=True, exist_ok=True)
            if source == "tableau":
                self._step("download", "active")
                from .tableau.download import download_dashboard
                downloads = data_directory() / "downloads"
                filename = download_dashboard(settings, login, downloads, period, lambda m: self._progress(m), self._attention)
                for old in sorted(downloads.glob("tableau_export_*.csv"))[:-KEEP_DOWNLOADS]:
                    old.unlink(missing_ok=True)
                self._step("download", "done", "Downloaded from Tableau")
                self._progress("Downloaded from Tableau", 35)
            elif source == "sample":
                filename = SAMPLE
                settings = validate_settings({**settings, "mission": "Sample Mission"})
            current = "render"
            self._step("render", "active")
            base = 35 if source == "tableau" else 5
            span = 80 - base

            def progress(message, done=None, total=None):
                if message.startswith("Creating district summary"):
                    self._step("render", "done")
                    self._step("summaries", "active")
                fraction = done / total if done is not None and total else None
                self._progress(message, base + span * fraction if fraction is not None else None)
            folder, facts = generate_reports(filename, output, settings, progress, period=period,
                                             baseline_covered=source == "tableau")
            skipped = facts["skipped_rows"]
            self._step("render", "done", f"{facts['images'] - facts['district_summaries']} dashboards" +
                       (f" · {skipped} row{'s' * (skipped != 1)} without an area left out" if skipped else ""))
            compared = facts["comparison_period"]
            self._step("summaries", "done", f"{facts['district_summaries']} districts" +
                       (f" · vs {compared}" if compared else " · comparison dates not in this file"))
            removed = prune_reports(output, 1 if source == "sample" else settings["keep_reports"])
            with self.lock:
                self.run.update(status="done", percent=100, finished=datetime.now(timezone.utc).isoformat(),
                                message="Finished", result={"folder": str(folder), "images": facts["images"],
                                "dashboards": facts["images"] - facts["district_summaries"],
                                "summaries": facts["district_summaries"], "referrals": facts["referrals"],
                                "period": facts["period"], "compared_with": compared, "removed": 0 if source == "sample" else len(removed)})
        except Exception as exc:
            traceback.print_exc()
            self._step(current, "failed", "")
            with self.lock:
                self.run.update(status="failed", finished=datetime.now(timezone.utc).isoformat(),
                                message="Run failed", error=str(exc) or type(exc).__name__)

    def open_target(self, target):
        path = Path(target)
        # Only folders: the window never needs to launch a file.
        if path.is_dir():
            os.startfile(str(path))


class Handler(BaseHTTPRequestHandler):
    controller: Controller
    token: str

    def log_message(self, format, *args):
        pass

    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status=HTTPStatus.OK):
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

    def _authorized(self):
        return secrets.compare_digest(self.headers.get("X-Session", ""), self.token)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            return self._json({"ok": True})
        if path.startswith("/api/"):
            if not self._authorized():
                return self._json({"error": "Unauthorized"}, HTTPStatus.FORBIDDEN)
            if path == "/api/seen":
                # Asked by a second launch; it must not count as the window being open.
                return self._json({"idle": time.monotonic() - self.controller.last_seen})
            self.controller.seen()
            if path == "/api/state":
                try:
                    return self._json(self.controller.state())
                except Exception as exc:
                    return self._json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        # The window uses the same bundled fonts as the report pictures.
        root, name = (FONT_ROOT, name[len("fonts/"):]) if name.startswith("fonts/") else (WEB_ROOT, name)
        target = (root / name).resolve()
        if root.resolve() not in target.parents or not target.is_file():
            return self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")
        self._send(HTTPStatus.OK, target.read_bytes(), mimetypes.guess_type(target.name)[0] or "application/octet-stream")

    def do_POST(self):
        if not self._authorized():
            return self._json({"error": "Unauthorized"}, HTTPStatus.FORBIDDEN)
        self.controller.seen()
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json({"error": "Invalid request"}, HTTPStatus.BAD_REQUEST)
        c = self.controller
        actions = {
            "/api/heartbeat": lambda: None,
            "/api/settings": lambda: c.save_settings(body) and None,
            "/api/account": lambda: c.save_account(body),
            "/api/account/forget": credentials.forget_login,
            "/api/output-folder": c.pick_output_folder,
            "/api/run": lambda: c.start_run(str(body.get("source", "tableau")), bool(body.get("watch")),
                                            body.get("start"), body.get("end"),
                                            body.get("compare_start"), body.get("compare_end")),
            "/api/open": lambda: c.open_target(str(body.get("target", ""))),
        }
        action = actions.get(self.path)
        if action is None:
            return self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        try:
            result = action()
        except (ValueError, RuntimeError) as exc:
            return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            traceback.print_exc()
            return self._json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
        self._json({"ok": True, "result": result})


def _browser():
    candidates = [shutil.which("msedge"),
                  os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
                  os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
                  shutil.which("chrome"),
                  os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
                  os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe")]
    return next((c for c in candidates if c and Path(c).exists()), None)


def open_window(url):
    browser = _browser()
    if browser:
        profile = data_directory() / "window"
        subprocess.Popen([browser, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1100,860",
                          "--no-first-run", "--no-default-browser-check", "--disable-component-update"],
                         creationflags=CREATE_NO_WINDOW)
    else:
        webbrowser.open(url)


class WindowWatch:
    """Reopen a window that never loads the page.

    Edge hands a new window to a running instance of the same profile; when that
    instance is still closing (the previous window was just shut), the new window is lost.
    """

    def __init__(self, url, now=None):
        self.url = url
        self.attempts = 1
        self.launched = time.monotonic() if now is None else now
        self.done = False

    def check(self, connected, now=None):
        """Call about once a second; `connected` means the page has loaded since the last launch."""
        now = time.monotonic() if now is None else now
        if connected:
            self.done = True
        if self.done or now - self.launched < WINDOW_WAIT_SECONDS:
            return
        if self.attempts >= WINDOW_ATTEMPTS:
            self.done = True
            return
        self.attempts += 1
        self.launched = now
        (open_window if self.attempts < WINDOW_ATTEMPTS else webbrowser.open)(self.url)


def _show_existing(info):
    """Open a window onto the copy that is already running, and make sure it loads."""
    url = f"http://{HOST}:{info['port']}/#t={info['token']}"
    open_window(url)
    watch = WindowWatch(url)
    request = Request(f"http://{HOST}:{info['port']}/api/seen", headers={"X-Session": info["token"]})
    while not watch.done:
        time.sleep(1)
        try:
            with urlopen(request, timeout=2) as response:
                idle = json.loads(response.read())["idle"]
        except (OSError, ValueError, KeyError, URLError):
            return  # The running copy has closed; nothing left to show.
        watch.check(idle < time.monotonic() - watch.launched)


def _existing_instance():
    marker = data_directory() / "instance.json"
    try:
        info = json.loads(marker.read_text(encoding="utf-8"))
        with urlopen(Request(f"http://{HOST}:{info['port']}/api/health"), timeout=0.8) as response:
            if response.status == 200:
                return info
    except (OSError, ValueError, KeyError, URLError):
        pass
    return None


def _run_picker(kind, settings):
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.lift()
    root.focus_force()
    try:
        if kind == "folder":
            return filedialog.askdirectory(parent=root, title="Choose where Referral Dash saves reports",
                                           initialdir=settings["output_folder"] or str(Path.home())) or None
        return filedialog.askopenfilename(parent=root, title="Choose a Tableau CSV download",
                                          filetypes=[("Tableau downloads", "*.csv *.tsv *.txt"), ("All files", "*.*")]) or None
    finally:
        root.destroy()


def main():
    existing = _existing_instance()
    if existing:
        _show_existing(existing)
        return
    # No other copy is running, so the browser profiles are free to tidy.
    prune_app_data()
    controller = Controller()
    token = secrets.token_urlsafe(32)
    handler = type("BoundHandler", (Handler,), {"controller": controller, "token": token})
    server = ThreadingHTTPServer((HOST, 0), handler)
    port = server.server_address[1]
    (data_directory() / "instance.json").write_text(json.dumps({"port": port, "token": token}), encoding="utf-8")
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True).start()
    url = f"http://{HOST}:{port}/#t={token}"
    watch = None
    if os.getenv("REFERRAL_DASH_NO_WINDOW") == "1":
        print(url, flush=True)  # For automated UI checks.
    else:
        open_window(url)
        watch = WindowWatch(url)
    try:
        # The main thread owns native dialogs and decides when the closed window means "quit".
        while True:
            try:
                kind, answer = controller.ui_tasks.get(timeout=1)
                try:
                    answer.put(_run_picker(kind, load_settings()))
                except Exception:
                    traceback.print_exc()
                    answer.put(None)
            except queue.Empty:
                pass
            if watch and not watch.done:
                watch.check(controller.window_connected)
            with controller.lock:
                busy = bool(controller.run and controller.run["status"] == "running")
            if not busy and time.monotonic() - controller.last_seen > IDLE_SECONDS:
                break
    finally:
        server.shutdown()
        (data_directory() / "instance.json").unlink(missing_ok=True)
