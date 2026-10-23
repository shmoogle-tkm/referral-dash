"""Start Referral Dash, or render a file without a window: --self-test EXPORT OUTPUT_FOLDER."""
import io
import json
import sys
from pathlib import Path

# Windowed executables have no console streams; libraries still write to them.
if sys.stderr is None:
    sys.stderr = io.StringIO()
if sys.stdout is None:
    sys.stdout = io.StringIO()


def self_test(filename, output):
    from referral_dash.engine import generate_reports
    from referral_dash.settings import validate_settings
    try:
        folder, facts = generate_reports(filename, output, validate_settings({"mission": "Sample Mission"}))
        result = {"status": "passed", "folder": str(folder), "facts": facts}
    except Exception as exc:
        result = {"status": "failed", "error": str(exc)}
    (Path(output) / "self-test-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if result["status"] == "passed" else 1


def main():
    if "--self-test" in sys.argv:
        index = sys.argv.index("--self-test")
        return self_test(*sys.argv[index + 1:index + 3])
    from referral_dash.app import main as run_app
    run_app()
    return 0


if __name__ == "__main__":
    sys.exit(main())
