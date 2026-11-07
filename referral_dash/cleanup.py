"""Keep Referral Dash small on disk: old report sets, old downloads, and browser caches.

Only folders and files this app made are removed. Each run builds its week-earlier
comparison from its own download, so deleting old report sets loses nothing.
"""
import shutil
import time
from pathlib import Path

from .settings import data_directory

RUN_PREFIX = "Dashboard_"
STAGING_PREFIX = ".building_"
STALE_STAGING_SECONDS = 24 * 3600
# Caches and downloaded browser components; sign-in cookies stay so the
# authenticator's "remember this device" keeps working.
BROWSER_CACHES = (
    "component_crx_cache", "extensions_crx_cache", "optimization_guide_model_store", "ProvenanceData",
    "Edge Entity Extraction", "WidevineCdm", "GrShaderCache", "GraphiteDawnCache", "ShaderCache",
    "Default/Cache", "Default/Code Cache", "Default/GPUCache", "Default/DawnGraphiteCache",
    "Default/DawnWebGPUCache", "Default/Service Worker/CacheStorage", "Default/Service Worker/ScriptCache",
)
RETIRED_FILES = ("google-token.json",)


def report_runs(output):
    """Finished report sets in `output`, newest first."""
    folder = Path(output)
    if not folder.is_dir():
        return []
    runs = [p for p in folder.glob(RUN_PREFIX + "*") if p.is_dir() and (p / "run-summary.json").is_file()]
    # Names are Dashboard_<date>_<time>_<random>; within one second, the finish time decides.
    return sorted(runs, key=lambda p: (p.name.rsplit("_", 1)[0], (p / "run-summary.json").stat().st_mtime), reverse=True)


def prune_reports(output, keep):
    """Delete all but the newest `keep` report sets (0 keeps everything); return what was removed."""
    removed = []
    if keep > 0:
        for old in report_runs(output)[keep:]:
            shutil.rmtree(old, ignore_errors=True)
            removed.append(old)
    folder = Path(output)
    if folder.is_dir():
        # A run that crashed mid-render leaves a hidden staging folder behind.
        for stale in folder.glob(STAGING_PREFIX + "*"):
            if stale.is_dir() and time.time() - stale.stat().st_mtime > STALE_STAGING_SECONDS:
                shutil.rmtree(stale, ignore_errors=True)
    return removed


def prune_browser_profile(profile):
    """Remove cache folders from a browser profile that is not in use."""
    profile = Path(profile)
    for relative in BROWSER_CACHES:
        target = profile / relative
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)


def prune_app_data():
    """Startup tidy-up of the app's own data folder."""
    home = data_directory()
    for name in RETIRED_FILES:
        (home / name).unlink(missing_ok=True)
    prune_browser_profile(home / "window")
    for kind in ("chrome", "edge"):
        prune_browser_profile(home / "browser" / kind)
