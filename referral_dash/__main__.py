"""Start Referral Dash, or render a file without a window: --self-test EXPORT OUTPUT_FOLDER [START END]."""
import io
import json
import sys
from pathlib import Path

# Windowed executables have no console streams; libraries still write to them.
if sys.stderr is None:
    sys.stderr = io.StringIO()
if sys.stdout is None:
    sys.stdout = io.StringIO()


def check_imports():
    """Load everything a real run needs, so a build missing a module fails before release."""
    from selenium import webdriver
    from selenium.webdriver.chrome.webdriver import WebDriver as Chrome  # noqa: F401
    from selenium.webdriver.edge.webdriver import WebDriver as Edge  # noqa: F401
    import keyring.backends.Windows  # noqa: F401
    import referral_dash.tableau.download  # noqa: F401
    import referral_dash.app  # noqa: F401
    import referral_dash.drive  # noqa: F401
    webdriver.ChromeOptions(), webdriver.EdgeOptions()


def self_test(filename, output, start=None, end=None):
    from referral_dash.engine import generate_reports
    from referral_dash.period import make_period
    from referral_dash.settings import validate_settings
    try:
        check_imports()
        settings = validate_settings({"mission": "Sample Mission"})
        period = make_period(settings, start, end) if start else None
        folder, facts = generate_reports(filename, output, settings, period=period)
        result = {"status": "passed", "folder": str(folder), "facts": facts}
    except Exception as exc:
        result = {"status": "failed", "error": str(exc)}
    (Path(output) / "self-test-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if result["status"] == "passed" else 1


def main():
    if "--self-test" in sys.argv:
        index = sys.argv.index("--self-test")
        return self_test(*sys.argv[index + 1:index + 5])
    from referral_dash.app import main as run_app
    run_app()
    return 0


if __name__ == "__main__":
    sys.exit(main())
