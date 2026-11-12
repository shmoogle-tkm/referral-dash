"""Waiting for the person to sign in to Tableau themselves, and opening the Dashboard view.

Referral Dash never types or stores a Church username or password: the browser opens at the
workbook, the person signs in as they would by hand, and the run carries on from there.
"""
from __future__ import annotations

import time
from urllib.parse import urlsplit

from selenium.common.exceptions import StaleElementReferenceException, WebDriverException
from selenium.webdriver.common.by import By

from .wait import dismiss_post_login_dialog, switch_to_frame_containing

SIGN_IN_MINUTES = 10
# A saved browser session can open the workbook without a sign-in; give it a moment before asking.
ASK_AFTER_SECONDS = 4
# Signed in but left on Tableau's home page: go to the workbook, at most this often.
REDIRECT_EVERY_SECONDS = 15


def _signed_in_view(driver):
    """True once the workbook view itself is open (it draws inside an iframe)."""
    try:
        return "/views/" in str(driver.current_url or "") and bool(driver.find_elements(By.TAG_NAME, "iframe"))             and not _sign_in_field_showing(driver)
    except StaleElementReferenceException:
        return False


def _sign_in_field_showing(driver):
    for field_id in ("email", "site-uri", "siteURI", "username-input", "password-input"):
        for field in driver.find_elements(By.ID, field_id):
            if field.is_displayed():
                return True
    return False


def _tableau_landing_url(value: str) -> bool:
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    return host.endswith(".online.tableau.com") and host != "sso.online.tableau.com" and not any(
        part in parsed.path.lower() for part in ("login", "siteuri", "/auth/")
    )


def _access_failure_message(driver) -> str | None:
    """The one sign-in result a person can't fix by trying again: no access to Tableau."""
    try:
        body = " ".join(driver.find_element(By.TAG_NAME, "body").text.split()).lower()
    except Exception:
        return None
    if "not assigned to this application" in body or "user is not assigned" in body:
        return ("This Church account is not assigned to Tableau Online. "
                "Ask the mission office to give it Tableau access, then run Referral Dash again.")
    return None


def wait_for_sign_in(driver, endpoint: str, on_waiting=lambda: None, timeout: int = SIGN_IN_MINUTES * 60,
                     clock=time.monotonic, sleep=time.sleep) -> None:
    """Return once the workbook is open, after the person signs in in the browser window.

    Nothing is typed for them. `on_waiting()` is called once if they need to sign in.
    """
    driver.implicitly_wait(0)
    driver.switch_to.default_content()
    started = clock()
    asked = False
    last_redirect = started
    while clock() - started < timeout:
        try:
            if _signed_in_view(driver):
                return
            url = str(driver.current_url or "")
            if ("/views/" not in url and _tableau_landing_url(url) and not _sign_in_field_showing(driver)
                    and clock() - last_redirect >= REDIRECT_EVERY_SECONDS):
                last_redirect = clock()
                driver.get(endpoint)
                continue
            message = _access_failure_message(driver)
        except WebDriverException as error:
            raise RuntimeError("The browser window was closed before sign-in finished. Click Run to try again.") from error
        if message:
            raise RuntimeError(message)
        if not asked and clock() - started >= ASK_AFTER_SECONDS:
            asked = True
            on_waiting()
        sleep(1)
    raise RuntimeError(f"Sign-in wasn't finished within {SIGN_IN_MINUTES} minutes. Click Run to try again.")


def open_dashboard_view(driver, mission: str, zone, *, timeout: int = 60) -> None:
    """Select the account's Dashboard view, recreating it for this mission when missing."""
    from .dashboard import ensure_dashboard_view
    dismiss_post_login_dialog(driver)
    switch_to_frame_containing(driver, By.ID, "manage-customviews", timeout=timeout)
    ensure_dashboard_view(driver, mission, zone, timeout=timeout)
    switch_to_frame_containing(driver, By.CSS_SELECTOR, 'textarea[aria-label*="Start Date"]', timeout=timeout)
