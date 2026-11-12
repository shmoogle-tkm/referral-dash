"""Open Tableau for the person to sign in, then download the mission's Dashboard crosstab.

Adapted from the Referral Care Data Hub's referraldashhelper and browser client.
"""
from __future__ import annotations

import csv
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from ..cleanup import prune_browser_profile
from ..group import TZ_LOOKUP
from ..settings import data_directory
from ..settings import is_configured
from .dashboard import choose_mission, dashboard_entry_url, read_filter_choices
from .session import open_dashboard_view, wait_for_sign_in
from .wait import dismiss_post_login_dialog, switch_to_frame_containing
from .wait import set_date_filter


SIGN_IN_MESSAGE = ("Sign in to Tableau in the browser window that opened. "
                   "Referral Dash carries on by itself once you're in.")


def _options(kind, download_dir):
    options = webdriver.ChromeOptions() if kind == "chrome" else webdriver.EdgeOptions()
    # A saved profile keeps the Church/Tableau sign-in between runs, so an approved
    # ("remember this device") authenticator is not asked for every time.
    options.add_argument(f"--user-data-dir={data_directory() / 'browser' / kind}")
    # Background component downloads only fill the profile; Tableau needs none of them.
    options.add_argument("--disable-component-update")
    options.add_experimental_option("prefs", {
        "download.default_directory": str(download_dir),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
    })
    return options


def create_browser(download_dir):
    """Chrome when installed, otherwise Edge. Selenium downloads the matching driver."""
    errors = []
    for kind, factory in (("chrome", webdriver.Chrome), ("edge", webdriver.Edge)):
        try:
            return factory(options=_options(kind, download_dir))
        except WebDriverException as error:
            errors.append(f"{kind}: {error.msg or error}".splitlines()[0])
    raise RuntimeError("Could not open Chrome or Microsoft Edge for the Tableau download. " + "; ".join(errors))


def _save_evidence(driver):
    """Keep a picture of the last browser page so a failed sign-in can be diagnosed."""
    target = data_directory() / "tableau-failure.png"
    try:
        if driver is not None:
            driver.save_screenshot(str(target))
            return
    except WebDriverException:
        pass
    target.unlink(missing_ok=True)


def _wait_for_download(folder, before, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = set(folder.iterdir())
        finished = [f for f in current - before if f.suffix not in (".crdownload", ".tmp")]
        if finished and not any(f.suffix == ".crdownload" for f in current):
            return finished[0]
        time.sleep(1)
    raise TimeoutError("Tableau did not finish downloading the CSV.")


def download_dashboard(settings, destination, period, progress=lambda message: None,
                       attention=lambda message: None, found_mission=lambda name: None):
    """Download the Dashboard crosstab covering `period` and its comparison range; return the file path.

    The browser is always visible: the person signs in to Tableau there themselves.
    `attention(message)` is called when they need to sign in.
    `found_mission(name)` gets the mission read from Tableau when settings have none yet.
    """
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    attempt = 0
    while True:
        work = destination / f".download-{uuid.uuid4().hex[:8]}"
        work.mkdir()
        driver = None
        try:
            progress("Opening Tableau...")
            driver = create_browser(work.resolve())

            def waiting():
                # Bring the window in front of Referral Dash so the person sees the sign-in page.
                try:
                    driver.minimize_window()
                    driver.maximize_window()
                except WebDriverException:
                    pass
                attention(SIGN_IN_MESSAGE)
            return _download(driver, settings, work, destination, period, progress, waiting, found_mission)
        except Exception as error:
            _save_evidence(driver)
            if attempt or not isinstance(error, TimeoutException):
                raise
            attempt += 1
            progress("Tableau was slow to respond; trying once more...")
        finally:
            if driver is not None:
                driver.quit()
            shutil.rmtree(work, ignore_errors=True)
            for kind in ("chrome", "edge"):
                prune_browser_profile(data_directory() / "browser" / kind)


def _download(driver, settings, work, destination, period, progress, waiting=lambda: None,
              found_mission=lambda name: None):
    zone = TZ_LOOKUP[settings["timezones"]["default"]]
    entry = dashboard_entry_url(settings["tableau_view_url"])
    wait = WebDriverWait(driver, 60)
    driver.get(entry)
    progress("Waiting for Tableau sign-in...")
    wait_for_sign_in(driver, entry, waiting)
    mission = settings["mission"]
    if not is_configured(settings):
        # Each account sees only its own mission in Tableau, so there is nothing to type in setup.
        progress("Finding your mission in Tableau...")
        dismiss_post_login_dialog(driver)
        switch_to_frame_containing(driver, By.CSS_SELECTOR, 'textarea[aria-label*="Start Date"]', timeout=60)
        mission = choose_mission(read_filter_choices(driver, "Mission"))
        found_mission(mission)
    progress("Opening the Dashboard view...")
    open_dashboard_view(driver, mission, zone)

    first, last = period.download_start, period.end
    progress(f"Filtering referrals from {first:%b %d} to {last:%b %d}...")
    set_date_filter(driver, wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'textarea[aria-label*="Start Date"]'))),
                    f"{first.month}/{first.day}/{first.year}")
    set_date_filter(driver, wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'textarea[aria-label="End Date"]'))),
                    f"{last.month}/{last.day}/{last.year}")

    progress("Downloading the referral list...")
    driver.execute_script("arguments[0].click();", wait.until(EC.element_to_be_clickable(
        (By.CSS_SELECTOR, '[data-tb-test-id="viz-viewer-toolbar-button-download"]'))))
    driver.execute_script("arguments[0].click();", wait.until(EC.element_to_be_clickable(
        (By.XPATH, '//span[text()="Crosstab"]/ancestor::*[@role="menuitem"]'))))
    time.sleep(3)  # The crosstab dialog animates in; earlier clicks land on the old layout.
    driver.execute_script("arguments[0].click();", wait.until(EC.element_to_be_clickable(
        (By.CSS_SELECTOR, '[data-tb-test-id="crosstab-options-dialog-radio-csv-Label"]'))))
    before = set(work.iterdir())
    driver.execute_script("arguments[0].click();", wait.until(EC.element_to_be_clickable(
        (By.XPATH, '//*[@id="export-crosstab-options-dialog-Dialog-BodyWrapper-Dialog-Body-Id"]/div/div[3]/button'))))
    downloaded = _wait_for_download(work, before)
    validate_date_range(downloaded, first, last)
    final = destination / f"tableau_export_{datetime.now():%Y%m%d_%H%M%S}.csv"
    for _ in range(40):
        try:
            downloaded.replace(final)
            return final
        except PermissionError:
            time.sleep(0.25)
    raise TimeoutError("The downloaded CSV stayed locked by the browser.")


def validate_date_range(path, first, last):
    """Reject exports whose date filters Tableau did not apply."""
    with open(path, newline="", encoding="utf-16") as infile:
        reader = csv.DictReader(infile, delimiter="\t")
        if "Event Date Selected" not in (reader.fieldnames or []):
            raise ValueError("The Tableau download is not the Dashboard detail crosstab (Event Date Selected is missing).")
        dates = [datetime.strptime(row["Event Date Selected"].strip(), "%m/%d/%Y").date()
                 for row in reader if row.get("Event Date Selected", "").strip()]
    if dates and min(dates) < first:
        raise ValueError(f"Tableau's date filter was not applied: earliest row is {min(dates)}, expected {first} or later.")
    if dates and max(dates) > last:
        raise ValueError(f"Tableau's date filter was not applied: latest row is {max(dates)}, expected {last} or earlier.")
