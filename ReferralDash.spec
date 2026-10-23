# PyInstaller build: py -m PyInstaller ReferralDash.spec  (or run build.ps1)
from PyInstaller.utils.hooks import collect_data_files

package = "referral_dash"
datas = [(f"{package}/{folder}", folder) for folder in ("web", "fonts", "images", "sample-data")]
datas += collect_data_files("selenium") + collect_data_files("googleapiclient")

a = Analysis(
    [f"{package}/__main__.py"],
    datas=datas,
    hiddenimports=["keyring.backends.Windows", "win32ctypes.core"],
    excludes=["customtkinter", "matplotlib", "numpy", "pandas"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="Referral Dash",
    icon=f"{package}/web/app-icon.ico",
    console=False,
    upx=False,
)
