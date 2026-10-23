# Builds dist\Referral-Dash-<version>-Windows.zip from a clean checkout.
# Put google_oauth_client.json (a Google Cloud "Desktop app" OAuth client) in the
# repository root first if the release should offer Google Drive uploads.
# Native tools write progress to stderr, so failures are detected by exit code.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

if (-not (Test-Path .venv)) { py -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --quiet --disable-pip-version-check -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw "Dependency install failed." }
.\.venv\Scripts\python.exe -m unittest discover -s tests
if ($LASTEXITCODE -ne 0) { throw "Tests failed; nothing was built." }

$version = (.\.venv\Scripts\python.exe -c "from referral_dash.settings import VERSION; print(VERSION)").Trim()
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean ReferralDash.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

$release = "dist\Referral Dash $version"
if (Test-Path $release) { Remove-Item -Recurse -Force $release }
New-Item -ItemType Directory $release | Out-Null
Copy-Item "dist\Referral Dash.exe" $release
Copy-Item licenses $release\Licenses -Recurse
Copy-Item "docs\START HERE.txt" $release
if (Test-Path google_oauth_client.json) {
    Copy-Item google_oauth_client.json $release
} else {
    Write-Warning "google_oauth_client.json is missing: this build cannot upload to Google Drive."
}

# The exact packaged executable must render the sample before the ZIP is made.
$check = Join-Path $env:TEMP "referral-dash-selftest"
if (Test-Path $check) { Remove-Item -Recurse -Force $check }
New-Item -ItemType Directory $check | Out-Null
$sample = Join-Path $PSScriptRoot "referral_dash\sample-data\sample-referrals.csv"
Start-Process "$release\Referral Dash.exe" -ArgumentList "--self-test", "`"$sample`"", "`"$check`"" -Wait
$result = Get-Content "$check\self-test-result.json" | ConvertFrom-Json
if ($result.status -ne "passed") { throw "Packaged self-test failed: $($result.error)" }

$zip = "dist\Referral-Dash-$version-Windows.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $release -DestinationPath $zip
Write-Host "Built $zip ($($result.facts.images) sample images rendered by the packaged app)."
