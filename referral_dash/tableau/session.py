"""Account-independent login and view selection for the Tableau collectors."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from .wait import dismiss_post_login_dialog, switch_to_frame_containing


class TableauSignInTimeout(RuntimeError):
    """A transient sign-in transition, safe to retry in a fresh browser."""


def _sign_in_timeout(driver, phase):
    parsed = urlsplit(str(driver.current_url or ""))
    # Never include query strings, page text, cookies, or credentials in logs.
    location = f"{parsed.hostname or 'Tableau'}{parsed.path}"
    return TableauSignInTimeout(
        f"Tableau sign-in timed out {phase} at {location}. "
        "Nothing was downloaded or uploaded. Check the account's Tableau access if retrying also fails."
    )


def tableau_site_uri(endpoint: str | None) -> str:
    match = re.search(r"/t/([^/]+)", endpoint or "")
    return match.group(1) if match else ""


def _login_state(driver):
    for field_id in ("email", "site-uri", "siteURI", "username-input", "password-input"):
        for field in driver.find_elements(By.ID, field_id):
            if field.is_displayed() and field.is_enabled():
                return field_id, field
    if "/siteURI" in driver.current_url:
        for field in driver.find_elements(By.CSS_SELECTOR, 'input[type="email"], input[type="text"]'):
            if field.is_displayed() and field.is_enabled():
                return "siteURI", field
    if "/views/" in driver.current_url and driver.find_elements(By.TAG_NAME, "iframe"):
        return "ready", None
    return False


def _tableau_landing_url(value: str) -> bool:
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    return host.endswith(".online.tableau.com") and host != "sso.online.tableau.com" and not any(
        part in parsed.path.lower() for part in ("login", "siteuri", "/auth/")
    )


def _access_failure_message(driver) -> str | None:
    """Turn IdP access failures into an actionable setup message."""
    try:
        url = str(driver.current_url or "").lower()
        body = " ".join(driver.find_element(By.TAG_NAME, "body").text.split()).lower()
    except Exception:
        return None
    if "not assigned to this application" in body or "user is not assigned" in body:
        return (
            "This Church account is not assigned to the Tableau Online application. "
            "Ask the administrator to assign the account to Tableau Online, then run Referral Dash again."
        )
    if "access denied" in body or "unauthorized" in body or "not authorized" in body:
        return "This Church account is not authorized for the Tableau dashboard. Ask the administrator to grant Tableau access, then retry."
    if "id.churchofjesuschrist.org" in url and body:
        # Keep the generic login failure for ordinary invalid credentials, but
        # include the identity-provider page when it exposes a useful reason.
        if any(term in body for term in ("invalid", "incorrect", "failed", "locked")):
            return "The Church account was rejected by sign-in. Check the saved username and password, then retry."
    return None


def login_tableau(
    driver, endpoint: str, *, email: str | None, username: str | None,
    password: str | None, timeout: int = 60,
) -> None:
    """Accept email, site, username, password, and already signed-in entry points.

    Credentials are read by callers for each run; no account identity or session
    is cached by this module. Only visible login fields receive credentials.
    """
    driver.implicitly_wait(0)
    driver.switch_to.default_content()
    # The view URL is the entry point for a fresh account. Tableau must receive
    # the email first so it can route the account to the Church sign-in page.
    # Going straight to TABLEAU_SSO_ENDPOINT skips that routing step and can
    # incorrectly show the identity provider's application-assignment page.
    wait = WebDriverWait(driver, timeout)
    def entry_state(browser):
        current = _login_state(browser)
        if current:
            return current
        if "/views/" not in browser.current_url and _tableau_landing_url(browser.current_url):
            browser.get(endpoint)
            return _login_state(browser)
        return False
    try:
        state = wait.until(entry_state)
    except TimeoutException as error:
        access_message = _access_failure_message(driver)
        if access_message:
            raise RuntimeError(access_message) from error
        raise _sign_in_timeout(driver, "waiting for the sign-in page") from error
    submitted_email = False
    for _ in range(8):
        field_id, field = state
        if field_id == "ready":
            return
        if field_id in {"site-uri", "siteURI"} or (field_id == "email" and "/siteURI" in driver.current_url):
            value = tableau_site_uri(endpoint)
            if not value:
                raise RuntimeError("Tableau site name is missing from the configured view URL.")
        elif field_id == "email":
            value = email
            if submitted_email:
                raise RuntimeError("Tableau returned to the email page; check this account's Tableau access.")
            submitted_email = True
        elif field_id == "username-input":
            value = username
        else:
            value = password
        if not value:
            raise RuntimeError(f"Tableau login requires the saved account's {field_id} value.")
        field.clear()
        field.send_keys(value)
        field.submit()

        def next_state(browser):
            # After the Church password, Tableau may land on its home page instead of the
            # requested view; any signed-in Tableau page means the hand-off finished.
            if field_id == "password-input":
                if _tableau_landing_url(browser.current_url) and not browser.find_elements(By.ID, "password-input"):
                    return "sso-complete", None
            current = _login_state(browser)
            if current and (current[0] != field_id or current[1] != field):
                return current
            if field_id in {"site-uri", "siteURI"}:
                if "Unable to verify site URI" in browser.find_element(By.TAG_NAME, "body").text:
                    return "site-error", None
            return False

        try:
            state = wait.until(next_state)
        except TimeoutException as error:
            access_message = _access_failure_message(driver)
            if access_message:
                raise RuntimeError(access_message) from error
            raise _sign_in_timeout(driver, f"after {field_id}") from error
        if state[0] == "sso-complete":
            driver.get(endpoint)
            try:
                state = wait.until(entry_state)
            except TimeoutException as error:
                access_message = _access_failure_message(driver)
                if access_message:
                    raise RuntimeError(access_message) from error
                raise _sign_in_timeout(driver, "returning from Church sign-in") from error
        elif state[0] == "site-error":
            raise RuntimeError(
                "Tableau could not route the saved email to the configured Church site. "
                "In Settings, use the exact email that you enter on Tableau's email page, then retry. "
                "The Church username is only used on the Church sign-in page."
            )
    raise RuntimeError("Tableau login did not reach the requested view.")


def open_dashboard_view(driver, mission: str, zone, *, timeout: int = 60) -> None:
    """Select the account's Dashboard view, recreating it for this mission when missing."""
    from .dashboard import ensure_dashboard_view
    dismiss_post_login_dialog(driver)
    switch_to_frame_containing(driver, By.ID, "manage-customviews", timeout=timeout)
    ensure_dashboard_view(driver, mission, zone, timeout=timeout)
    switch_to_frame_containing(driver, By.CSS_SELECTOR, 'textarea[aria-label*="Start Date"]', timeout=timeout)
