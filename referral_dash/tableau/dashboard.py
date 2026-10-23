"""Recreate the Dashboard custom view from its independent filter definition.

Adapted from the Referral Care Data Hub; the mission filter comes from settings."""
from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .wait import set_date_filter, view_signature, wait_for_view_update


VIEW_NAME = "Dashboard"
EVENT_BASIS = "First Finding Event Date"
SOURCES = ("Facebook - Mission Ad", "Headquarters Paid Ad", "Headquarters Local")


def dashboard_filters(mission):
    return (
        ("Mission", (mission,)),
        ("Zone", ("(All)",)),
        ("District", ("(All)",)),
        ("Teaching Area", ("(All)",)),
        ("Finding Category", ("Media",)),
        ("Finding Source", SOURCES),
        ("Ad Group", ("(All)",)),
        ("Ad Topic old", ("(All)",)),
    )


def dashboard_entry_url(endpoint: str | None) -> str | None:
    """Open the workbook, so a deleted account-owned view cannot break login."""
    if not endpoint:
        return endpoint
    parsed = urlsplit(endpoint)
    prefix, separator, rest = parsed.path.partition("/views/")
    parts = rest.strip("/").split("/")
    if not separator:
        return endpoint
    if len(parts) < 2 or not all(parts[:2]):
        raise ValueError("Dashboard recovery needs the configured Tableau workbook/view URL.")
    return urlunsplit((parsed.scheme, parsed.netloc, prefix + "/views/" + "/".join(parts[:2]), "", ""))


def _click(driver, element):
    driver.execute_script("arguments[0].click();", element)


def _visible(driver, by, selector):
    return [e for e in driver.find_elements(by, selector) if e.is_displayed()]


def _wait_ready(driver, timeout):
    # Tableau's transparent loading pane still intercepts clicks. Screenshot
    # stability and Selenium visibility alone cannot establish query completion.
    WebDriverWait(driver, timeout).until(lambda d: d.execute_script(
        "return Array.from(document.querySelectorAll('#loadingGlassPane')).every("
        "e => !e.getBoundingClientRect().width || !e.getBoundingClientRect().height "
        "|| getComputedStyle(e).pointerEvents === 'none');"
    ))


def _group(driver, label):
    return driver.find_element(
        By.XPATH, f'//*[@role="group"][.//h3[normalize-space(@title)="{label}"]]',
    )


def _set_parameter(driver, name, value, timeout):
    selector = f'[role="combobox"][aria-label^="{name} "]'
    control = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.CSS_SELECTOR, selector)))
    if control.get_attribute("aria-label") == f"{name} {value}":
        return
    control.click()
    choice = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((
        By.XPATH, f'//*[@role="menu"]//*[normalize-space(text())="{value}"]',
    )))
    before = view_signature(driver)
    choice.click()
    wait_for_view_update(driver, before, timeout=timeout)
    _wait_ready(driver, timeout)
    WebDriverWait(driver, timeout).until(
        lambda d: d.find_element(By.CSS_SELECTOR, selector).get_attribute("aria-label") == f"{name} {value}"
    )


def _set_quick_filter(driver, label, values, timeout):
    group = _group(driver, label)
    opener = group.find_element(By.CSS_SELECTOR, '[aria-haspopup="listbox"]')
    if len(values) == 1 and opener.text.strip() == values[0]:
        return
    menu_id = group.get_attribute("id") + "_menu"
    # Tableau opens these popups on a pointer interaction, not a synthetic click.
    opener.click()
    menu = WebDriverWait(driver, timeout).until(EC.visibility_of_element_located((By.ID, menu_id)))
    def loaded_rows(_):
        try:
            rows = menu.find_elements(By.CSS_SELECTOR, '[role="checkbox"]')
            return rows if rows and all(r.text.strip() for r in rows) and any(r.text.strip() == "(All)" for r in rows) else False
        except StaleElementReferenceException:
            return False
    rows = WebDriverWait(driver, timeout).until(loaded_rows)
    options = {row.text.strip(): row for row in rows}
    if "(All)" not in options:
        raise RuntimeError(f"Cannot recover Dashboard: {label} has no All selection.")
    if label == "Mission" and not set(values) <= options.keys():
        raise RuntimeError(f"Tableau has no mission named {values[0]!r}. In Settings, enter the mission name exactly as Tableau lists it.")
    target = set(values)
    # Tableau's relevant-value domain can omit a source with no current rows.
    if label == "Finding Source":
        target &= options.keys()
    if not target or not target.issubset(options):
        raise RuntimeError(f"Cannot recover Dashboard: expected {label} choices are unavailable.")
    desired = set(options) if target == {"(All)"} else target
    changed = False
    # Click the actual input; clicking its outer row only focuses it.
    for text, row in options.items():
        if text == "(All)":
            continue
        field = row.find_element(By.CSS_SELECTOR, 'input[type="checkbox"]')
        if field.is_selected() != (text in desired):
            _click(driver, field)
            changed = True
    if changed:
        before = view_signature(driver)
        menu.find_element(By.XPATH, './/button[normalize-space(.)="Apply"]').click()
        driver.execute_script("document.activeElement.blur();")
        wait_for_view_update(driver, before, timeout=timeout)
        _wait_ready(driver, timeout)
    # A query replaces checkbox nodes. Reacquire before closing the popup.
    menu = driver.find_element(By.ID, menu_id)
    actual = {r.text.strip() for r in menu.find_elements(By.CSS_SELECTOR, '[role="checkbox"]')
              if r.text.strip() != "(All)" and r.find_element(By.CSS_SELECTOR, 'input[type="checkbox"]').is_selected()}
    expected = desired - {"(All)"}
    if actual != expected:
        raise RuntimeError(f"Cannot recover Dashboard: {label} did not retain the requested choices.")
    # Apply commits the choices and disables Cancel, leaving the popup open.
    # Click its actual backdrop; clicking an underlying control is intercepted.
    backdrop = _visible(driver, By.CSS_SELECTOR, ".tab-glass.clear-glass")
    if not backdrop:
        raise RuntimeError(f"Cannot close the recovered {label} filter menu.")
    backdrop[-1].click()
    WebDriverWait(driver, timeout).until(EC.invisibility_of_element_located((By.ID, menu_id)))


