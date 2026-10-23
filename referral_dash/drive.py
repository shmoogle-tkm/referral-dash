"""Upload finished reports to a Google Drive folder the user chooses."""
from __future__ import annotations

import mimetypes
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import copy
from pathlib import Path

from .settings import app_directory, data_directory, install_directory

SCOPES = ["https://www.googleapis.com/auth/drive"]
CLIENT_FILE = "google_oauth_client.json"
FOLDER_TYPE = "application/vnd.google-apps.folder"
RETRIES = 5
# A stalled connection gives up and retries instead of freezing the run.
TIMEOUT_SECONDS = 120
# Like the Data Hub's publisher: several uploads at once hide per-request delays.
UPLOAD_WORKERS = 4


def _client_file():
    for folder in (install_directory(), app_directory()):
        if (folder / CLIENT_FILE).exists():
            return folder / CLIENT_FILE
    raise RuntimeError(f"Google sign-in is not set up in this copy of Referral Dash ({CLIENT_FILE} is missing).")


def _token_file():
    return data_directory() / "google-token.json"


def _credentials():
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    if not _token_file().exists():
        return None
    creds = Credentials.from_authorized_user_file(str(_token_file()), SCOPES)
    if not creds.valid and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            return None
        _token_file().write_text(creds.to_json(), encoding="utf-8")
    return creds if creds.valid else None


def _service(creds=None):
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build
    creds = creds or _credentials()
    if creds is None:
        raise RuntimeError("Google Drive is not connected. Open Settings and click Connect Google Drive.")
    http = AuthorizedHttp(creds, http=httplib2.Http(timeout=TIMEOUT_SECONDS))
    return build("drive", "v3", http=http, cache_discovery=False)


def connected_account():
    """The signed-in Google account's email, or None when Drive is not connected."""
    if _credentials() is None:
        return None
    about = _service().about().get(fields="user(emailAddress)").execute(num_retries=RETRIES)
    return about["user"]["emailAddress"]


def connect():
    """Open Google's sign-in page in the browser and keep the resulting token."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(_client_file()), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True, timeout_seconds=300,
                                  success_message="Google Drive is connected. You can close this tab and return to Referral Dash.")
    _token_file().write_text(creds.to_json(), encoding="utf-8")
    return connected_account()


def disconnect():
    _token_file().unlink(missing_ok=True)


def folder_name(folder_id):
    """Confirm the folder exists and this account can see it; return its name."""
    from googleapiclient.errors import HttpError
    try:
        item = _service().files().get(fileId=folder_id, fields="name,mimeType,capabilities(canAddChildren)",
                                      supportsAllDrives=True).execute(num_retries=RETRIES)
    except HttpError as error:
        if error.resp.status == 404:
            raise ValueError("That Drive folder was not found, or this Google account cannot open it.") from error
        raise
    if item["mimeType"] != FOLDER_TYPE:
        raise ValueError("That link is a file, not a folder. Open the folder in Drive and copy its link.")
    if not item.get("capabilities", {}).get("canAddChildren"):
        raise ValueError("This Google account can view that folder but cannot add files to it.")
    return item["name"]


def folder_link(folder_id):
    return f"https://drive.google.com/drive/folders/{folder_id}"


def upload_run(run_folder, parent_id, progress=lambda message, done=None, total=None: None):
    """Copy a finished run folder, with its subfolders, into the Drive folder. Returns the new folder's link."""
    from googleapiclient.http import MediaFileUpload
    creds = _credentials()
    service = _service(creds)
    run_folder = Path(run_folder)

    def make_folder(name, parent):
        return service.files().create(body={"name": name, "mimeType": FOLDER_TYPE, "parents": [parent]},
                                      fields="id", supportsAllDrives=True).execute(num_retries=RETRIES)["id"]

    progress("Creating the Drive folder...")
    folders = {run_folder: make_folder(run_folder.name, parent_id)}
    for folder in sorted(p for p in run_folder.rglob("*") if p.is_dir()):
        folders[folder] = make_folder(folder.name, folders[folder.parent])
    files = sorted(p for p in run_folder.rglob("*") if p.is_file())

    # httplib2 connections are not thread-safe, so each worker keeps its own client.
    local = threading.local()

    def upload(path):
        if not hasattr(local, "service"):
            local.service = _service(copy(creds))
        media = MediaFileUpload(str(path), mimetype=mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        local.service.files().create(body={"name": path.name, "parents": [folders[path.parent]]}, media_body=media,
                                     fields="id", supportsAllDrives=True).execute(num_retries=RETRIES)

    with ThreadPoolExecutor(max_workers=UPLOAD_WORKERS) as pool:
        pending = [pool.submit(upload, path) for path in files]
        try:
            for done, future in enumerate(as_completed(pending), 1):
                future.result()
                progress(f"Uploading to Google Drive ({done} of {len(files)})...", done, len(files))
        except BaseException:
            for future in pending:
                future.cancel()
            raise
    return folder_link(folders[run_folder])
