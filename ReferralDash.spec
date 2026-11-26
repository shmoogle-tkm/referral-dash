# PyInstaller build: build.ps1 on Windows (one Referral Dash.exe), build.sh on a Mac (Referral Dash.app).
import os
import re
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

package = "referral_dash"
mac = sys.platform == "darwin"
version = re.search(r'VERSION = "([^"]+)"', open(f"{package}/settings.py", encoding="utf-8").read()).group(1)

datas = [(f"{package}/{folder}", folder) for folder in ("web", "fonts", "images", "sample-data", "data")]
datas += collect_data_files("selenium")
# The trend video encodes with the ffmpeg binary that imageio-ffmpeg ships.
datas += collect_data_files("imageio_ffmpeg")
# Google treats a Desktop app's client file as not secret; without one, the Drive option stays hidden.
if os.path.isfile("drive_oauth_client.json"):
    datas.append(("drive_oauth_client.json", "."))

# Windows keeps the Drive connection in Credential Manager, a Mac in the Keychain.
keyring_backend = ["keyring.backends.macOS"] if mac else ["keyring.backends.Windows", "win32ctypes.core"]

a = Analysis(
    [f"{package}/__main__.py"],
    datas=datas,
    # Selenium loads its browser drivers lazily, so PyInstaller cannot see them by itself.
    hiddenimports=[*keyring_backend, *collect_submodules("selenium.webdriver")],
    # Google libraries may linger in an older build environment; the app no longer uses them.
    excludes=["customtkinter", "matplotlib", "numpy", "pandas", "google", "googleapiclient", "google_auth_oauthlib",
              "google_auth_httplib2", "httplib2"],
)
pyz = PYZ(a.pure)

if mac:
    # A Mac app is a folder (Referral Dash.app), so the files are collected beside the program.
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Referral Dash", console=False, upx=False)
    collected = COLLECT(exe, a.binaries, a.datas, name="Referral Dash", upx=False)
    app = BUNDLE(
        collected,
        name="Referral Dash.app",
        icon=f"{package}/web/app-icon.png",  # PyInstaller turns it into .icns (needs Pillow).
        bundle_identifier="io.github.referral-dash",
        version=version,
        info_plist={
            "CFBundleShortVersionString": version,
            "CFBundleVersion": version,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
else:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name="Referral Dash",
        icon=f"{package}/web/app-icon.ico",
        console=False,
        upx=False,
    )
