"""Upload finished reports to a Google Drive folder the person picks.

Asks Google only for `drive.file`: the folder picked in Google's Picker and what this
app creates there. That permission is what keeps the unverified-app warning away, and
it also means the app can never see, change or remove anything else in the Drive.
"""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import secrets
import threading
import time
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

from . import credentials
from .settings import app_directory, data_directory, install_directory

CLIENT_FILE = "drive_oauth_client.json"
SCOPE = "https://www.googleapis.com/auth/drive.file"
FOLDER_TYPE = "application/vnd.google-apps.folder"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_URL = "https://www.googleapis.com/drive/v3/files"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_LINK = "https://drive.google.com/drive/folders/"
SIGN_IN_SECONDS = 300
TIMEOUT_SECONDS = 120
RETRIES = 5
RETRY_STATUS = {429, 500, 502, 503, 504}
UPLOAD_WORKERS = 4
PICTURE_SUFFIXES = {".jpg", ".jpeg", ".png"}
DONE_PAGE = (b"<!doctype html><meta charset='utf-8'><title>Referral Dash</title>"
             b"<body style='font:18px system-ui;background:#07111a;color:#f6f8fa;text-align:center;padding:80px'>"
             b"<h1>You're all set</h1><p>You can close this tab and go back to Referral Dash.</p>")


class DriveError(RuntimeError):
    """Something a person can read; the message says what to do next."""


def _client_file():
    for folder in (app_directory(), install_directory()):
        if (folder / CLIENT_FILE).is_file():
            return folder / CLIENT_FILE
    return None


def available():
    """Whether this copy of the app was built with a Google client to connect through."""
    return _client_file() is not None


def _client():
    path = _client_file()
    if path is None:
        raise DriveError("Google Drive isn't set up in this copy of Referral Dash.")
    try:
        client = json.loads(path.read_text(encoding="utf-8"))["installed"]
        return {"id": client["client_id"], "secret": client["client_secret"]}
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise DriveError(f"{CLIENT_FILE} must be a Google OAuth client of type Desktop app.") from error


def _reason(error):
    """Google's own explanation from an error response, if it sent one."""
    try:
        body = json.loads(error.read().decode("utf-8"))
    except (OSError, ValueError):
        return ""
    detail = body.get("error")
    if isinstance(detail, dict):
        return str(detail.get("message", ""))
    return str(body.get("error_description") or detail or "")


def _send(method, url, *, data=None, headers=None):
    """One request with retries for Google's temporary errors. Raises HTTPError for the rest."""
    for attempt in range(RETRIES):
        try:
            request = Request(url, data=data, headers=headers or {}, method=method)
            with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                body = response.read()
            return json.loads(body) if body else {}
        except HTTPError as error:
            error.detail = _reason(error)
            error.close()
            # Google also reports being rate limited as 403.
            temporary = error.code in RETRY_STATUS or (error.code == 403 and "rate limit" in error.detail.lower())
            if not temporary or attempt == RETRIES - 1:
                raise
        except (URLError, TimeoutError, ConnectionError) as error:
            if attempt == RETRIES - 1:
                raise DriveError("Could not reach Google Drive. Check the internet connection and try again.") from error
        time.sleep(min(2 ** attempt, 20))


