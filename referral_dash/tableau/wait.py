from __future__ import annotations

from hashlib import sha256
import time

from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


def view_signature(driver) -> bytes:
    """Return a compact signature of the currently visible Tableau view."""
    return sha256(driver.get_screenshot_as_png()).digest()


def wait_for_view_update(
    driver,
    previous_signature: bytes,
    *,
    timeout: float = 90,
    stable_checks: int = 3,
    poll_seconds: float = 1,
) -> None:
    """Wait until Tableau visibly changes and then remains stable.

    Tableau leaves toolbar controls clickable while a filter query is still
    recalculating. Waiting only for clickability can therefore export the old,
    unfiltered crosstab. A completed visual update is a stronger signal and is
    independent of Tableau's frequently changing internal spinner classes.
    """
    deadline = time.monotonic() + timeout
    changed = False
    last_signature = previous_signature
    consecutive_stable = 0

    while time.monotonic() < deadline:
        try:
            current_signature = view_signature(driver)
        except StaleElementReferenceException:
            time.sleep(poll_seconds)
            continue

        if current_signature != previous_signature:
            changed = True

        if changed and current_signature == last_signature:
            consecutive_stable += 1
            if consecutive_stable >= stable_checks:
                return
        else:
            consecutive_stable = 0

        last_signature = current_signature
        time.sleep(poll_seconds)

    raise TimeoutError("Tableau did not finish applying the requested filter.")


def switch_to_frame_containing(driver, by, value, *, timeout: float = 60) -> None:
    def switch(_driver):
        _driver.switch_to.default_content()
        for iframe in _driver.find_elements(By.TAG_NAME, "iframe"):
            try:
                _driver.switch_to.default_content()
                _driver.switch_to.frame(iframe)
                if _driver.find_elements(by, value):
                    return True
            except Exception:
                continue
        _driver.switch_to.default_content()
        return False

    WebDriverWait(driver, timeout).until(switch)


def dismiss_post_login_dialog(driver) -> bool:
    driver.switch_to.default_content()
    dialogs = driver.find_elements(
        By.CSS_SELECTOR,
        '[data-tb-test-id="tabcld-postlogin-id-Dialog-Body"]',
    )
    if not any(dialog.is_displayed() for dialog in dialogs):
        return False

    buttons = [
        button
        for button in driver.find_elements(
            By.CSS_SELECTOR,
            '[data-tb-test-id^="tabcld-postlogin"] button',
        )
        if button.is_displayed() and button.is_enabled()
    ]
    if not buttons:
        raise RuntimeError("Tableau post-login message is blocking the view and has no dismiss button.")
    driver.execute_script("arguments[0].click();", buttons[-1])
    WebDriverWait(driver, 15).until(
        EC.invisibility_of_element_located(
            (By.CSS_SELECTOR, '[data-tb-test-id="tabcld-postlogin-id-Dialog-Glass"]')
        )
    )
    return True


def set_date_filter(driver, element, value: str) -> None:
    normalized_value = value.lstrip("0").replace("/0", "/")
    current_value = element.get_attribute("value").lstrip("0").replace("/0", "/")
    if current_value == normalized_value:
        return

    before_filter = view_signature(driver)
    ActionChains(driver).move_to_element(element).click().key_down(Keys.CONTROL).send_keys(
        "a"
    ).key_up(Keys.CONTROL).send_keys(Keys.BACKSPACE).send_keys(value).send_keys(Keys.ENTER).perform()
    # A focused date field keeps blinking its caret after the query finishes.
    # The visual stability check would treat every blink as a pending update.
    # Blur this field directly: Tab can focus another date field with a caret.
    driver.execute_script("arguments[0].blur();", element)
    WebDriverWait(driver, 15).until(
        lambda _driver: element.get_attribute("value").lstrip("0").replace("/0", "/")
        == normalized_value
    )
    wait_for_view_update(driver, before_filter)
