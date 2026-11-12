"use strict";

const token = (() => {
  const match = location.hash.match(/t=([^&]+)/);
  if (match) {
    sessionStorage.setItem("referral-dash-token", match[1]);
    history.replaceState(null, "", location.pathname);
  }
  return sessionStorage.getItem("referral-dash-token") || "";
})();

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const DAY = 24 * 3600 * 1000;
// US zones by their everyday names; the rest by city, grouped by region.
const US_ZONES = [["America/New_York", "Eastern (US)"], ["America/Chicago", "Central (US)"], ["America/Denver", "Mountain (US)"],
                  ["America/Phoenix", "Arizona (US)"], ["America/Los_Angeles", "Pacific (US)"], ["America/Anchorage", "Alaska (US)"],
                  ["Pacific/Honolulu", "Hawaii (US)"]];

function zoneLabel(zone) {
  const us = US_ZONES.find(([value]) => value === zone);
  return us ? us[1] : zone.split("/").slice(1).join(" / ").replaceAll("_", " ") || zone;
}

function zoneOptions() {
  const option = (zone, label = zoneLabel(zone)) => `<option value="${escapeHtml(zone)}">${escapeHtml(label)}</option>`;
  const regions = {};
  for (const zone of state.timezones) {
    const region = zone.includes("/") ? zone.split("/")[0] : "Other";
    (regions[region] ||= []).push(zone);
  }
  return `<option value="auto">Automatic · ${escapeHtml(zoneLabel(state.suggested_timezone))} (this computer)</option>` +
    `<optgroup label="United States">${US_ZONES.map(([zone, label]) => option(zone, label)).join("")}</optgroup>` +
    Object.entries(regions).map(([region, zones]) =>
      `<optgroup label="${escapeHtml(region)}">${zones.sort((a, b) => zoneLabel(a).localeCompare(zoneLabel(b))).map((z) => option(z)).join("")}</optgroup>`).join("");
}
const STEP_ICONS = { pending: "", active: "", done: "✓", failed: "!" };
const RUN_ICONS = {
  running: "M12 2a10 10 0 1 0 10 10h-3a7 7 0 1 1-7-7V2Z",
  done: "M9.5 16.2 5.3 12l-1.4 1.4 5.6 5.6L20.1 8.4 18.7 7 9.5 16.2Z",
  failed: "M11 7h2v7h-2V7Zm0 9h2v2h-2v-2Zm1-14a10 10 0 1 1 0 20 10 10 0 0 1 0-20Zm0 2a8 8 0 1 0 0 16 8 8 0 0 0 0-16Z",
};
let state = null;
let formsFilled = false;
let view = null;
let wasReady = null;

