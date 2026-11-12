"""The Google Drive connection, kept in Windows Credential Manager for this Windows user.

Referral Dash no longer keeps a Church account: people sign in to Tableau themselves each run.
Copies from before 2.6 saved one under LOGIN_RECORD; `forget_login` removes it.
"""
from __future__ import annotations

import keyring

SERVICE_NAME = "Referral Dash"
LOGIN_RECORD = "church-login.v1"
DRIVE_RECORD = "google-drive.v1"


def load_drive_token() -> str:
    try:
        return keyring.get_password(SERVICE_NAME, DRIVE_RECORD) or ""
    except keyring.errors.KeyringError as error:
        raise RuntimeError("Windows could not read the Google Drive connection. Reopen the app and try again.") from error


def save_drive_token(token: str) -> None:
    try:
        keyring.set_password(SERVICE_NAME, DRIVE_RECORD, token)
    except keyring.errors.KeyringError as error:
        raise RuntimeError("Windows could not save the Google Drive connection.") from error


def forget_drive_token() -> None:
    try:
        keyring.delete_password(SERVICE_NAME, DRIVE_RECORD)
    except keyring.errors.PasswordDeleteError:
        pass


def forget_login() -> None:
    """Remove a Church account saved by an older copy."""
    try:
        keyring.delete_password(SERVICE_NAME, LOGIN_RECORD)
    except keyring.errors.KeyringError:
        pass