def apply_dashboard_definition(driver, mission, zone, timeout=90, *, days=30):
    """Only called when the account has no saved Dashboard view."""
    _wait_ready(driver, timeout)
    today = datetime.now(zone).date()
    for label, value in (("Start Date", today - timedelta(days=days)), ("End Date", today)):
        field = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((
            By.CSS_SELECTOR, f'textarea[aria-label^="{label}"]',
        )))
        set_date_filter(driver, field, f"{value.month}/{value.day}/{value.year}")
        _wait_ready(driver, timeout)
    _set_parameter(driver, "Event Type Date", EVENT_BASIS, timeout)
    _set_parameter(driver, "Sort By Field", EVENT_BASIS, timeout)
    for label, values in dashboard_filters(mission):
        _set_quick_filter(driver, label, values, timeout)
    flc = _group(driver, "FLC Y/N")
    all_radio = flc.find_element(By.XPATH, './/*[@role="radio"][normalize-space(.)="(All)"]//input')
    if not all_radio.is_selected():
        before = view_signature(driver)
        _click(driver, all_radio)
        wait_for_view_update(driver, before, timeout=timeout)
    for label in ("Vendor Ad Id", "Person Id"):
        group = _group(driver, label)
        if any(e.text.strip() for e in group.find_elements(By.CSS_SELECTOR, '.FIItem')):
            raise RuntimeError(f"Cannot recover Dashboard: Original contains a {label} restriction.")
        for field in group.find_elements(By.CSS_SELECTOR, 'input[type="text"],textarea'):
            if field.get_attribute("value"):
                raise RuntimeError(f"Cannot recover Dashboard: Original contains a {label} search.")



def ensure_dashboard_view(driver, mission, zone, timeout=90, *, view_name=VIEW_NAME, apply_definition=None):
    """Select the existing view, or rebuild and privately save the missing view."""
    _click(driver, driver.find_element(By.ID, "manage-customviews"))
    close_selector = '[data-tb-test-id="manageCustomViews-Dialog-CloseButton"]'
    close = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.CSS_SELECTOR, close_selector)))
    views = _visible(driver, By.CSS_SELECTOR, f'[itemtype="SetCurrentView"][aria-label="{view_name}"]')
    if len(views) > 1:
        raise RuntimeError(f"More than one Tableau view is named {view_name}; the correct view cannot be selected safely.")
    if views:
        before = view_signature(driver)
        _click(driver, views[0])
        wait_for_view_update(driver, before, timeout=timeout)
        _wait_ready(driver, timeout)
        return False
    _click(driver, close)
    WebDriverWait(driver, timeout).until(EC.invisibility_of_element_located((By.CSS_SELECTOR, close_selector)))
    (apply_definition or apply_dashboard_definition)(driver, mission, zone, timeout=timeout)
    _click(driver, WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.ID, 'save-as-custom-view'))))
    name = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.CSS_SELECTOR, '[data-tb-test-id="name-TextInput"]')))
    name.clear()
    name.send_keys(view_name)
    _click(driver, WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.CSS_SELECTOR, '[data-tb-test-id="save-Button"]'))))
    WebDriverWait(driver, timeout).until(EC.invisibility_of_element_located((By.CSS_SELECTOR, '[data-tb-test-id="name-TextInput"]')))
    WebDriverWait(driver, timeout).until(
        lambda d: " ".join(d.find_element(By.ID, "manage-customviews").text.split()) == f"View: {view_name}"
    )
    return True
