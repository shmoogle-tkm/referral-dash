# PyInstaller build: py -m PyInstaller ReferralDash.spec  (or run build.ps1)
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

package = "referral_dash"
datas = [(f"{package}/{folder}", folder) for folder in ("web", "fonts", "images", "sample-data")]
datas += collect_data_files("selenium")

a = Analysis(
    [f"{package}/__main__.py"],
    datas=datas,
    # Selenium loads its browser drivers lazily, so PyInstaller cannot see them by itself.
    hiddenimports=["keyring.backends.Windows", "win32ctypes.core", *collect_submodules("selenium.webdriver")],
    # Google libraries may linger in an older build environment; the app no longer uses them.
    excludes=["customtkinter", "matplotlib", "numpy", "pandas", "google", "googleapiclient", "google_auth_oauthlib",
              "google_auth_httplib2", "httplib2"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="Referral Dash",
    icon=f"{package}/web/app-icon.ico",
    console=False,
    upx=False,
)
