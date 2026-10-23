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

from . import credentials, drive
from .engine import generate_reports
from .history import History
from .settings import (TIMEZONES, VERSION, app_directory, data_directory, is_configured, load_settings,
                       save_settings, validate_settings)

HOST = "127.0.0.1"
WEB_ROOT = app_directory() / "web"
SAMPLE = app_directory() / "sample-data" / "sample-referrals.csv"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
IDLE_SECONDS = 150
KEEP_DOWNLOADS = 10
STEPS = [("download", "Get referrals"), ("render", "Make dashboards"),
         ("summaries", "District summaries"), ("upload", "Upload to Drive")]


def default_output_folder():
    return Path.home() / "Documents" / "Referral Dash reports"


class Controller:
    """All state the window shows; every mutation happens under one lock."""

    def __init__(self):
        self.lock = threading.RLock()
        self.run = None
        self.drive_state = {"connecting": False, "account": None, "error": ""}
        self.ui_tasks = queue.Queue()
        self.last_seen = time.monotonic()
        threading.Thread(target=self._refresh_drive_account, daemon=True).start()

    # ----- state -----
    def state(self):
        settings = load_settings()
        login = credentials.load_login()
        output = settings["output_folder"] or str(default_output_folder())
        with self.lock:
            run = json.loads(json.dumps(self.run)) if self.run else None
            drive_state = dict(self.drive_state)
        return {
            "version": VERSION, "timezones": TIMEZONES, "settings": settings,
            "configured": is_configured(settings),
            "output_folder": output,
            "account": {"email": login.email, "username": login.username, "saved": login.complete},
            "drive": {**drive_state, "folder_id": settings["drive_folder"], "folder_name": settings["drive_folder_name"],
                      "folder_link": drive.folder_link(settings["drive_folder"]) if settings["drive_folder"] else ""},
            "run": run, "recent": self.recent(output),
        }

    def recent(self, output):
        runs = []
        for folder in sorted(Path(output).glob("Dashboard_*"), reverse=True)[:8]:
            try:
                facts = json.loads((folder / "run-summary.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            runs.append({"folder": str(folder), "name": folder.name, "generated_utc": facts.get("generated_utc"),
                         "referrals": facts.get("referrals"), "images": facts.get("images"),
                         "district_summaries": facts.get("district_summaries"),
                         "compared_with": facts.get("comparison_generated_utc"),
                         "drive_link": facts.get("drive_link"), "mission": facts.get("settings", {}).get("mission")})
        return runs

    def _refresh_drive_account(self):
        try:
            account = drive.connected_account()
            error = ""
        except Exception as exc:  # A Drive outage must not stop the window from loading.
            account, error = None, str(exc)
        with self.lock:
            self.drive_state.update(account=account, error=error)

    # ----- settings -----
    def save_settings(self, body):
        current = load_settings()
        allowed = {"mission", "lightning", "pepper", "start_time", "cutoff_time", "timezones",
                   "tableau_view_url", "lookback_days", "show_browser"}
        return save_settings({**current, **{k: v for k, v in body.items() if k in allowed}})

    def save_account(self, body):
        credentials.save_login(str(body.get("email", "")), str(body.get("username", "")), str(body.get("password", "")))

    def connect_drive(self):
        with self.lock:
            if self.drive_state["connecting"]:
                return
            self.drive_state.update(connecting=True, error="")

        def work():
            try:
                account, error = drive.connect(), ""
            except Exception as exc:
                account, error = None, f"Google sign-in did not finish: {exc}"
            with self.lock:
                self.drive_state.update(connecting=False, account=account, error=error)
        threading.Thread(target=work, daemon=True).start()

    def disconnect_drive(self):
        drive.disconnect()
        with self.lock:
            self.drive_state.update(account=None, error="")

    def set_drive_folder(self, link):
        settings = load_settings()
        folder_id = validate_settings({**settings, "drive_folder": link})["drive_folder"]
        name = drive.folder_name(folder_id) if folder_id else ""
        save_settings({**settings, "drive_folder": folder_id, "drive_folder_name": name})

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
    def start_run(self, source):
        settings = load_settings()
        login = credentials.load_login()
        if source != "sample":
            if not is_configured(settings):
                raise ValueError("Open Settings and enter your mission name exactly as Tableau shows it.")
            if source == "tableau" and not login.complete:
                raise ValueError("Open Settings and save the Church account used for Tableau.")
        filename = None
        if source == "file":
            filename = self.ask_ui("file")
            if not filename:
                return None
        with self.lock:
            if self.run and self.run["status"] == "running":
                raise ValueError("A run is already in progress.")
            uploads = bool(settings["drive_folder"]) and source != "sample"
            self.run = {"id": secrets.token_hex(4), "status": "running", "source": source,
                        "started": datetime.now(timezone.utc).isoformat(), "finished": None,
                        "message": "Starting...", "attention": "", "percent": 1, "error": "", "result": None,
                        "steps": [{"id": i, "label": label, "state": "pending", "detail": ""} for i, label in STEPS
                                  if i != "upload" or uploads]}
            if source != "tableau":
                self._step("download", "done", "Sample data" if source == "sample" else Path(filename).name)
        threading.Thread(target=self._work, args=(source, filename, settings, login), daemon=True).start()
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

    def _work(self, source, filename, settings, login):
        current = "download"
        try:
            output = Path(settings["output_folder"] or default_output_folder())
            output.mkdir(parents=True, exist_ok=True)
            if source == "tableau":
                self._step("download", "active")
                from .tableau.download import download_dashboard
                downloads = data_directory() / "downloads"
                filename = download_dashboard(settings, login, downloads, lambda m: self._progress(m), self._attention)
                for old in sorted(downloads.glob("tableau_export_*.csv"))[:-KEEP_DOWNLOADS]:
                    old.unlink(missing_ok=True)
                self._step("download", "done", "Downloaded from Tableau")
                self._progress("Downloaded from Tableau", 35)
            elif source == "sample":
                filename = SAMPLE
                settings = validate_settings({**settings, "mission": "Sample Mission"})
            history = None if source == "sample" else History(settings)
            if history:
                history.import_reports(output)
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
            folder, facts = generate_reports(filename, output, settings, progress, history=history)
            skipped = facts["skipped_rows"]
            self._step("render", "done", f"{facts['images'] - facts['district_summaries']} dashboards" +
                       (f" · {skipped} row{'s' * (skipped != 1)} without an area left out" if skipped else ""))
            compared = facts["comparison_generated_utc"]
            self._step("summaries", "done", f"{facts['district_summaries']} districts" +
                       (f" · vs {datetime.fromisoformat(compared):%b %d}" if compared else " · first run, no comparison"))
            link = None
            if any(s["id"] == "upload" for s in self.run["steps"]):
                current = "upload"
                self._step("upload", "active")
                self._progress("Uploading to Google Drive...", 82)
                link = drive.upload_run(folder, settings["drive_folder"], lambda m: self._progress(m))
                facts["drive_link"] = link
                (folder / "run-summary.json").write_text(json.dumps(facts, indent=2), encoding="utf-8")
                self._step("upload", "done", settings["drive_folder_name"] or "Google Drive")
            with self.lock:
                self.run.update(status="done", percent=100, finished=datetime.now(timezone.utc).isoformat(),
                                message="Finished", result={"folder": str(folder), "images": facts["images"],
                                "dashboards": facts["images"] - facts["district_summaries"],
                                "summaries": facts["district_summaries"], "referrals": facts["referrals"],
                                "compared_with": compared, "drive_link": link})
        except Exception as exc:
            traceback.print_exc()
            self._step(current, "failed", "")
            with self.lock:
                self.run.update(status="failed", finished=datetime.now(timezone.utc).isoformat(),
                                message="Run failed", error=str(exc) or type(exc).__name__)

    def open_target(self, target):
        if target.startswith("https://drive.google.com/"):
            webbrowser.open(target)
            return
        path = Path(target)
        if path.exists():
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
            self.controller.last_seen = time.monotonic()
            if path == "/api/state":
                try:
                    return self._json(self.controller.state())
                except Exception as exc:
                    return self._json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB_ROOT / name).resolve()
        if WEB_ROOT.resolve() not in target.parents or not target.is_file():
            return self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")
        self._send(HTTPStatus.OK, target.read_bytes(), mimetypes.guess_type(target.name)[0] or "application/octet-stream")

    def do_POST(self):
        if not self._authorized():
            return self._json({"error": "Unauthorized"}, HTTPStatus.FORBIDDEN)
        self.controller.last_seen = time.monotonic()
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
            "/api/drive/connect": c.connect_drive,
            "/api/drive/disconnect": c.disconnect_drive,
            "/api/drive/folder": lambda: c.set_drive_folder(str(body.get("link", ""))),
            "/api/output-folder": c.pick_output_folder,
            "/api/run": lambda: c.start_run(str(body.get("source", "tableau"))),
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
        subprocess.Popen([browser, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1280,860",
                          "--no-first-run", "--no-default-browser-check"], creationflags=CREATE_NO_WINDOW)
    else:
        webbrowser.open(url)


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
        open_window(f"http://{HOST}:{existing['port']}/#t={existing['token']}")
        return
    controller = Controller()
    token = secrets.token_urlsafe(32)
    handler = type("BoundHandler", (Handler,), {"controller": controller, "token": token})
    server = ThreadingHTTPServer((HOST, 0), handler)
    port = server.server_address[1]
    (data_directory() / "instance.json").write_text(json.dumps({"port": port, "token": token}), encoding="utf-8")
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True).start()
    url = f"http://{HOST}:{port}/#t={token}"
    if os.getenv("REFERRAL_DASH_NO_WINDOW") == "1":
        print(url, flush=True)  # For automated UI checks.
    else:
        open_window(url)
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
            with controller.lock:
                busy = bool(controller.run and controller.run["status"] == "running") or controller.drive_state["connecting"]
            if not busy and time.monotonic() - controller.last_seen > IDLE_SECONDS:
                break
    finally:
        server.shutdown()
        (data_directory() / "instance.json").unlink(missing_ok=True)
