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
const TITLES = { run: "Run reports", reports: "Past reports", settings: "Settings" };
const STEP_ICONS = { pending: "", active: "", done: "✓", failed: "!" };
const RUN_ICONS = {
  running: "M12 2a10 10 0 1 0 10 10h-3a7 7 0 1 1-7-7V2Z",
  done: "M9.5 16.2 5.3 12l-1.4 1.4 5.6 5.6L20.1 8.4 18.7 7 9.5 16.2Z",
  failed: "M11 7h2v7h-2V7Zm0 9h2v2h-2v-2Zm1-14a10 10 0 1 1 0 20 10 10 0 0 1 0-20Zm0 2a8 8 0 1 0 0 16 8 8 0 0 0 0-16Z",
};
let state = null;
let formsFilled = false;

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

function showView(name, focus) {
  $$(".nav-item").forEach((item) => item.classList.toggle("is-active", item.dataset.view === name));
  $$(".view").forEach((view) => view.classList.toggle("is-active", view.id === `${name}-view`));
  $("#page-title").textContent = TITLES[name];
  if (focus) setTimeout(() => document.getElementById(focus)?.focus(), 50);
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function shortDate(iso) {
  return iso ? new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "";
}

function dateTime(iso) {
  return iso ? new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "";
}

function folderName(path) {
  return path.split(/[\\/]/).filter(Boolean).pop() || path;
}

function fillForms() {
  const s = state.settings;
  const mission = $("#mission-form");
  mission.mission.value = s.mission === "Your Mission" ? "" : s.mission;
  $("#timezone-select").innerHTML = state.timezones.map((zone) => `<option>${zone}</option>`).join("");
  mission.timezone.value = s.timezones.default;
  for (const key of ["lookback_days", "start_time", "cutoff_time", "lightning", "pepper"]) mission[key].value = s[key];
  mission.overrides.value = Object.entries(s.timezones).filter(([zone]) => zone !== "default").map(([zone, tz]) => `${zone} = ${tz}`).join("\n");
  const account = $("#account-form");
  account.email.value = state.account.email;
  account.username.value = state.account.username;
  account.password.placeholder = state.account.saved ? "Saved · leave blank to keep it" : "";
  const advanced = $("#advanced-form");
  advanced.tableau_view_url.value = s.tableau_view_url;
  advanced.show_browser.checked = s.show_browser;
  $("#drive-folder").value = state.drive.folder_link;
  formsFilled = true;
}

function renderSetup() {
  const done = { mission: state.configured, account: state.account.saved, drive: Boolean(state.drive.account && state.drive.folder_id) };
  $("#check-mission").classList.toggle("is-done", done.mission);
  $("#check-account").classList.toggle("is-done", done.account);
  $("#check-drive").classList.toggle("is-done", done.drive);
  $("#setup-checklist").hidden = done.mission && done.account;
  return done.mission && done.account;
}

function renderRunCard(ready, running) {
  const badge = $("#run-badge");
  badge.className = "status-badge " + (running ? "running" : ready ? "ready" : "setup");
  badge.textContent = running ? "Running" : ready ? "Ready" : "Needs setup";
  $("#run-button").disabled = running || !ready;
  $("#run-button").textContent = running ? "Running…" : "Run now";
  $$("[data-run='file'], [data-run='sample']").forEach((button) => (button.disabled = running));
  const days = state.settings.lookback_days;
  $("#run-description").textContent = `Signs in to Tableau, downloads the last ${days} days of referrals, builds every dashboard and district summary, compares with last week` +
    (state.drive.folder_id ? ", and uploads everything to Google Drive." : ", and saves everything on this computer.");
  $("#flow-drive").textContent = state.drive.folder_id ? "Drive" : "This PC";
  $("#flow-drive").classList.toggle("is-off", !state.drive.folder_id);

  $("#output-name").textContent = folderName(state.output_folder);
  $("#output-path").textContent = state.output_folder;
  $("#output-path").title = state.output_folder;
  if (state.drive.folder_id) {
    $("#drive-name").textContent = state.drive.folder_name || "Drive folder";
    $("#drive-detail").textContent = state.drive.account ? `as ${state.drive.account}` : "Reconnect Google Drive in Settings";
  } else {
    $("#drive-name").textContent = "Not set up";
    $("#drive-detail").textContent = "Optional";
  }
  $("#drive-setup").textContent = state.drive.folder_id ? "Change" : "Set up";
  const last = state.recent.find((run) => run.mission === state.settings.mission);
  $("#compare-name").textContent = last ? `Automatic · last run ${shortDate(last.generated_utc)}` : "Automatic";
  $("#compare-detail").textContent = "Compares with last week's run, or the latest earlier one";
}

function elapsed(run) {
  const end = run.finished ? new Date(run.finished) : new Date();
  const seconds = Math.max(0, Math.round((end - new Date(run.started)) / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function renderRun(run) {
  const panel = $("#active-work");
  const result = $("#run-result");
  if (!run) {
    panel.classList.add("is-hidden");
    result.hidden = true;
    return;
  }
  panel.classList.remove("is-hidden");
  panel.classList.toggle("is-done", run.status === "done");
  panel.classList.toggle("is-failed", run.status === "failed");
  const waiting = run.status === "running" && Boolean(run.attention);
  panel.classList.toggle("is-attention", waiting);
  $("#active-work .work-spinner path").setAttribute("d", RUN_ICONS[run.status]);
  $("#work-title").textContent = waiting ? "Waiting for you" : { running: "Making reports", done: "Reports are ready", failed: "The run stopped" }[run.status];
  $("#work-message").textContent = run.status === "failed" ? run.error : waiting ? run.attention : run.message;
  $("#work-time").textContent = elapsed(run);
  $("#work-percent").textContent = `${run.percent}%`;
  $("#work-bar").style.width = `${Math.max(run.percent, 1)}%`;
  $("#work-steps").innerHTML = run.steps.map((step) => `
    <div class="work-event is-${step.state}"><span>${STEP_ICONS[step.state]}</span>
      <div><strong>${escapeHtml(step.label)}</strong><small>${escapeHtml(step.detail || { pending: "Waiting", active: "In progress", done: "Done", failed: "Failed" }[step.state])}</small></div></div>`).join("");

  result.hidden = run.status !== "done";
  if (run.status === "done") {
    const r = run.result;
    result.innerHTML = `
      <div class="stat"><small>Dashboards</small><strong>${r.dashboards}</strong></div>
      <div class="stat"><small>District summaries</small><strong>${r.summaries}</strong></div>
      <div class="stat"><small>Referrals</small><strong>${r.referrals}</strong></div>
      <div class="stat"><small>Compared with</small><strong class="small">${r.compared_with ? dateTime(r.compared_with) : "First run"}</strong></div>
      <div class="result-actions">
        <button class="button button-secondary" data-open="${escapeHtml(r.folder)}">Open folder</button>
        ${r.drive_link ? `<button class="button button-primary" data-open="${escapeHtml(r.drive_link)}">Open in Drive</button>` : ""}
      </div>`;
  }
}

function renderReports() {
  const list = $("#report-list");
  if (!state.recent.length) {
    list.innerHTML = `<p class="empty-state">No reports yet. Run reports to make your first set.</p>`;
    return;
  }
  list.innerHTML = state.recent.map((run) => `
    <div class="report-row">
      <span class="report-marker"></span>
      <div class="report-main"><strong>${escapeHtml(run.mission || "Report")} · ${run.referrals ?? "?"} referrals</strong>
        <span>${run.images ?? "?"} pictures · ${run.compared_with ? `compared with ${shortDate(run.compared_with)}` : "no comparison"}</span></div>
      <span class="report-time">${dateTime(run.generated_utc)}</span>
      <div class="report-actions">
        <button class="button button-secondary" data-open="${escapeHtml(run.folder)}">Open</button>
        ${run.drive_link ? `<button class="button button-secondary" data-open="${escapeHtml(run.drive_link)}">Drive</button>` : ""}
      </div>
    </div>`).join("");
}

function renderDrive() {
  const d = state.drive;
  const status = $("#drive-state");
  status.className = "connection-state" + (d.account ? " is-connected" : d.error ? " is-error" : "");
  status.textContent = d.connecting ? "Finish signing in to Google in your browser…" : d.account ? `Connected as ${d.account}` : d.error || "Not connected";
  $("#drive-connect").hidden = Boolean(d.account);
  $("#drive-connect").disabled = d.connecting;
  $("#drive-disconnect").hidden = !d.account;
  $("#drive-folder-form").hidden = !d.account;
  $("#drive-folder-name").textContent = d.folder_id ? `Uploading into “${d.folder_name || d.folder_id}”. Each run adds a dated folder.` : "Paste a folder link to upload every run there.";
  $("#drive-clear").disabled = !d.folder_id;
}

function render() {
  const run = state.run;
  const running = run?.status === "running";
  const ready = renderSetup();
  const pill = $("#system-pill");
  const waiting = running && Boolean(run.attention);
  pill.className = "system-pill" + (waiting ? " needs-setup" : running ? " is-busy" : run?.status === "failed" ? " has-error" : ready ? "" : " needs-setup");
  $("#system-text").textContent = waiting ? "Needs you" : running ? "Running" : run?.status === "failed" ? "Last run failed" : ready ? "Ready" : "Setup needed";
  $("#brand-mission").textContent = state.configured ? state.settings.mission : "Mission reports";
  $("#footer-version").textContent = `Referral Dash ${state.version}`;
  renderRunCard(ready, running);
  renderRun(run);
  renderReports();
  renderDrive();
}

async function refresh() {
  try {
    state = await api("/api/state");
    if (!formsFilled) fillForms();
    render();
  } catch (error) {
    $("#system-pill").className = "system-pill has-error";
    $("#system-text").textContent = "Disconnected · reopen Referral Dash";
  }
}

document.addEventListener("click", async (event) => {
  const target = event.target.closest("button");
  if (!target) return;
  if (target.dataset.view) showView(target.dataset.view);
  if (target.dataset.openView) showView(target.dataset.openView, target.dataset.focus);
  if (target.dataset.open) act("/api/open", { target: target.dataset.open });
  if (target.dataset.run) {
    target.disabled = true;
    await act("/api/run", { source: target.dataset.run });
    target.disabled = false;
  }
});

$("#output-change").addEventListener("click", () => act("/api/output-folder", {}));
$("#open-output").addEventListener("click", () => act("/api/open", { target: state.output_folder }));
$("#show-password").addEventListener("change", (event) => ($("#account-password").type = event.target.checked ? "text" : "password"));
$("#drive-connect").addEventListener("click", () => act("/api/drive/connect", {}));
$("#drive-disconnect").addEventListener("click", () => act("/api/drive/disconnect", {}, "Google Drive disconnected."));
$("#drive-clear").addEventListener("click", async () => {
  if (await act("/api/drive/folder", { link: "" }, "Reports will stay on this computer.")) $("#drive-folder").value = "";
});

$("#mission-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const timezones = { default: form.timezone.value };
  for (const line of form.overrides.value.split("\n")) {
    if (!line.trim()) continue;
    const at = line.lastIndexOf("=");
    if (at < 1) return toast("Zone overrides look like: Hawaii = HST", true);
    timezones[line.slice(0, at).trim()] = line.slice(at + 1).trim().toUpperCase();
  }
  const body = { mission: form.mission.value, timezones, lookback_days: form.lookback_days.value, start_time: form.start_time.value,
                 cutoff_time: form.cutoff_time.value, lightning: form.lightning.value, pepper: form.pepper.value };
  if (await act("/api/settings", body, "Mission saved.")) formsFilled = false;
});

$("#account-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  if (await act("/api/account", { email: form.email.value, username: form.username.value, password: form.password.value }, "Church account saved in Windows Credential Manager.")) {
    form.password.value = "";
    formsFilled = false;
  }
});

$("#advanced-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  if (await act("/api/settings", { tableau_view_url: form.tableau_view_url.value, show_browser: form.show_browser.checked }, "Tableau connection saved.")) formsFilled = false;
});

$("#drive-folder-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (await act("/api/drive/folder", { link: $("#drive-folder").value }, "Drive folder saved.")) formsFilled = false;
});

refresh();
setInterval(refresh, 1000);
