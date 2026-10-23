"""The Church account for Tableau, kept in Windows Credential Manager for this Windows user.

Adapted from the Referral Care Data Hub; nothing is written to settings.json.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import keyring

SERVICE_NAME = "Referral Dash"
LOGIN_RECORD = "church-login.v1"


@dataclass(frozen=True)
class LoginCredentials:
    email: str = ""
    username: str = ""
    password: str = field(default="", repr=False)

    @property
    def complete(self) -> bool:
        return all((self.email.strip(), self.username.strip(), self.password))


def load_login() -> LoginCredentials:
    try:
        value = keyring.get_password(SERVICE_NAME, LOGIN_RECORD)
    except keyring.errors.KeyringError as error:
        raise RuntimeError("Windows could not read the saved Church account. Reopen the app and try again.") from error
    if not value:
        return LoginCredentials()
    try:
        record = json.loads(value)
        return LoginCredentials(record["email"], record["username"], record["password"])
    except (TypeError, ValueError, KeyError) as error:
        raise ValueError("The saved Church account could not be read. Save it again in Settings.") from error


def save_login(email: str, username: str, password: str) -> LoginCredentials:
    """Save the account; a blank password keeps the saved one for the same username."""
    if any(c in email + username for c in ("\r", "\n", "\x00")) or "\x00" in password:
        raise ValueError("The Church account contains an invalid character.")
    saved = load_login()
    if not password and saved.username == username.strip():
        password = saved.password
    credentials = LoginCredentials(email.strip(), username.strip(), password)
    if not credentials.complete:
        raise ValueError("Email, username, and password are all required.")
    try:
        keyring.set_password(SERVICE_NAME, LOGIN_RECORD, json.dumps(
            {"email": credentials.email, "username": credentials.username, "password": credentials.password}))
    except keyring.errors.KeyringError as error:
        raise RuntimeError("Windows could not save the Church account. Your previous account was kept.") from error
    return credentials


def forget_login() -> None:
    try:
        keyring.delete_password(SERVICE_NAME, LOGIN_RECORD)
    except keyring.errors.PasswordDeleteError:
        pass