def _exchange(fields):
    try:
        return _send("POST", TOKEN_URL, data=urlencode(fields).encode(),
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    except HTTPError as error:
        if error.code in (400, 401):
            raise DriveError("Google Drive needs to be connected again. Click Connect Google Drive. " + error.detail) from error
        raise DriveError(f"Google sign-in failed: {error.detail or error.code}") from error


class _Session:
    """Keeps an access token current; safe to share between upload workers."""

    def __init__(self):
        self.client = _client()
        self.refresh_token = credentials.load_drive_token()
        if not self.refresh_token:
            raise DriveError("Google Drive needs to be connected again. Click Connect Google Drive.")
        self.lock = threading.Lock()
        self.access_token = None
        self.expires = 0

    def _token(self, stale=None):
        with self.lock:
            # Another worker may have renewed it already.
            if self.access_token and self.access_token != stale and time.monotonic() < self.expires:
                return self.access_token
            reply = _exchange({"client_id": self.client["id"], "client_secret": self.client["secret"],
                               "refresh_token": self.refresh_token, "grant_type": "refresh_token"})
            self.access_token = reply["access_token"]
            self.expires = time.monotonic() + int(reply.get("expires_in", 3600)) - 120
            return self.access_token

    def call(self, method, url, *, data=None, content_type="application/json"):
        token = self._token()
        for attempt in (1, 2):
            try:
                return _send(method, url, data=data, headers={"Authorization": f"Bearer {token}", "Content-Type": content_type})
            except HTTPError as error:
                if error.code == 401 and attempt == 1:
                    token = self._token(stale=token)
                    continue
                raise DriveError(f"Google Drive refused the request ({error.code}): {error.detail}") from error


# ----- connecting -----
def _wait_for_redirect(server, state, deadline):
    """Serve the loopback address until Google sends the browser back to it."""
    answer = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            parts = urlsplit(self.path)
            query = {k: v[0] for k, v in parse_qs(parts.query).items()}
            if parts.path == "/" and query.get("state") == state:
                answer.update(query)
            self.send_response(200 if answer else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(DONE_PAGE if answer else b"")

    server.RequestHandlerClass = Handler
    while not answer:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DriveError("Google sign-in timed out. Click Connect Google Drive to try again.")
        server.timeout = min(remaining, 1)
        server.handle_request()
    return answer


def connect(open_browser=webbrowser.open):
    """Sign in and pick a folder in one browser trip; returns the folder's id and name."""
    client = _client()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    server = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    try:
        redirect = f"http://127.0.0.1:{server.server_address[1]}/"
        open_browser(AUTH_URL + "?" + urlencode({
            "client_id": client["id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
            "access_type": "offline", "prompt": "consent", "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256",
            # Google's Picker runs as part of sign-in; only folders can be chosen.
            "trigger_onepick": "true", "allow_folder_selection": "true", "mimetypes": FOLDER_TYPE,
        }))
        answer = _wait_for_redirect(server, state, time.monotonic() + SIGN_IN_SECONDS)
    finally:
        server.server_close()
    if "code" not in answer:
        raise DriveError("Google sign-in was cancelled, so Google Drive wasn't connected.")
    if not answer.get("picked_file_ids"):
        raise DriveError("No folder was picked, so Google Drive wasn't connected. Try again and choose a folder.")
    folder_id = answer["picked_file_ids"].split(",")[0]
    tokens = _exchange({"client_id": client["id"], "client_secret": client["secret"], "code": answer["code"],
                        "code_verifier": verifier, "grant_type": "authorization_code", "redirect_uri": redirect})
    if not tokens.get("refresh_token"):
        raise DriveError("Google didn't allow lasting access. Click Connect Google Drive to try again.")
    credentials.save_drive_token(tokens["refresh_token"])
    try:
        name = _folder_name(_Session(), folder_id)
    except DriveError:
        credentials.forget_drive_token()
        raise
    return {"id": folder_id, "name": name}


def _folder_name(session, folder_id):
    item = session.call("GET", f"{API_URL}/{folder_id}?" + urlencode(
        {"fields": "name,mimeType,capabilities/canAddChildren", "supportsAllDrives": "true"}))
    if item.get("mimeType") != FOLDER_TYPE:
        raise DriveError("That isn't a folder. Click Connect Google Drive and choose a folder.")
    if not item.get("capabilities", {}).get("canAddChildren", True):
        raise DriveError(f"This Google account can't add files to \"{item.get('name')}\". Choose a folder it can edit.")
    return item["name"]


def disconnect():
    credentials.forget_drive_token()


# ----- uploading -----
def folder_link(folder_id):
    return FOLDER_LINK + folder_id


def _make_folder(session, name, parent):
    body = json.dumps({"name": name, "mimeType": FOLDER_TYPE, "parents": [parent]}).encode()
    return session.call("POST", f"{API_URL}?fields=id&supportsAllDrives=true", data=body)["id"]


def _upload_file(session, path, parent):
    boundary = uuid.uuid4().hex
    meta = json.dumps({"name": path.name, "parents": [parent]})
    kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
            f"--{boundary}\r\nContent-Type: {kind}\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    session.call("POST", f"{UPLOAD_URL}?uploadType=multipart&fields=id&supportsAllDrives=true", data=body,
                 content_type=f"multipart/related; boundary={boundary}")


def _trash(session, file_id):
    session.call("PATCH", f"{API_URL}/{file_id}?fields=id&supportsAllDrives=true", data=json.dumps({"trashed": True}).encode())


def _uploads_path():
    return data_directory() / "drive-uploads.json"


def _load_uploads():
    """Report sets this app uploaded, oldest first, by Drive folder."""
    try:
        return json.loads(_uploads_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_uploads(uploads):
    target = _uploads_path()
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(uploads, indent=1), encoding="utf-8")
    temporary.replace(target)


def upload_run(run_folder, parent_id, progress=lambda message, done=None, total=None: None):
    """Copy a report set's pictures, with their subfolders, into the Drive folder; return its link."""
    session = _Session()
    run_folder = Path(run_folder)
    files = sorted(p for p in run_folder.rglob("*") if p.suffix.lower() in PICTURE_SUFFIXES and p.is_file())
    progress("Creating the Google Drive folder...")
    root = _make_folder(session, run_folder.name, parent_id)
    try:
        folders = {run_folder: root}
        for path in files:
            for folder in reversed([path.parent, *path.parent.parents]):
                if folder not in folders and run_folder in folder.parents:
                    folders[folder] = _make_folder(session, folder.name, folders[folder.parent])
        with ThreadPoolExecutor(max_workers=UPLOAD_WORKERS) as pool:
            pending = [pool.submit(_upload_file, session, path, folders[path.parent]) for path in files]
            try:
                for done, future in enumerate(as_completed(pending), 1):
                    future.result()
                    progress(f"Uploading to Google Drive ({done} of {len(files)})...", done, len(files))
            except BaseException:
                for future in pending:
                    future.cancel()
                raise
    except BaseException:
        # A half-uploaded set is worse than none; put it in the Drive trash.
        try:
            _trash(session, root)
        except DriveError:
            pass
        raise
    uploads = _load_uploads()
    uploads.setdefault(parent_id, []).append({"id": root, "name": run_folder.name})
    _save_uploads(uploads)
    return folder_link(root)


def prune_uploads(parent_id, keep):
    """Move this app's older uploads to the Drive trash, keeping the newest `keep` (0 keeps all).

    Only sets recorded here are touched; nothing else in the folder is even visible to the app.
    Returns how many were moved. One that won't move stays listed and is tried again next run.
    """
    if keep <= 0:
        return 0
    uploads = _load_uploads()
    sets = uploads.get(parent_id, [])
    old, newest = sets[:-keep], sets[-keep:]
    if not old:
        return 0
    session = _Session()
    still_there = []
    moved = 0
    for item in old:
        try:
            _trash(session, item["id"])
            moved += 1
        except DriveError as error:
            # Already deleted by hand means there is nothing left to do.
            if "(404)" not in str(error):
                still_there.append(item)
    uploads[parent_id] = still_there + newest
    _save_uploads(uploads)
    return moved
