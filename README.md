# Referral Dash

A Windows and Mac app any mission can use to turn its Tableau referral data into
dashboard pictures and District Summaries. It is the stand-alone sibling of the
Referral Care Data Hub, and shares that app's Tableau automation, look, and
district summary design.

One click:

1. **Opens Tableau** in a visible browser for the person to sign in themselves
   (no account is typed or stored), then opens the account's `Dashboard` view, or recreates it
   with the mission's filters if it is missing, sets the date range, and
   downloads the detail crosstab.
2. **Renders** area, district, zone and mission dashboards.
3. **Renders District Summaries** in the Data Hub style, comparing every area
   with an earlier date range: the same dates 7 days before by default, or as
   set under Settings > Advanced. For August 8 - September 8, the app downloads August 1 -
   September 8 once and builds August 1 - September 1 from it, so a mission's
   very first run already has change numbers. Each range is measured as it
   stood at the end of its range (`period.py`, `engine.rewind`), and leaves off
   as many days as Tableau's data trails, so both sides have the same follow-up
   time. Dashboards count only the report range.
4. **Cleans up** after itself: only the newest report sets are kept (8 by
   default, changeable on the main screen), and browser caches in
   `%LOCALAPPDATA%\Referral Dash` are pruned. Comparisons come from each run's
   own download, so deleting old report sets loses nothing.

Settings > Advanced sets what reports cover (the last N days, or a date range)
and what they compare with (N days before, or a date range). The main screen
shows the result in one line; fixed dates stay until they are changed there.

5. **Uploads to Google Drive** (optional). Connect Google Drive opens Google's
   sign-in and folder picker in one browser trip; the folder can be in a shared
   drive. Its button says Insert. Each run uploads its pictures
   there as its five folders, replacing the previous copy, which goes to the Drive trash. The app asks only
   for `drive.file`, so it can see only that folder and what it made there.

## Videos

The **Report | Video** switch at the top of the main screen chooses what the run makes. Report makes the dashboards and district summaries as before. Video makes a trend video: a line graph of the fastest areas' median response time, one day at a time. Each day is the 7 days ending on it, the graph shows 7 days at once, and only the top 10 areas are drawn.

A video covers referrals from `video_days` days ago up to today (42 by default). Change it under Settings > Advanced > Video. Videos are saved to `Referral Dash videos` in the reports folder, and Past videos lists them. Videos are not uploaded to Google Drive. **Try sample data** works in both modes.

## Google Drive setup

The option appears only in builds that include `drive_oauth_client.json`, which
is never committed. To avoid Google's unverified-app warning:

1. Create a new Google Cloud project. Enable the Google Drive API and the Google
   Picker API.
2. Under Google Auth platform, set the audience to External, add only the
   `.../auth/drive.file` scope under Data Access, and click **Publish app**. In
   Testing mode Google shows a warning and sign-ins expire after 7 days.
3. Create a client of type **Desktop app**, download its JSON, and save it as
   `drive_oauth_client.json` in the repository root before running `build.ps1`.

Mission Google accounts managed by an organization can still be blocked by their
administrator.

The UI is a local web page served on `127.0.0.1` behind a per-launch session
token and shown in an Edge or Chrome app window, like the Data Hub control
center. Since 2.1 it is a single dark screen styled after the report pictures
(Cal Sans and Nunito, served from `referral_dash/fonts`), with a two-step
first-time setup; rarely needed options sit under Settings > Advanced.

## Layout

| Path | Purpose |
| --- | --- |
| `referral_dash/app.py` | Local server, run worker, window launch |
| `referral_dash/web/` | UI (HTML, CSS, JS) |
| `referral_dash/tableau/` | Sign-in, Dashboard view recovery, download (adapted from the Data Hub) |
| `referral_dash/engine.py` | Export validation and complete, isolated report runs |
| `referral_dash/district_summary.py` | District Summary renderer |
| `referral_dash/period.py` | The report's date range and the range it compares with |
| `referral_dash/cleanup.py` | Keeps the newest report sets; prunes browser caches |
| `referral_dash/drive.py` | Google sign-in, folder pick, upload, moving old sets to the trash |
| `referral_dash/credentials.py` | Google Drive connection in Windows Credential Manager |

## Develop

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m referral_dash
```

Set `REFERRAL_DASH_HOME` to use a scratch settings folder, and
`REFERRAL_DASH_NO_WINDOW=1` to print the URL instead of opening a window.

## Release

```powershell
.\build.ps1
```

This runs the tests, builds `Referral Dash.exe` with PyInstaller, has the
packaged exe render the sample (`--self-test`), and zips
`dist\Referral Dash <version>`.

The Mac versions are built by GitHub on its Macs: pushing a version tag
(`git tag v2.7.0` then `git push origin v2.7.0`) runs `.github/workflows/mac.yml`,
which builds `Referral-Dash-<version>-Mac-Apple-Silicon.zip` and
`Referral-Dash-<version>-Mac-Intel.zip` with `build.sh` (tests, the packaged
self-test, then the ZIP) and attaches both to a draft release for that tag.
Add the Windows ZIP to the same draft, then publish it. To include Google
Drive in the Mac builds, save the contents of `drive_oauth_client.json` as the
repository secret `DRIVE_OAUTH_CLIENT`. On a Mac, `bash build.sh` builds the
same ZIP locally.

Windows-only and Mac-only code is limited to a few spots, each choosing by
platform: the data folder (`%LOCALAPPDATA%` or `~/Library/Application Support`),
the app window (Edge or Chrome), opening folders, the time zone, the serif font,
and the saved Drive connection (Credential Manager or the Keychain).
