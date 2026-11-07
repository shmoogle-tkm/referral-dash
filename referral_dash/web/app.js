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
// The dates apply to one run; until they are changed they follow today's default range.
let rangeEdited = false;

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

function toast(message, isError = false) {
  const element = document.createElement("div");
  element.className = "toast" + (isError ? " error" : "");
  element.textContent = message;
  $("#toast-region").append(element);
  setTimeout(() => element.remove(), isError ? 9000 : 4000);
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
  view = name;
  $("#home-view").hidden = name !== "home";
  $("#settings-view").hidden = name !== "settings";
  $("#settings-button").hidden = name === "settings";
  window.scrollTo(0, 0);
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

// The range the change arrows compare with: a preset shift, or the dates typed in.
function compareRange() {
  const [start, end] = [$("#range-start").value, $("#range-end").value];
  const choice = $("#compare-select").value;
  if (choice === "custom") return [$("#compare-start").value, $("#compare-end").value];
  return start && end ? [shifted(start, Number(choice)), shifted(end, Number(choice))] : ["", ""];
}

function renderRange(ready, running) {
  const [start, end] = [$("#range-start"), $("#range-end")];
  $("#range").hidden = !ready;
  if (!rangeEdited) [start.value, end.value] = state.default_range;
  start.max = end.max = state.default_range[1];
  start.disabled = end.disabled = running;
  const [compareStart, compareEnd] = [$("#compare-start"), $("#compare-end")];
  const custom = $("#compare-select").value === "custom";
  $("#compare-fields").hidden = !custom;
  // Opening the custom dates starts them from the range that was just showing.
  if (!custom) [compareStart.value, compareEnd.value] = compareRange();
  compareStart.max = compareEnd.max = state.default_range[1];
  compareStart.disabled = compareEnd.disabled = $("#compare-select").disabled = running;
  const [first, last] = compareRange();
  $("#range-note").textContent = !start.value || !end.value ? "Choose both report dates."
    : !first || !last ? "Choose both dates to compare with."
    : last >= end.value ? "The comparison dates must end before the report's end date."
    : `Change arrows compare with ${longDate(first)} – ${longDate(last)}.`;
  $("#range-reset").hidden = !rangeEdited;
  $("#range-reset").textContent = `Back to the last ${state.settings.lookback_days} days`;
}

function daysAgo(iso) {
  const days = Math.floor((Date.now() - new Date(iso)) / DAY);
  return days <= 0 ? "today" : days === 1 ? "yesterday" : `${days} days ago`;
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
  $("#timezone-select").innerHTML = state.timezones.map((zone) => `<option>${zone}</option>`).join("");
  // Until the mission is saved, guess the time zone from this computer.
  mission.timezone.value = state.configured ? s.timezones.default : state.suggested_timezone;
  const account = $("#account-form");
  account.email.value = state.account.email;
  account.username.value = state.account.username;
  const advanced = $("#advanced-form");
  for (const key of ["lookback_days", "start_time", "cutoff_time", "lightning", "pepper", "tableau_view_url"]) advanced[key].value = s[key];
  advanced.overrides.value = Object.entries(overrides()).map(([zone, tz]) => `${zone} = ${tz}`).join("\n");
  advanced.show_browser.checked = s.show_browser;
  $("#keep-select").value = String(s.keep_reports);
  if (![...$("#keep-select").options].some((o) => o.value === String(s.keep_reports))) {
    $("#keep-select").add(new Option(String(s.keep_reports), String(s.keep_reports)), 0);
    $("#keep-select").value = String(s.keep_reports);
  }
  formsFilled = true;
}

function renderSettings(ready) {
  const setup = !ready;
  document.body.classList.toggle("is-setup", setup);
  $("#welcome").hidden = !setup;
  $("#settings-head").hidden = setup;
  $("#setup-alt").hidden = !setup;
  $("#advanced").hidden = setup;
  $("#account-step").textContent = state.account.saved ? "✓" : "1";
  $("#account-step").classList.toggle("is-done", state.account.saved);
  $("#mission-step").textContent = state.configured ? "✓" : "2";
  $("#mission-step").classList.toggle("is-done", state.configured);
  $("#account-note").textContent = state.account.saved ? "Saved on this computer" : "";
  $("#account-password").placeholder = state.account.saved ? "Saved · leave blank to keep it" : "";
  $("#mission-note").textContent = state.configured ? `Saved · ${state.settings.timezones.default}` : "";
}

function renderRunCard(ready, running) {
  const last = state.recent.find((run) => run.mission === state.settings.mission);
  const due = !last || Date.now() - new Date(last.generated_utc) >= 6 * DAY;
  $("#run-eyebrow").textContent = !ready ? "Almost there" : last ? `Last run ${shortDate(last.generated_utc)} · ${daysAgo(last.generated_utc)}` : "First run";
  $("#run-title").textContent = !ready ? "Finish setup to make reports" : rangeEdited ? "Make reports for these dates" : due ? "Make this week's reports" : "This week's reports are done";
  $("#run-lede").textContent = !ready
    ? "Add your Church account and mission in Settings. It takes a minute."
    : "Signs in to Tableau, downloads your referrals, and makes every dashboard and district summary, compared with the week before.";
  const button = $("#run-button");
  button.disabled = running;
  const fresh = due || rangeEdited;
  button.textContent = running ? "Running…" : !ready ? "Finish setup" : fresh ? "Run now" : "Run again";
  button.classList.toggle("is-quiet", ready && !fresh && !running);
  renderRange(ready, running);
  $$(".run-alternatives [data-run]").forEach((b) => (b.disabled = running));

  $("#output-name").textContent = folderName(state.output_folder);
  $("#output-path").textContent = state.output_folder;
  $("#output-path").title = state.output_folder;
  const keep = state.settings.keep_reports;
  $("#keep-help").textContent = keep ? "report sets. Older ones are deleted after each run to save space." : "report sets. Nothing is deleted.";
}

function elapsed(run) {
  const end = run.finished ? new Date(run.finished) : new Date();
  const seconds = Math.max(0, Math.round((end - new Date(run.started)) / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function renderWork(run) {
  const panel = $("#work");
  panel.hidden = !run;
  if (!run) return;
  const waiting = run.status === "running" && Boolean(run.attention);
  panel.className = `card work is-${run.status}` + (waiting ? " is-attention" : "");
  $("#work .work-icon path").setAttribute("d", RUN_ICONS[run.status]);
  $("#work-title").textContent = waiting ? "Waiting for you" : { running: "Making reports", done: "Reports are ready", failed: "The run stopped" }[run.status];
  $("#work-message").textContent = run.status === "failed" ? run.error : waiting ? run.attention : run.status === "done" ? `Finished in ${elapsed(run)}` : run.message;
  $("#work-time").textContent = elapsed(run);
  $("#work-bar").style.width = `${run.status === "running" ? Math.max(run.percent, 2) : 100}%`;
  $("#work-steps").innerHTML = run.steps.map((step) => `
    <li class="is-${step.state}"><span>${STEP_ICONS[step.state]}</span>
      <div><strong>${escapeHtml(step.label)}</strong><small>${escapeHtml(step.detail || { pending: "Waiting", active: "In progress", done: "Done", failed: "Failed" }[step.state])}</small></div></li>`).join("");

  const result = $("#work-result");
  result.hidden = run.status !== "done";
  if (run.status === "done") {
    const r = run.result;
    result.innerHTML = `
      <div class="stat"><strong>${r.referrals}</strong><small>Referrals</small></div>
      <div class="stat"><strong>${r.dashboards}</strong><small>Dashboards</small></div>
      <div class="stat"><strong>${r.summaries}</strong><small>District summaries</small></div>
      <div class="stat"><strong class="small">${r.compared_with ? escapeHtml(r.compared_with) : "—"}</strong><small>${r.compared_with ? "Compared with" : "Comparison dates not in this file"}</small></div>` +
      (r.removed ? `<p class="result-note">Removed ${r.removed} older report set${r.removed === 1 ? "" : "s"} to save space.</p>` : "");
  }
  const actions = $("#work-actions");
  actions.hidden = run.status === "running";
  if (run.status === "done") {
    actions.innerHTML = `<button class="button button-primary" data-open="${escapeHtml(run.result.folder)}">Open reports</button>`;
  } else if (run.status === "failed") {
    actions.innerHTML = `<button class="button button-primary" data-run="${escapeHtml(run.source)}">Try again</button>` +
      (run.source === "tableau" ? `<button class="button" data-run="tableau" data-watch="1">Try again and show the browser</button>` : "") +
      (/mission/i.test(run.error) ? `<button class="button" data-view="settings">Fix mission name</button>` : "");
  }
}

function renderReports() {
  const list = $("#report-list");
  if (!state.recent.length) {
    list.innerHTML = `<p class="empty-state">No reports yet. Your first run will show up here.</p>`;
    return;
  }
  list.innerHTML = state.recent.map((run) => `
    <div class="report-row">
      <div class="report-main"><strong>${dateTime(run.generated_utc)}</strong>
        <span>${run.period ? `${escapeHtml(run.period)} · ` : ""}${run.referrals ?? "?"} referrals · ${run.images ?? "?"} pictures · ${run.compared_with ? `vs ${escapeHtml(run.compared_with)}` : "no comparison"}</span></div>
      <button class="button" data-open="${escapeHtml(run.folder)}">Open</button>
    </div>`).join("");
}

function render() {
  const run = state.run;
  const running = run?.status === "running";
  const ready = state.configured && state.account.saved;
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
  $("#footer-version").textContent = `Referral Dash ${state.version}`;
  renderSettings(ready);
  renderRunCard(ready, running);
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
    const ready = state.configured && state.account.saved;
    if (target.dataset.run !== "sample" && !ready) return showView("settings");
    const body = { source: target.dataset.run, watch: Boolean(target.dataset.watch) };
    // A CSV keeps its own dates unless a range was chosen on purpose.
    if (target.dataset.run === "tableau" || (target.dataset.run === "file" && rangeEdited)) {
      [body.start, body.end] = [$("#range-start").value, $("#range-end").value];
      [body.compare_start, body.compare_end] = compareRange();
      if (!body.start || !body.end || !body.compare_start || !body.compare_end) return toast("Choose all the dates first.", true);
    }
    showView("home");
    target.disabled = true;
    await act("/api/run", body);
    target.disabled = false;
  }
});

for (const field of [$("#range-start"), $("#range-end"), $("#compare-select"), $("#compare-start"), $("#compare-end")]) {
  field.addEventListener("input", () => {
    rangeEdited = true;
    render();
  });
}
$("#range-reset").addEventListener("click", () => {
  rangeEdited = false;
  $("#compare-select").value = "7";
  render();
});
$("#output-change").addEventListener("click", () => act("/api/output-folder", {}));
$("#output-open").addEventListener("click", () => act("/api/open", { target: state.output_folder }));
$("#keep-select").addEventListener("change", (event) => act("/api/settings", { keep_reports: Number(event.target.value) }, "Saved."));
$("#show-password").addEventListener("click", (event) => {
  const field = $("#account-password");
  field.type = field.type === "password" ? "text" : "password";
  event.target.textContent = field.type === "password" ? "Show" : "Hide";
});

$("#mission-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  // Zone overrides live under Advanced; keep them when the mission is saved.
  const body = { mission: form.mission.value, timezones: { ...overrides(), default: form.timezone.value } };
  await act("/api/settings", body, "Mission saved.");
});

$("#account-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  if (await act("/api/account", { email: form.email.value, username: form.username.value, password: form.password.value }, "Church account saved.")) {
    form.password.value = "";
  }
});

$("#advanced-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const timezones = { default: state.settings.timezones.default };
  for (const line of form.overrides.value.split("\n")) {
    if (!line.trim()) continue;
    const at = line.lastIndexOf("=");
    if (at < 1) return toast("Zone lines look like: Hawaii = HST", true);
    timezones[line.slice(0, at).trim()] = line.slice(at + 1).trim().toUpperCase();
  }
  const body = { timezones, lookback_days: form.lookback_days.value, start_time: form.start_time.value, cutoff_time: form.cutoff_time.value,
                 lightning: form.lightning.value, pepper: form.pepper.value, tableau_view_url: form.tableau_view_url.value,
                 show_browser: form.show_browser.checked };
  await act("/api/settings", body, "Advanced settings saved.");
});

refresh();
setInterval(refresh, 1000);
