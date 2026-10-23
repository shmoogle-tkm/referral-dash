# Referral Dash

A Windows app any mission can use to turn its Tableau referral data into
dashboard pictures and District Summaries. It is the stand-alone sibling of the
Referral Care Data Hub, and shares that app's Tableau automation, look, and
district summary design.

One click:

1. **Signs in to Tableau** with the mission's Church account (stored in Windows
   Credential Manager), opens the account's `Dashboard` view, or recreates it
   with the mission's filters if it is missing, sets the date range, and
   downloads the detail crosstab.
2. **Renders** area, district, zone and mission dashboards.
3. **Renders District Summaries** in the Data Hub style, comparing every area
   with the newest earlier run at least five days old (otherwise the newest
   earlier run). Comparisons come from saved area aggregates in
   `%LOCALAPPDATA%\Referral Dash\history`, scoped to the mission, time zones
   and day window.
4. **Uploads** the run folder to a chosen Google Drive folder (optional).

The UI is a local web page served on `127.0.0.1` behind a per-launch session
token and shown in an Edge or Chrome app window, like the Data Hub control
center. The design lives in Figma:
[Referral Dash 2.0 — App redesign](https://www.figma.com/design/XcxUkYTyxkMAquqK1NGnMN).

## Layout

| Path | Purpose |
| --- | --- |
| `referral_dash/app.py` | Local server, run worker, window launch |
| `referral_dash/web/` | UI (HTML, CSS, JS) |
| `referral_dash/tableau/` | Sign-in, Dashboard view recovery, download (adapted from the Data Hub) |
| `referral_dash/engine.py` | Export validation and complete, isolated report runs |
| `referral_dash/district_summary.py` | District Summary renderer |
| `referral_dash/history.py` | Automatic comparison baselines |
| `referral_dash/drive.py` | Google Drive sign-in and upload |
| `referral_dash/credentials.py` | Church account in Windows Credential Manager |

## Develop

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m referral_dash
```

Set `REFERRAL_DASH_HOME` to use a scratch settings/history folder, and
`REFERRAL_DASH_NO_WINDOW=1` to print the URL instead of opening a window.

## Release

```powershell
.\build.ps1
```

This runs the tests, builds `Referral Dash.exe` with PyInstaller, has the
packaged exe render the sample (`--self-test`), and zips
`dist\Referral Dash <version>`.

Google Drive uploads need a Google Cloud OAuth client of type **Desktop app**
saved as `google_oauth_client.json` in the repository root before building.
It is copied next to the exe and is never committed. While the OAuth consent
screen is in *Testing*, only accounts listed as test users can connect;
publish the consent screen (Google verification is required for the Drive
scope) before giving the app to other missions.