async function api(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "X-Session": token, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

const calm = matchMedia("(prefers-reduced-motion: reduce)");

function toast(message, isError = false) {
  const element = document.createElement("div");
  element.className = "toast" + (isError ? " error" : "");
  element.textContent = message;
  $("#toast-region").append(element);
  setTimeout(() => {
    element.classList.add("is-leaving");
    setTimeout(() => element.remove(), calm.matches ? 0 : 220);
  }, isError ? 9000 : 4000);
}

async function act(path, body, success) {
  try {
    const payload = await api(path, body);
    if (success) toast(success);
    await refresh();
    return payload;
  } catch (error) {
    toast(error.message, true);
    return null;
  }
}

function showView(name) {
  const changed = view !== name;
  view = name;
  $("#home-view").hidden = name !== "home";
  $("#settings-view").hidden = name !== "settings";
  $("#settings-button").hidden = name === "settings";
  window.scrollTo(0, 0);
  if (!changed) return;
  const shown = $(name === "home" ? "#home-view" : "#settings-view");
  shown.classList.remove("is-entering");
  void shown.offsetWidth;  // Restart the entrance.
  shown.classList.add("is-entering");
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function shortDate(iso) {
  return iso ? new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "";
}

function dateTime(iso) {
  return iso ? new Date(iso).toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "";
}

function shifted(day, days) {
  const value = new Date(`${day}T00:00:00`);
  value.setDate(value.getDate() - days);
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}

function longDate(day) {
  return new Date(`${day}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

// Shows, while the form is being filled in, what these date choices would mean.
function renderDateChoices() {
  const form = $("#advanced-form");
  for (const pair of $$(".date-pair")) pair.hidden = form[pair.dataset.mode].value !== "range";
  // The mission's date, the same one the main screen uses.
  const today = state?.today || new Date().toLocaleDateString("en-CA");
  const fixedReport = form.report_mode.value === "range";
  const start = fixedReport ? form.report_start.value : shifted(today, Number(form.lookback_days.value) || 0);
  const end = fixedReport ? form.report_end.value : today;
  const fixedCompare = form.compare_mode.value === "range";
  const days = Number(form.compare_days.value) || 0;
  // District summaries cover the report's last few days, against the matching days before.
  const recent = Math.max(1, Number(form.summary_days.value) || 7);
  const summaryStart = start && end && (shifted(end, recent - 1) > start ? shifted(end, recent - 1) : start);
  const first = fixedCompare ? form.compare_start.value : summaryStart && shifted(summaryStart, days);
  const last = fixedCompare ? form.compare_end.value : end && shifted(end, days);
  $("#dates-preview").textContent = start && end && first && last
    ? `Dashboards ${longDate(start)} – ${longDate(end)}. District summaries ${longDate(summaryStart)} – ${longDate(end)}, compared with ${longDate(first)} – ${longDate(last)}.`
    : "Choose both dates to use this range.";
}

function daysAgo(iso) {
  const days = Math.floor((Date.now() - new Date(iso)) / DAY);
  return days <= 0 ? "today" : days === 1 ? "yesterday" : `${days} days ago`;
}

function capital(text) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

// Numbers count up once when a run finishes.
function countUp(element) {
  const target = Number(element.dataset.count);
  if (calm.matches || !Number.isFinite(target)) {
    element.textContent = element.dataset.count;
    return;
  }
  const start = performance.now();
  const step = (now) => {
    const t = Math.min(1, (now - start) / 700);
    element.textContent = Math.round(target * (1 - Math.pow(1 - t, 3))).toLocaleString();
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function folderName(path) {
  return path.split(/[\\/]/).filter(Boolean).pop() || path;
}

function overrides() {
  return Object.fromEntries(Object.entries(state.settings.timezones).filter(([zone]) => zone !== "default"));
}

function fillForms() {
  const s = state.settings;
  const mission = $("#mission-form");
  mission.mission.value = state.configured ? s.mission : "";
  // "Automatic" follows this computer's time zone; picking a zone keeps that one.
  $("#timezone-select").innerHTML = zoneOptions();
  // Until the mission is saved, guess the time zone from this computer.
  mission.timezone.value = s.timezone_auto ? "auto" : s.timezones.default;
  const account = $("#account-form");
  account.email.value = state.account.email;
  account.username.value = state.account.username;
  const advanced = $("#advanced-form");
  for (const key of ["lookback_days", "start_time", "cutoff_time", "lightning", "pepper", "tableau_view_url", "report_mode",
                     "report_start", "report_end", "compare_mode", "compare_days", "compare_start", "compare_end", "summary_days"]) advanced[key].value = s[key];
  renderDateChoices();
  advanced.overrides.value = Object.entries(overrides()).map(([zone, tz]) => `${zone} = ${tz}`).join("\n");
  advanced.show_browser.checked = s.show_browser;
  $("#keep-input").value = s.keep_reports;
  formsFilled = true;
  loadMissions();
}

// Type-ahead for the mission name, from the list Tableau's Mission filter shows.
async function loadMissions() {
  try {
    const { missions } = await api("/api/missions");
    $("#mission-options").innerHTML = missions.map((name) => `<option value="${escapeHtml(name)}"></option>`).join("");
  } catch {
    // Without the list, the field still takes any name.
  }
}

function renderSettings(ready) {
  const setup = !ready;
  document.body.classList.toggle("is-setup", setup);
  $("#welcome").hidden = !setup;
  $("#settings-head").hidden = setup;
  $("#setup-alt").hidden = !setup;
  $("#advanced").hidden = setup;
  // Setup only needs the Church account: the mission comes from Tableau, the time zone from this PC.
  $("#mission-form").hidden = setup;
  $("#account-step").hidden = setup;
  $("#mission-help").textContent = state.configured ? "Read from Tableau. Change it only if it's wrong." : "Found in Tableau on your first run.";
  $("#account-step").textContent = state.account.saved ? "✓" : "1";
  $("#account-step").classList.toggle("is-done", state.account.saved);
  $("#mission-step").textContent = state.configured ? "✓" : "2";
  $("#mission-step").classList.toggle("is-done", state.configured);
  $("#account-note").textContent = state.account.saved ? "Saved" : "";
  $("#account-password").placeholder = state.account.saved ? "Saved" : "";
  $("#mission-note").textContent = state.configured ? "Saved" : "";
}

function renderRunCard(ready, running) {
  const fixedDates = state.settings.report_mode === "range";
  const last = state.recent.find((run) => run.mission === state.settings.mission);
  const due = !last || Date.now() - new Date(last.generated_utc) >= 6 * DAY;
  $("#run-eyebrow").textContent = !ready ? "Almost there" : last ? `Last run ${shortDate(last.generated_utc)} · ${daysAgo(last.generated_utc)}` : "First run";
  $("#run-title").textContent = !ready ? "Finish setup to make reports" : fixedDates ? "Make reports for these dates" : due ? "Make this week's reports" : "This week's reports are done";
  $("#run-lede").textContent = !ready
    ? "Add your Church account in Settings. It takes a minute."
    : "Signs in to Tableau, downloads your referrals, and makes every dashboard and district summary.";
  const button = $("#run-button");
  button.disabled = running;
  const fresh = due || fixedDates;
  button.textContent = running ? "Running…" : !ready ? "Finish setup" : fresh ? "Run now" : "Run again";
  button.classList.toggle("is-quiet", ready && !fresh && !running);
  const plan = $("#plan-line");
  plan.hidden = !ready;
  plan.className = "plan-line" + (state.plan.error ? " is-error" : "");
  plan.textContent = state.plan.error ? `Check the dates in Settings > Advanced: ${state.plan.error}`
    : `${state.plan.period} · District summaries ${state.plan.summary} vs ${state.plan.compared}`;
  $$(".run-alternatives [data-run]").forEach((b) => (b.disabled = running));

  $("#output-name").textContent = folderName(state.output_folder);
  $("#output-path").textContent = state.output_folder;
  $("#output-path").title = state.output_folder;
  const keep = state.settings.keep_reports;
  $("#keep-help").textContent = keep ? "report sets. Older ones are deleted after each run to save space. 0 keeps them all." : "report sets. 0 keeps them all, so nothing is deleted.";
}

function renderDrive(running) {
  const drive = state.drive;
  $("#drive-row").hidden = !drive.available;
  $("#drive-name").textContent = drive.connecting ? "Finish in your browser…" : drive.connected ? drive.folder_name : "Not connected";
  const note = $("#drive-note");
  note.textContent = drive.error || (drive.connecting ? "Sign in, pick a folder, then click Insert." : drive.connected ? "Each run uploads here." : "Pick a folder in your Drive or a shared drive.");
  note.className = drive.error ? "is-error" : "";
  $("#drive-connect").textContent = drive.connected ? "Change folder" : "Connect Google Drive";
  $("#drive-connect").hidden = drive.connecting;
  $("#drive-connect").disabled = running;
  $("#drive-cancel").hidden = !drive.connecting;
  $("#drive-disconnect").hidden = !drive.connected;
  $("#drive-disconnect").disabled = running;
  $("#drive-open").hidden = !drive.connected;
}

function elapsed(run) {
  const end = run.finished ? new Date(run.finished) : new Date();
  const seconds = Math.max(0, Math.round((end - new Date(run.started)) / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function renderSteps(steps) {
  const list = $("#work-steps");
  const rows = steps.map((step) => ({
    state: step.state,
    html: `<span>${STEP_ICONS[step.state]}</span>
      <div><strong>${escapeHtml(step.label)}</strong><small>${escapeHtml(step.detail || { pending: "Waiting", active: "In progress", done: "Done", failed: "Failed" }[step.state])}</small></div>`,
  }));
  if (list.children.length !== rows.length) list.innerHTML = rows.map(() => "<li></li>").join("");
  // Only a step that changed is redrawn, so spinners keep turning and each check pops once.
  rows.forEach((row, index) => {
    const item = list.children[index];
    if (item.dataset.html === row.html) return;
    item.dataset.html = row.html;
    item.className = `is-${row.state}`;
    item.innerHTML = row.html;
  });
}

function renderWork(run) {
  const wrap = $("#work-wrap");
  if (!run) {
    wrap.hidden = true;
    wrap.classList.remove("is-open");
    return;
  }
  if (wrap.hidden) {
    // Unhide first, then open on a later frame so the card slides the page down instead of jumping.
    wrap.hidden = false;
    requestAnimationFrame(() => requestAnimationFrame(() => wrap.classList.add("is-open")));
  }
  const panel = $("#work");
  const waiting = run.status === "running" && Boolean(run.attention);
  panel.className = `card work is-${run.status}` + (waiting ? " is-attention" : "");
  const icon = $("#work .work-icon path");
  if (icon.getAttribute("d") !== RUN_ICONS[run.status]) icon.setAttribute("d", RUN_ICONS[run.status]);
  $("#work-title").textContent = waiting ? "Waiting for you" : { running: "Making reports", done: "Reports are ready", failed: "The run stopped" }[run.status];
  $("#work-message").textContent = run.status === "failed" ? run.error : waiting ? run.attention : run.status === "done" ? `Finished in ${elapsed(run)}` : run.message;
  $("#work-time").textContent = elapsed(run);
  $("#work-bar").style.width = `${run.status === "running" ? Math.max(run.percent, 2) : 100}%`;
  renderSteps(run.steps);

  const key = `${run.id}:${run.status}`;
  const result = $("#work-result");
  result.hidden = run.status !== "done";
  if (run.status === "done" && result.dataset.key !== key) {
    result.dataset.key = key;
    const r = run.result;
    result.innerHTML = `
      <div class="stat"><strong data-count="${Number(r.referrals) || 0}">0</strong><small>Referrals</small></div>
      <div class="stat"><strong data-count="${Number(r.dashboards) || 0}">0</strong><small>Dashboards</small></div>
      <div class="stat"><strong data-count="${Number(r.summaries) || 0}">0</strong><small>District summaries</small></div>
      <div class="stat"><strong class="small">${r.compared_with ? escapeHtml(r.compared_with) : "—"}</strong><small>${r.compared_with ? "Compared with" : "Comparison dates not in this file"}</small></div>` +
      (r.removed ? `<p class="result-note">Removed ${r.removed} older report set${r.removed === 1 ? "" : "s"} to save space.</p>` : "") +
      (r.drive_error ? `<p class="result-note is-error">Couldn't upload to Google Drive: ${escapeHtml(r.drive_error)} Your reports are still on this computer.</p>` : "");
    result.querySelectorAll("[data-count]").forEach(countUp);
  }
  const actions = $("#work-actions");
  actions.hidden = run.status === "running";
  if (actions.dataset.key === key) return;
  actions.dataset.key = key;
  if (run.status === "done") {
    actions.innerHTML = `<button class="button button-primary" data-open="${escapeHtml(run.result.folder)}">Open reports</button>` +
      (run.result.drive_link ? `<button class="button" data-open="${escapeHtml(run.result.drive_link)}">Open in Drive</button>` : "");
  } else if (run.status === "failed") {
    actions.innerHTML = `<button class="button button-primary" data-run="${escapeHtml(run.source)}">Try again</button>` +
      (run.source === "tableau" ? `<button class="button" data-run="tableau" data-watch="1">Try again and show the browser</button>` : "") +
      (/mission/i.test(run.error) ? `<button class="button" data-view="settings">Fix mission name</button>` : "");
  } else {
    actions.innerHTML = "";
  }
}

const FOLDER_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 5h7l2 2h9v12H3V5Z"/></svg>';
const ARROW_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9.3 5.3 7.9 6.7l5.3 5.3-5.3 5.3 1.4 1.4 6.7-6.7-6.7-6.7Z"/></svg>';

// One line per report: the dates it covers and when it was made. Click anywhere on it to open it.
function renderReports() {
  const list = $("#report-list");
  const html = !state.recent.length ? `<p class="empty-state">No reports yet.</p>` : state.recent.map((run, index) => `
    <button class="report-row" data-open="${escapeHtml(run.folder)}" title="${escapeHtml(run.folder)}">
      <span class="report-icon">${FOLDER_ICON}</span>
      <span class="report-main"><strong>${escapeHtml(run.period || dateTime(run.generated_utc))}</strong>
        <span>${capital(daysAgo(run.generated_utc))}</span></span>
      ${index === 0 ? '<span class="badge">Latest</span>' : ""}
      <span class="report-open">Open ${ARROW_ICON}</span>
    </button>`).join("");
  // Redrawing on every poll would drop hover and restart animations.
  if (list.dataset.html !== html) {
    list.innerHTML = html;
    list.dataset.html = html;
  }
}

function render() {
  const run = state.run;
  const running = run?.status === "running";
  const ready = state.account.saved;
  const waiting = running && Boolean(run.attention);
  // Setup comes first; once it is finished, go straight to the run screen.
  if (view === null) showView(ready ? "home" : "settings");
  else if (ready && wasReady === false) {
    showView("home");
    toast("All set. Click Run now whenever you're ready.");
  }
  wasReady = ready;

  const pill = $("#status-pill");
  pill.className = "status-pill " + (waiting ? "is-warn" : running ? "is-busy" : run?.status === "failed" ? "is-error" : ready ? "is-ok" : "is-warn");
  $("#status-text").textContent = waiting ? "Needs you" : running ? "Running" : run?.status === "failed" ? "Last run stopped" : ready ? "Ready" : "Setup needed";
  $("#brand-mission").textContent = state.configured ? state.settings.mission : "Mission reports";
  renderSettings(ready);
  renderRunCard(ready, running);
  renderDrive(running);
  renderWork(run);
  renderReports();
}

async function refresh() {
  try {
    state = await api("/api/state");
    if (!formsFilled) fillForms();
    render();
  } catch (error) {
    $("#status-pill").className = "status-pill is-error";
    $("#status-text").textContent = "Disconnected · reopen Referral Dash";
  }
}

document.addEventListener("click", async (event) => {
  const target = event.target.closest("button");
  if (!target) return;
  if (target.dataset.view) showView(target.dataset.view);
  if (target.dataset.open) act("/api/open", { target: target.dataset.open });
  if (target.dataset.run) {
    const ready = state.account.saved;
    if (target.dataset.run !== "sample" && !ready) return showView("settings");
    showView("home");
    target.disabled = true;
    await act("/api/run", { source: target.dataset.run, watch: Boolean(target.dataset.watch) });
    target.disabled = false;
  }
});

$("#advanced-form").addEventListener("input", renderDateChoices);
// Typing a number of days picks that option.
$$(".segment .days-input").forEach((field) => field.addEventListener("focus", () => {
  const radio = field.closest(".segment").querySelector('input[type="radio"]');
  if (radio.checked) return;
  radio.checked = true;
  radio.form.classList.add("is-dirty");
  renderDateChoices();
}));
// Save buttons appear once something has changed.
for (const form of [$("#account-form"), $("#mission-form"), $("#advanced-form")]) {
  form.addEventListener("input", () => form.classList.add("is-dirty"));
  form.addEventListener("change", () => form.classList.add("is-dirty"));
}
$("#drive-connect").addEventListener("click", () => act("/api/drive/connect", {}));
$("#drive-cancel").addEventListener("click", () => act("/api/drive/cancel", {}));
$("#drive-disconnect").addEventListener("click", () => act("/api/drive/disconnect", {}, "Google Drive disconnected. Nothing was removed from your Drive."));
$("#drive-open").addEventListener("click", () => act("/api/open", { target: state.drive.folder_link }));
$("#output-change").addEventListener("click", () => act("/api/output-folder", {}));
$("#output-open").addEventListener("click", () => act("/api/open", { target: state.output_folder }));
$("#keep-input").addEventListener("change", async (event) => {
  const field = event.target;
  const typed = field.value.trim();
  // A blank box is not "keep them all"; put the saved number back.
  const saved = typed !== "" && await act("/api/settings", { keep_reports: typed }, "Saved.");
  field.value = saved ? Number(typed) : state.settings.keep_reports;
});
$("#show-password").addEventListener("click", (event) => {
  const field = $("#account-password");
  field.type = field.type === "password" ? "text" : "password";
  event.target.textContent = field.type === "password" ? "Show" : "Hide";
});

$("#mission-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  // Zone overrides live under Advanced; keep them when the mission is saved.
  const auto = form.timezone.value === "auto";
  const body = { timezone_auto: auto, timezones: { ...overrides(), default: auto ? state.settings.timezones.default : form.timezone.value } };
  // Blank means "find it in Tableau", so only a typed name is sent.
  if (form.mission.value.trim()) body.mission = form.mission.value;
  if (await act("/api/settings", body, "Mission saved.")) form.classList.remove("is-dirty");
});

$("#account-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  if (await act("/api/account", { email: form.email.value, username: form.username.value, password: form.password.value }, "Church account saved.")) {
    form.password.value = "";
    form.classList.remove("is-dirty");
  }
});

$("#advanced-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const timezones = { default: state.settings.timezones.default };
  for (const line of form.overrides.value.split("\n")) {
    if (!line.trim()) continue;
    const at = line.lastIndexOf("=");
    if (at < 1) return toast("Zone lines look like: Hawaii = Pacific/Honolulu", true);
    timezones[line.slice(0, at).trim()] = line.slice(at + 1).trim();
  }
  const body = { timezones, report_mode: form.report_mode.value, report_start: form.report_start.value, report_end: form.report_end.value,
                 compare_mode: form.compare_mode.value, compare_days: form.compare_days.value, summary_days: form.summary_days.value,
                 compare_start: form.compare_start.value, compare_end: form.compare_end.value,
                 lookback_days: form.lookback_days.value, start_time: form.start_time.value, cutoff_time: form.cutoff_time.value,
                 lightning: form.lightning.value, pepper: form.pepper.value, tableau_view_url: form.tableau_view_url.value,
                 show_browser: form.show_browser.checked };
  if (await act("/api/settings", body, "Advanced settings saved.")) form.classList.remove("is-dirty");
});

refresh();
setInterval(refresh, 1000);
