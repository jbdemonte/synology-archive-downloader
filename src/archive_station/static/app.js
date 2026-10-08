"use strict";
const { t } = ArchiveI18n;
const $ = (id) => document.getElementById(id);
const state = {
  jobs: [],
  selected: null,
  checked: new Set(),
  filter: "all",
  search: "",
  settings: null,
  destinationLocked: false,
  expanded: new Map(),
  fileViews: new Map(),
  openedInitially: false,
  plans: [],
  inspection: null,
  folderTarget: null,
  folder: null,
  polling: false,
};
const labels = {
  queued: "En attente",
  running: "En cours",
  downloading: "En cours",
  paused: "En pause",
  completed: "Terminé",
  error: "À vérifier",
  cancelled: "Annulé",
};
const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const number = (n) => Number(n || 0).toLocaleString(ArchiveI18n.locale);
function bytes(n) {
  if (n == null) return t("Inconnue");
  if (!n) return t("0 o");
  const unit = Math.min(4, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** unit).toLocaleString(ArchiveI18n.locale, { maximumFractionDigits: unit ? 1 : 0 })} ${[t("o"), t("Kio"), t("Mio"), t("Gio"), t("Tio")][unit]}`;
}
function eta(job) {
  if (
    state.policy?.allowed === false ||
    !["running", "queued"].includes(job.status)
  )
    return "";
  const value =
    job.eta_seconds != null
      ? `${job.eta_lower_bound ? "≥" : "≈"} ${remainingDuration(job.eta_seconds)}`
      : t(
          {
            measuring: "Calcul…",
            stalled: "En attente de débit",
            unknown: "Inconnue",
            error: "À vérifier",
            verifying: "En cours",
          }[job.eta_state] || "En attente",
        );
  return `${t("Restant")} : ${value}`;
}
function remainingDuration(seconds) {
  const minutes = Math.max(1, Math.ceil(seconds / 60));
  const units = [
    ["day", Math.floor(minutes / 1440)],
    ["hour", Math.floor((minutes % 1440) / 60)],
    ["minute", minutes % 60],
  ];
  return units
    .filter(([, value]) => value)
    .slice(0, 2)
    .map(([unit, value]) =>
      new Intl.NumberFormat(ArchiveI18n.locale, {
        style: "unit",
        unit,
        unitDisplay: "short",
      }).format(value),
    )
    .join(" ");
}
function etaHint(job) {
  return [
    job.average_window_seconds > 0
      ? t("Moyenne sur {minutes} min : {speed}/s", {
          minutes: Number(job.average_window_seconds / 60).toLocaleString(
            ArchiveI18n.locale,
            { maximumFractionDigits: 1 },
          ),
          speed: bytes(job.average_speed),
        })
      : "",
    job.eta_lower_bound
      ? t("Estimation minimale : certains fichiers ont une taille inconnue.")
      : "",
  ]
    .filter(Boolean)
    .join(" · ");
}
function etaMarkup(job) {
  const label = eta(job);
  return label
    ? `<div class="remaining-time" title="${esc(etaHint(job))}">${esc(label)}</div>`
    : "";
}
const embedded = location.pathname.startsWith(
  "/webman/3rdparty/ArchiveStation/",
);
let authMode = embedded ? "dsm" : "password";
document.body.classList.toggle("dsm-embedded", embedded);
function restoreWindowLayout() {
  // DSM restores old geometry before loading our iframe. Migrate only this
  // application's formerly tall window, once per DSM user. Later manual sizes
  // (and maximized windows) remain the user's choice.
  try {
    const desktop = window.parent;
    const frame = window.frameElement;
    if (!embedded || !frame || !desktop.Ext?.getCmp) return;
    for (let node = frame.parentElement; node; node = node.parentElement) {
      const nativeWindow = node.id && desktop.Ext.getCmp(node.id);
      if (
        nativeWindow?.iframeId !== frame.id ||
        nativeWindow.jsConfig?.jsID !== "com.archivestation.app"
      )
        continue;
      const instance = nativeWindow.appInstance;
      if (instance.getUserSettings("layoutRevision") === 2) return;
      if (!nativeWindow.maximized && desktop.innerWidth >= 1000) {
        const size = nativeWindow.getSize();
        if (size.width / size.height < 1.35) {
          const width = Math.min(1360, desktop.innerWidth - 48);
          const height = Math.min(
            840,
            Math.round(width / 1.62),
            desktop.innerHeight - 96,
          );
          nativeWindow.setSize(width, height);
          desktop.SYNO.SDS.WindowMgr.centerWindow(nativeWindow);
          nativeWindow.onHandlerResize();
        }
      }
      instance.setUserSettings("layoutRevision", 2);
      return;
    }
  } catch {
    // An unsupported DSM window API must never prevent using the application.
  }
}
function dsmToken() {
  // DSM's own Ajax client uses this session token. Read it only from our
  // same-origin desktop parent and send it only to the DSM gateway.
  try {
    return window.parent.SYNO?.SDS?.Session?.SynoToken || "";
  } catch {
    return "";
  }
}
async function api(path, body, signal) {
  const endpoint = embedded
    ? `/webman/3rdparty/ArchiveStation/gateway.cgi?route=${encodeURIComponent(path)}`
    : path;
  const headers =
    body === undefined ? {} : { "Content-Type": "application/json" };
  const token = embedded ? dsmToken() : "";
  if (token) headers["X-SYNO-TOKEN"] = token;
  const response = await fetch(endpoint, {
    method: body === undefined ? "GET" : "POST",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
    signal,
  });
  let result;
  try {
    result = await response.json();
  } catch {
    if (response.status === 403)
      throw new Error(
        t(
          "Accès refusé par DSM. Vérifiez les permissions du dossier pour ArchiveStation.",
        ),
      );
    throw new Error(
      t(
        "Le service ne répond pas correctement. Réessayez dans quelques instants ou vérifiez le paquet dans DSM.",
      ),
    );
  }
  const status = embedded
    ? (result._http_status ?? response.status)
    : response.status;
  if (status >= 400) {
    if (status === 401 && path !== "/api/login") showLogin();
    throw new Error(t(result.error) || `HTTP ${status}`);
  }
  return result;
}
let toastTimer;
function toast(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    $("toast").hidden = true;
  }, 6000);
}
function showLogin() {
  $("login-screen").hidden = false;
  $("application").hidden = true;
  document.querySelectorAll("dialog[open]").forEach((d) => d.close());
  $("local-login").hidden = authMode !== "password";
  $("login-help").textContent =
    authMode === "dsm"
      ? t(
          "Connectez-vous à DSM avec un compte administrateur, puis ouvrez Archive Station depuis le menu principal.",
        )
      : t(
          "Utilisez le mot de passe initial indiqué dans le dossier d’état du serveur local.",
        );
}
function showApp() {
  $("login-screen").hidden = true;
  $("application").hidden = false;
}
function progress(row, size) {
  const percentage =
    row.status === "completed"
      ? 100
      : size
        ? Math.min(100, (row.downloaded / size) * 100)
        : 0;
  const text =
    row.status === "completed"
      ? "100 %"
      : size
        ? `${percentage.toFixed(percentage > 0 && percentage < 1 ? 1 : 0)} %`
        : "—";
  return `<div class="progress-label"><span>${text}</span><small>${bytes(row.downloaded)} / ${bytes(size)}</small></div><div class="meter ${esc(row.status)}" role="progressbar" aria-label="${esc(t("Progression de {name}", { name: row.name || row.identifier }))}" aria-valuenow="${percentage.toFixed(1)}" aria-valuemin="0" aria-valuemax="100"><i style="width:${percentage}%"></i></div>`;
}
function badge(status, error = "") {
  return `<span class="badge ${esc(status)}" title="${esc(t(error))}">${esc(t(labels[status] || status))}</span>`;
}
function key(jobId, prefix) {
  return `${jobId}|${prefix}`;
}
function fileView(jobId) {
  return (
    state.fileViews.get(jobId) ||
    (state.jobs.find((job) => job.id === jobId)?.status === "completed"
      ? "completed"
      : "activity")
  );
}
function fileTabs(job) {
  const tabs = [
    ["activity", "Activité", Math.max(0, job.file_count - job.completed_files)],
    ["completed", "Terminés", job.completed_files],
    ["tree", "Arborescence", null],
  ];
  if (job.failed_files) tabs.push(["error", "À vérifier", job.failed_files]);
  return `<tr class="file-tabs-row"><td colspan="5"><div class="file-tabs" role="group" aria-label="${esc(job.identifier)}">${tabs
    .map(
      ([view, label, count]) =>
        `<button type="button" data-file-view="${view}" data-view-job="${esc(job.id)}" aria-pressed="${fileView(job.id) === view}">${esc(t(label))}${count == null ? "" : `<span>${number(count)}</span>`}</button>`,
    )
    .join("")}</div></td></tr>`;
}
function fileRow(job, row, depth, showPath = false) {
  const folder = row.kind === "folder";
  const open = state.expanded.has(key(job.id, row.path));
  let status = row.status;
  if (
    ["paused", "cancelled"].includes(job.status) &&
    status !== "completed" &&
    status !== "error"
  )
    status = job.status;
  const toggle = folder
    ? `<button class="toggle" data-expand="${esc(job.id)}" data-prefix="${esc(row.path)}" aria-label="${esc(t(open ? "Replier" : "Déplier"))} ${esc(row.name)}" aria-expanded="${open}">${open ? "⌄" : "›"}</button>`
    : '<span class="tree-spacer"></span>';
  const parent =
    showPath && row.path.includes("/")
      ? row.path.slice(0, row.path.lastIndexOf("/"))
      : "";
  const detail = folder
    ? `${number(row.completed_files)} / ${number(row.file_count)} ${esc(t("fichiers"))}`
    : esc(parent);
  return `<tr class="child-row ${status === "downloading" ? "live-file" : ""}" data-job="${esc(job.id)}" data-file-path="${esc(row.path)}"><td><div class="tree-name" style="padding-left:${depth * 19}px">${toggle}<span class="tree-icon ${folder ? "" : "item-icon"}">${folder ? "▰" : "▤"}</span><div class="name-text"><span title="${esc(row.path)}">${esc(row.name)}</span>${detail ? `<small title="${esc(parent)}">${detail}</small>` : ""}</div></div></td><td>${bytes(row.size)}</td><td>${progress({ ...row, status }, row.size)}</td><td class="speed">${row.speed && !["paused", "cancelled"].includes(job.status) ? bytes(row.speed) + "/s" : "—"}</td><td>${badge(status, row.error)}${!folder && row.id && row.status === "queued" ? `<button class="file-priority" data-priority-file="${row.id}" data-priority-job="${esc(job.id)}" data-priority="${row.priority ? 0 : 1}" aria-pressed="${!!row.priority}" title="${esc(t("En premier"))}">${row.priority ? "★" : "☆"}</button>` : ""}</td></tr>`;
}
function flatFile(row) {
  return {
    ...row,
    path: row.name,
    name: row.name.split("/").pop(),
    kind: "file",
  };
}
function activityRows(job, data) {
  let html = "";
  for (const [field, status, label] of [
    ["active", "downloading", "En cours"],
    ["errors", "error", "À vérifier"],
    ["queued", "queued", "À suivre"],
  ]) {
    const rows = data[field],
      total = data.counts[status];
    if (!total && field !== "active") continue;
    const count =
      rows.length === total
        ? number(total)
        : `${number(rows.length)} / ${number(total)}`;
    html += `<tr class="activity-heading ${field}"><td colspan="5"><strong>${esc(t(label))}</strong><span>${count} ${esc(t("fichiers"))}</span></td></tr>`;
    html += rows.length
      ? rows.map((row) => fileRow(job, flatFile(row), 1, true)).join("")
      : `<tr class="file-empty"><td colspan="5">${esc(t("Aucun fichier en cours."))}</td></tr>`;
  }
  return html;
}
function treeRows(job, prefix, depth) {
  const branch = state.expanded.get(key(job.id, prefix));
  if (!branch) return "";
  let html = prefix ? "" : fileTabs(job);
  if (branch.error)
    return (
      html +
      `<tr class="child-row"><td colspan="5" class="error">${esc(branch.error)}</td></tr>`
    );
  if (!branch.data)
    return (
      html +
      `<tr class="child-row"><td colspan="5">${esc(t("Chargement des fichiers…"))}</td></tr>`
    );
  if (branch.view === "activity") return html + activityRows(job, branch.data);
  if (!branch.data.children.length)
    return (
      html +
      `<tr class="file-empty"><td colspan="5">${esc(t("Aucun fichier dans cette vue."))}</td></tr>`
    );
  for (const row of branch.data.children) {
    html += fileRow(job, row, depth, branch.view !== "tree");
    if (row.kind === "folder" && state.expanded.has(key(job.id, row.path)))
      html += treeRows(job, row.path, depth + 1);
  }
  if (branch.data.total > 100) {
    html += `<tr class="page-row"><td colspan="5"><button data-page="${esc(job.id)}" data-prefix="${esc(prefix)}" data-offset="${Math.max(0, branch.offset - 100)}" ${branch.offset === 0 ? "disabled" : ""}>${esc(t("← Précédents"))}</button><span>${number(branch.offset + 1)}–${number(Math.min(branch.offset + 100, branch.data.total))} ${esc(t("sur"))} ${number(branch.data.total)}</span><button data-page="${esc(job.id)}" data-prefix="${esc(prefix)}" data-offset="${branch.offset + 100}" ${branch.offset + 100 >= branch.data.total ? "disabled" : ""}>${esc(t("Suivants →"))}</button></td></tr>`;
  }
  return html;
}
function matches(job) {
  const search = `${job.identifier} ${job.title}`
    .toLocaleLowerCase()
    .includes(state.search.toLocaleLowerCase());
  const filter =
    state.filter === "all" ||
    (state.filter === "active"
      ? ["queued", "running"].includes(job.status)
      : state.filter === "error"
        ? job.status === "error" || job.failed_files
        : job.status === state.filter);
  return search && filter;
}
function render() {
  $("view-title").textContent = t(
    {
      all: "Transferts",
      active: "En cours",
      paused: "En pause",
      completed: "Terminés",
      error: "À vérifier",
    }[state.filter],
  );
  const sum = (field) =>
    state.jobs.reduce((total, job) => total + (job[field] || 0), 0);
  $("stat-speed").textContent = bytes(sum("speed")) + "/s";
  $("stat-files").innerHTML =
    `${number(sum("completed_files"))} <em>/ ${number(sum("file_count"))}</em>`;
  const unknown = state.jobs.some((job) => job.unknown_sizes);
  $("stat-bytes").innerHTML =
    `<span>${esc(bytes(sum("downloaded")))}</span><em>/ ${esc(bytes(sum("total_size")))}${unknown ? " +" : ""}</em>`;
  $("stat-bytes").title = unknown
    ? t("Estimation minimale : certains fichiers ont une taille inconnue.")
    : "";
  $("count-all").textContent = state.jobs.length;
  $("count-active").textContent = state.jobs.filter((j) =>
    ["queued", "running"].includes(j.status),
  ).length;
  $("count-paused").textContent = state.jobs.filter(
    (j) => j.status === "paused",
  ).length;
  $("count-completed").textContent = state.jobs.filter(
    (j) => j.status === "completed",
  ).length;
  $("count-error").textContent = state.jobs.filter(
    (j) => j.status === "error" || j.failed_files,
  ).length;
  const visible = state.jobs.filter(matches);
  state.checked = new Set(
    [...state.checked].filter((id) => visible.some((job) => job.id === id)),
  );
  $("select-visible").checked =
    !!visible.length && state.checked.size === visible.length;
  $("select-visible").indeterminate =
    state.checked.size > 0 && state.checked.size < visible.length;
  // A single visible archive is an unambiguous action target. Never keep an
  // invisible task selected when the user changes the filter or search.
  if (!visible.some((job) => job.id === state.selected))
    state.selected = visible.length === 1 ? visible[0].id : null;
  $("selection-hint").hidden =
    visible.length < 2 || !!state.selected || !!state.checked.size;
  $("empty").hidden = state.jobs.length > 0;
  $("no-results").hidden = !state.jobs.length || !!visible.length;
  $("list-count").textContent = t(
    visible.length === 1 ? "{count} tâche" : "{count} tâches",
    { count: number(visible.length) },
  );
  const focusedView = document.activeElement?.dataset.fileView;
  const focusedJob = document.activeElement?.dataset.viewJob;
  const focusedReport = document.activeElement?.dataset.reportJob;
  $("download-rows").innerHTML = visible
    .map((job) => {
      const open = state.expanded.has(key(job.id, ""));
      return `<tr class="job-row ${state.selected === job.id ? "selected" : ""}" data-job="${esc(job.id)}" tabindex="0" aria-selected="${state.selected === job.id}"><td><div class="tree-name"><input type="checkbox" class="job-check" data-select-job="${esc(job.id)}" ${state.checked.has(job.id) ? "checked" : ""} aria-label="${esc(t("Sélectionner {name}", { name: job.identifier }))}"/><button class="toggle" data-expand="${esc(job.id)}" data-prefix="" aria-label="${esc(t(open ? "Replier" : "Déplier"))} ${esc(job.identifier)}" aria-expanded="${open}">${open ? "⌄" : "›"}</button>${reportButton(job)}<div class="name-text"><strong title="${esc(job.identifier)}">${esc(job.identifier)}</strong><small title="${esc(job.title)}">${number(job.completed_files)} / ${number(job.file_count)} ${esc(t("fichiers"))} · ${esc(job.title)}</small></div></div></td><td>${bytes(job.total_size)}${job.unknown_sizes ? " +" : ""}</td><td>${progress(job, job.total_size)}${etaMarkup(job)}</td><td class="speed">${job.speed ? bytes(job.speed) + "/s" : "—"}</td><td>${badge(job.status)}</td></tr>${treeRows(job, "", 1)}`;
    })
    .join("");
  if (focusedReport)
    [...$("download-rows").querySelectorAll("[data-report-job]")]
      .find((button) => button.dataset.reportJob === focusedReport)
      ?.focus({ preventScroll: true });
  if (focusedView) {
    [...$("download-rows").querySelectorAll("[data-file-view]")]
      .find(
        (button) =>
          button.dataset.fileView === focusedView &&
          button.dataset.viewJob === focusedJob,
      )
      ?.focus({ preventScroll: true });
  }
  renderDetail();
}
const compactDetails = window.matchMedia("(max-height: 560px)");
if (compactDetails.matches) $("detail").open = false;
compactDetails.addEventListener("change", (event) => {
  if (event.matches) $("detail").open = false;
});
function selectedJobs() {
  return state.jobs.filter(
    (job) =>
      matches(job) &&
      (state.checked.size
        ? state.checked.has(job.id)
        : job.id === state.selected),
  );
}
$("select-visible").onchange = () => {
  state.checked = new Set(
    $("select-visible").checked
      ? state.jobs.filter(matches).map((job) => job.id)
      : [],
  );
  if (!state.checked.size) state.selected = null;
  render();
};
async function bulkAction(action, ids) {
  const result = await api("/api/jobs/bulk", { action, ids });
  await refresh();
  toast(
    `✓ ${t("{count} tâches", { count: result.updated.length })}${result.skipped.length ? ` · ${t("Ignorées")} : ${result.skipped.length}` : ""}`,
  );
}
$("global-action").onchange = async () => {
  const action = $("global-action").value;
  $("global-action").value = "";
  if (!action) return;
  try {
    await bulkAction(action);
  } catch (error) {
    toast(error.message);
  }
};
function renderDetail() {
  renderRefreshSummary();
  const job = state.jobs.find((j) => j.id === state.selected);
  $("detail").hidden = !job || state.checked.size > 1;
  const selected = selectedJobs();
  $("open-folder").hidden = !embedded || !job;
  $("refresh-manifest").disabled = !job;
  $("read-report").disabled = !job;
  $("repair").disabled =
    !job || ["queued", "running"].includes(job.status) || job.active_files > 0;
  $("pause").disabled = !selected.some((j) =>
    ["queued", "running"].includes(j.status),
  );
  $("resume").disabled = !selected.some((j) =>
    ["paused", "cancelled", "error"].includes(j.status),
  );
  $("retry").disabled = !selected.some((j) => j.failed_files);
  $("cancel").disabled = !selected.some(
    (j) => !["completed", "cancelled"].includes(j.status),
  );
  $("remove").disabled = !selected.some(
    (j) => !["queued", "running"].includes(j.status) && !j.active_files,
  );
  if (!job) return;
  $("detail-name").textContent = job.title;
  if (document.activeElement !== $("job-priority"))
    $("job-priority").value = job.priority || 0;
  $("detail-eta").textContent = eta(job);
  $("detail-eta").title = etaHint(job);
  $("detail-destination").textContent = `${job.destination}/${job.identifier}/`;
  $("detail-source").textContent = `archive.org/download/${job.identifier}`;
  $("detail-source").href =
    `https://archive.org/download/${encodeURIComponent(job.identifier)}`;
  $("detail-errors").textContent =
    job.hold_reason === "disk"
      ? t("Espace disque insuffisant.")
      : job.failed_files
        ? `${number(job.failed_files)} ${t("fichiers")} · ${t("À vérifier")} → ${t("Réessayer")}`
        : "";
}
function reportButton(job) {
  const count = job.incident_count || 0;
  const label = `${t("Lire le rapport")}${count ? ` · ${t("{count} incidents consignés", { count: number(count) })}` : ""}`;
  return `<button type="button" class="task-report ${count ? "has-incidents" : ""}" data-report-job="${esc(job.id)}" title="${esc(label)}" aria-label="${esc(label)}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h8l4 4v14H6zM14 3v5h4M9 12h6M9 16h6"/></svg>${count ? '<span aria-hidden="true">!</span>' : ""}</button>`;
}
let reportView = null;
async function loadReport(offset = 0) {
  const view = reportView;
  if (!view) return;
  view.controller?.abort();
  const controller = new AbortController();
  view.controller = controller;
  $("report-loading").hidden = false;
  $("report-error").textContent = "";
  for (const id of ["report-refresh", "report-prev", "report-next"])
    $(id).disabled = true;
  try {
    const data = await api(
      `/api/jobs/${view.jobId}/report?offset=${offset}`,
      undefined,
      controller.signal,
    );
    if (reportView !== view || view.controller !== controller) return;
    view.offset = data.offset;
    view.total = data.total;
    $("report-filename").textContent = data.filename;
    $("report-text").textContent = data.content;
    $("report-text").parentElement.scrollTop = 0;
    $("report-page").textContent =
      `${number(Math.floor(data.offset / 200) + 1)} / ${number(Math.ceil(data.total / 200))}`;
  } catch (error) {
    if (!controller.signal.aborted && reportView === view)
      $("report-error").textContent = error.message;
  } finally {
    if (reportView === view && view.controller === controller) {
      $("report-loading").hidden = true;
      $("report-refresh").disabled = false;
      $("report-prev").disabled = !view.offset;
      $("report-next").disabled =
        !view.total || view.offset + 200 >= view.total;
    }
  }
}
function openReport(jobId) {
  if (!jobId) return;
  reportView?.controller?.abort();
  reportView = { jobId, offset: 0, total: 0 };
  $("report-text").textContent = "";
  $("report-filename").textContent = "";
  $("report-page").textContent = "";
  $("report-dialog").showModal();
  loadReport();
}
$("read-report").onclick = () => openReport(state.selected);
$("report-refresh").onclick = () => loadReport();
$("report-prev").onclick = () =>
  loadReport(Math.max(0, reportView.offset - 200));
$("report-next").onclick = () => loadReport(reportView.offset + 200);
$("report-dialog").addEventListener("close", () => {
  reportView?.controller?.abort();
  reportView = null;
});
$("open-folder").onclick = async () => {
  const jobId = state.selected;
  if (!jobId) return;
  $("open-folder").disabled = true;
  try {
    const destination = await api(`/api/jobs/${jobId}/location`);
    const desktop = window.parent.SYNO?.SDS;
    if (!embedded || typeof desktop?.AppLaunch !== "function")
      throw new Error(t("Cette action nécessite DSM."));
    desktop.AppLaunch("SYNO.SDS.App.FileStation3.Instance", {
      opendir: destination.file_station_path,
    });
  } catch (error) {
    toast(error.message);
  } finally {
    $("open-folder").disabled = false;
  }
};
async function setPriority(change) {
  if (!state.selected) return;
  try {
    await api(`/api/jobs/${state.selected}/priority`, change);
    await refresh();
  } catch (error) {
    toast(error.message);
  }
}
$("job-priority").onchange = () =>
  setPriority({ priority: Number($("job-priority").value) });
$("queue-up").onclick = () => setPriority({ move: "up" });
$("queue-down").onclick = () => setPriority({ move: "down" });
async function loadBranch(jobId, prefix, offset = 0) {
  const view = fileView(jobId);
  if (prefix && view !== "tree") return;
  const id = key(jobId, prefix),
    previous = state.expanded.get(id);
  const branch = {
    offset,
    view,
    data: previous?.view === view ? previous.data : null,
  };
  state.expanded.set(id, branch);
  try {
    const endpoint =
      view === "activity"
        ? "activity"
        : view === "tree"
          ? `tree?prefix=${encodeURIComponent(prefix)}&offset=${offset}&limit=100`
          : `files?status=${view}&offset=${offset}&limit=100`;
    const data = await api(`/api/jobs/${jobId}/${endpoint}`);
    if (state.expanded.get(id) !== branch) return;
    branch.data = data.files
      ? { ...data, children: data.files.map(flatFile) }
      : data;
    branch.offset = data.offset ?? offset;
  } catch (error) {
    if (state.expanded.get(id) === branch) branch.error = error.message;
  }
}
function renderHistory(history) {
  const values = history?.values || Array(120).fill(0);
  const period = history?.period_seconds || 30;
  const windowSeconds = history?.window_seconds || values.length * period;
  const binWidth = 600 / values.length;
  const time = (seconds) =>
    new Intl.NumberFormat(ArchiveI18n.locale, {
      style: "unit",
      unit: "minute",
      unitDisplay: "short",
      maximumFractionDigits: 1,
    }).format(seconds ? -seconds / 60 : 0);
  $("history-start").textContent = time(windowSeconds);
  $("history-middle").textContent = time(windowSeconds / 2);
  $("history-end").textContent = time(0);
  const peak = Math.max(1, ...values);
  $("history-peak").textContent = bytes(Math.max(...values)) + "/s";
  const points = values
    .map((v, i) => `${(i + 0.5) * binWidth},${88 - (v / peak) * 82}`)
    .join(" ");
  $("history-chart").innerHTML =
    `<path d="M0 6H600M0 47H600M0 88H600" stroke="#e4eaf1" fill="none"/><polyline points="${points}" stroke="#0785e8" fill="none" stroke-width="2" vector-effect="non-scaling-stroke"/>` +
    values
      .map(
        (value, index) =>
          `<rect x="${index * binWidth}" y="0" width="${binWidth}" height="90" fill="transparent"><title>${esc(time((values.length - index) * period))} → ${esc(time((values.length - index - 1) * period))} : ${esc(bytes(value))}/s</title></rect>`,
      )
      .join("");
}
async function refresh() {
  if (state.polling || $("application").hidden) return;
  state.polling = true;
  try {
    const data = await api("/api/jobs");
    state.jobs = data.jobs;
    state.policy = data.policy;
    renderHistory(data.history);
    $("schedule-notice").hidden = !data.policy?.outside;
    $("schedule-notice").textContent =
      data.policy?.allowed === false
        ? t("En attente du créneau planifié.")
        : `${t("Débit réduit")} : ${bytes((data.policy?.limit_kib || 0) * 1024)}/s`;
    setDestinationLocked(
      state.jobs.some(
        (job) =>
          ["queued", "running"].includes(job.status) || job.active_files > 0,
      ),
    );
    if (!state.openedInitially && state.jobs.length) {
      state.openedInitially = true;
      const first =
        state.jobs.find(
          (job) => job.active_files || job.status === "running",
        ) || state.jobs.find((job) => job.status === "queued");
      if (first) state.expanded.set(key(first.id, ""), { offset: 0 });
    }
    for (const [id, branch] of [...state.expanded]) {
      if (state.expanded.get(id) !== branch) continue;
      const split = id.indexOf("|");
      const jobId = id.slice(0, split),
        prefix = id.slice(split + 1);
      if (!state.jobs.some((j) => j.id === jobId)) state.expanded.delete(id);
      else if (matches(state.jobs.find((job) => job.id === jobId)))
        await loadBranch(jobId, prefix, branch.offset);
    }
    render();
    $("connection").hidden = true;
    $("last-update").textContent = t("Actualisé à {time}", {
      time: new Date().toLocaleTimeString(ArchiveI18n.locale),
    });
  } catch (error) {
    $("connection").textContent = t(
      "Connexion au service interrompue. {error}",
      { error: error.message },
    );
    $("connection").hidden = false;
  } finally {
    state.polling = false;
  }
}
async function loadSettings() {
  state.settings = await api("/api/settings");
  const s = state.settings;
  const changed = await ArchiveI18n.apply(s.language || "auto", s.dsm_language);
  // Reports are generated even with the browser closed. Remember the language
  // resolved from the DSM session when the UI preference is automatic.
  if (s.language === "auto" && s.report_language !== ArchiveI18n.locale) {
    await api("/api/settings", { report_language: ArchiveI18n.locale }).catch(
      () => {},
    );
  }
  if (changed) render();
  setDestinationLocked(s.destination_locked === true);
  $("destination-short").textContent = s.download_dir;
  $("destination-short").title = s.download_dir;
  $("storage-text").textContent = s.storage
    ? t("{size} disponibles", { size: bytes(s.storage.free) })
    : t("Dossier à configurer");
  $("storage-fill").style.width = s.storage
    ? `${(1 - s.storage.free / s.storage.total) * 100}%`
    : "0";
  $("version").textContent = s.version;
}
function resetPlans() {
  state.plans = [];
  updateCapacity();
  $("preview-list").replaceChildren();
  $("create-button").hidden = true;
  $("inspect-button").hidden = false;
}
function finishInspection(inspection) {
  if (state.inspection !== inspection) return;
  for (const [control, disabled] of inspection.controls)
    control.disabled = disabled;
  state.inspection = null;
  renderPreviews();
  $("add-form")
    .querySelector(".dialog-body")
    .setAttribute("aria-busy", "false");
  $("inspect-status").hidden = true;
  $("inspect-button").textContent = t("Analyser les URL");
}
function stopInspection() {
  if (!state.inspection) return;
  state.inspection.controller.abort();
  finishInspection(state.inspection);
  resetPlans();
}
function openAdd() {
  resetPlans();
  $("add-error").textContent = "";
  $("job-destination").value = state.settings?.download_dir || "";
  $("add-dialog").showModal();
  $("urls").focus();
}
$("add-open").onclick = openAdd;
$("empty-add").onclick = openAdd;
document.querySelectorAll("[data-close]").forEach((button) => {
  button.onclick = () => {
    if (button.dataset.close === "add-dialog") stopInspection();
    $(button.dataset.close).close();
  };
});
$("add-dialog").addEventListener("cancel", stopInspection);
$("add-dialog").addEventListener("close", () => {
  if (!$("add-dialog").open) stopInspection();
});
for (const id of ["urls", "file-mode", "file-pattern"])
  $(id).addEventListener("input", resetPlans);
$("filters").onclick = (event) => {
  const button = event.target.closest("[data-filter]");
  if (!button) return;
  state.filter = button.dataset.filter;
  document
    .querySelectorAll("[data-filter]")
    .forEach((b) => b.classList.toggle("active", b === button));
  render();
};
$("search").oninput = () => {
  state.search = $("search").value;
  render();
};
$("download-rows").onclick = async (event) => {
  const report = event.target.closest("[data-report-job]");
  if (report) {
    openReport(report.dataset.reportJob);
    return;
  }
  const check = event.target.closest("[data-select-job]");
  if (check) {
    if (check.checked) state.checked.add(check.dataset.selectJob);
    else state.checked.delete(check.dataset.selectJob);
    state.selected = check.checked
      ? check.dataset.selectJob
      : [...state.checked][0] || null;
    render();
    return;
  }
  const priority = event.target.closest("[data-priority-file]");
  if (priority) {
    try {
      await api(`/api/jobs/${priority.dataset.priorityJob}/priority`, {
        file_id: Number(priority.dataset.priorityFile),
        priority: Number(priority.dataset.priority),
      });
      await refresh();
    } catch (error) {
      toast(error.message);
    }
    return;
  }
  const expand = event.target.closest("[data-expand]"),
    page = event.target.closest("[data-page]"),
    viewButton = event.target.closest("[data-file-view]"),
    row = event.target.closest("[data-job]");
  if (row) state.selected = row.dataset.job;
  if (viewButton) {
    const jobId = viewButton.dataset.viewJob;
    state.selected = jobId;
    state.fileViews.set(jobId, viewButton.dataset.fileView);
    for (const id of [...state.expanded.keys()])
      if (id.startsWith(key(jobId, ""))) state.expanded.delete(id);
    const pending = loadBranch(jobId, "");
    render();
    await pending;
  }
  if (expand) {
    const id = key(expand.dataset.expand, expand.dataset.prefix);
    if (state.expanded.has(id)) {
      for (const child of [...state.expanded.keys()])
        if (
          child === id ||
          child.startsWith(id + "/") ||
          (!expand.dataset.prefix && child.startsWith(id))
        )
          state.expanded.delete(child);
    } else {
      const pending = loadBranch(expand.dataset.expand, expand.dataset.prefix);
      render();
      await pending;
    }
  }
  if (page)
    await loadBranch(
      page.dataset.page,
      page.dataset.prefix,
      Number(page.dataset.offset),
    );
  render();
};
$("download-rows").onkeydown = (event) => {
  if (
    (event.key === "Enter" || event.key === " ") &&
    event.target.matches("tr[data-job]")
  ) {
    event.preventDefault();
    state.selected = event.target.dataset.job;
    render();
  }
};
for (const action of ["pause", "resume", "retry"])
  $(action).onclick = async () => {
    if (!selectedJobs().length) return;
    try {
      const ids = selectedJobs()
        .filter((job) =>
          action === "pause"
            ? ["queued", "running"].includes(job.status)
            : action === "resume"
              ? ["paused", "cancelled", "error"].includes(job.status)
              : job.failed_files,
        )
        .map((job) => job.id);
      if (ids.length === 1) await api(`/api/jobs/${ids[0]}/${action}`, {});
      else await bulkAction(action, ids);
      await refresh();
      if (ids.length === 1)
        toast(
          {
            pause: t(
              "Mise en pause demandée. Les octets reçus sont conservés.",
            ),
            resume: t("Reprise du téléchargement."),
            retry: t("Les fichiers en erreur ont été remis en attente."),
          }[action],
        );
    } catch (error) {
      toast(error.message);
    }
  };
let refreshPlan = null,
  refreshBusy = false,
  refreshSequence = 0;
let refreshApplying = false;
function renderRefreshSummary() {
  if (!$("refresh-dialog").open) return;
  const job = state.jobs.find((job) => job.id === refreshPlan?.job_id);
  const active =
    !job || ["queued", "running"].includes(job.status) || job.active_files > 0;
  $("refresh-controls").disabled = refreshBusy;
  $("refresh-close").disabled = refreshApplying;
  $("refresh-loading").hidden = !refreshBusy;
  $("refresh-warning").hidden = !active || refreshBusy;
  $("refresh-select").disabled = !refreshPlan?.file_count;
  $("refresh-apply").disabled =
    refreshBusy || active || !refreshPlan?.selected_count;
  $("refresh-summary").textContent = refreshPlan
    ? `${t("Nouveaux")} : ${refreshPlan.added_count} · ${t("Modifiés")} : ${refreshPlan.changed_count} · ${t("Absents conservés")} : ${refreshPlan.absent_count} — ${refreshPlan.selected_count} / ${refreshPlan.file_count} ${t("fichiers")} · ${bytes(refreshPlan.selected_size)}`
    : "";
}
$("refresh-manifest").onclick = async () => {
  const jobId = state.selected,
    sequence = ++refreshSequence;
  refreshPlan = null;
  refreshBusy = true;
  $("refresh-error").textContent = "";
  $("refresh-paused").checked = false;
  $("refresh-dialog").showModal();
  renderRefreshSummary();
  try {
    const result = await api(`/api/jobs/${jobId}/refresh`, {});
    if (sequence !== refreshSequence) return;
    refreshPlan = { ...result, job_id: jobId };
  } catch (error) {
    if (sequence === refreshSequence)
      $("refresh-error").textContent = error.message;
  } finally {
    if (sequence === refreshSequence) {
      refreshBusy = false;
      renderRefreshSummary();
    }
  }
};
$("refresh-dialog").addEventListener("close", () => {
  refreshSequence++;
});
$("refresh-select").onclick = () => {
  selectionPlan = refreshPlan;
  selectionPrefix = "";
  selectionOffset = 0;
  $("selection-dialog").showModal();
  loadSelection();
};
$("refresh-dialog").addEventListener("cancel", (event) => {
  if (refreshApplying) event.preventDefault();
});
$("refresh-apply").onclick = async () => {
  refreshApplying = true;
  refreshBusy = true;
  renderRefreshSummary();
  try {
    await api(`/api/jobs/${refreshPlan.job_id}/apply-refresh`, {
      plan_id: refreshPlan.plan_id,
      paused: $("refresh-paused").checked,
    });
    $("refresh-dialog").close();
    await refresh();
  } catch (error) {
    $("refresh-error").textContent = error.message;
  } finally {
    refreshApplying = false;
    refreshBusy = false;
    renderRefreshSummary();
  }
};
let repairTarget = null;
$("repair").onclick = () => {
  repairTarget = state.selected;
  $("repair-dialog").showModal();
};
$("repair-confirm").onclick = async () => {
  $("repair-confirm").disabled = true;
  try {
    await api(`/api/jobs/${repairTarget}/repair`, {});
    $("repair-dialog").close();
    await refresh();
  } catch (error) {
    toast(error.message);
  } finally {
    $("repair-confirm").disabled = false;
  }
};
let cancelTargets = [];
$("cancel").onclick = () => {
  cancelTargets = selectedJobs().map((job) => job.id);
  $("cancel-target-count").textContent = t("{count} tâches", {
    count: cancelTargets.length,
  });
  $("cancel-dialog").showModal();
};
$("cancel-confirm").onclick = async () => {
  try {
    if (cancelTargets.length === 1)
      await api(`/api/jobs/${cancelTargets[0]}/cancel`, {});
    else await bulkAction("cancel", cancelTargets);
    $("cancel-dialog").close();
    await refresh();
    if (cancelTargets.length === 1)
      toast(t("Téléchargement annulé. Les fichiers reçus sont conservés."));
  } catch (error) {
    $("cancel-dialog").close();
    toast(error.message);
  }
};
let removeTargets = [];
$("remove").onclick = () => {
  removeTargets = selectedJobs().map((job) => job.id);
  $("remove-target-count").textContent = t("{count} tâches", {
    count: removeTargets.length,
  });
  $("remove-dialog").showModal();
};
$("remove-confirm").onclick = async () => {
  try {
    if (removeTargets.length === 1)
      await api(`/api/jobs/${removeTargets[0]}/remove`, {});
    else await bulkAction("remove", removeTargets);
    $("remove-dialog").close();
    state.selected = null;
    await refresh();
    if (removeTargets.length === 1)
      toast(t("Tâche retirée. Les fichiers ont été conservés."));
  } catch (error) {
    $("remove-dialog").close();
    toast(error.message);
  }
};
$("add-form").onsubmit = async (event) => {
  event.preventDefault();
  if (state.inspection) return;
  resetPlans();
  $("add-error").textContent = "";
  const urls = [
    ...new Set(
      $("urls")
        .value.split(/\n/)
        .map((s) => s.trim())
        .filter(Boolean),
    ),
  ];
  if (!urls.length || urls.length > 20) {
    $("add-error").textContent = "Saisissez entre 1 et 20 URL, une par ligne.";
    return;
  }
  const options = {
    mode: $("file-mode").value,
    pattern: $("file-pattern").value.trim(),
  };
  const inspection = {
    controller: new AbortController(),
    controls: [
      ...$("add-form").querySelectorAll(
        "input, textarea, select, #browse-job, #inspect-button, #create-button",
      ),
    ].map((control) => [control, control.disabled]),
  };
  state.inspection = inspection;
  for (const [control] of inspection.controls) control.disabled = true;
  $("add-form").querySelector(".dialog-body").setAttribute("aria-busy", "true");
  $("inspect-status").hidden = false;
  const failures = [];
  try {
    for (let i = 0; i < urls.length; i++) {
      $("inspect-button").textContent = t("Analyse {current} / {total}…", {
        current: i + 1,
        total: urls.length,
      });
      $("inspect-progress").textContent = t(
        "Analyse de l’URL {current} sur {total}…",
        { current: i + 1, total: urls.length },
      );
      const plan = {
        url: urls[i],
        ...options,
      };
      try {
        const info = await api(
          "/api/inspect",
          plan,
          inspection.controller.signal,
        );
        if (state.inspection !== inspection) return;
        if (state.plans.some((p) => p.identifier === info.identifier)) continue;
        state.plans.push({ ...plan, ...info });
        renderPreviews();
      } catch (error) {
        if (state.inspection !== inspection) return;
        failures.push(`${urls[i]} : ${error.message}`);
      }
    }
    $("add-error").textContent = failures.join("\n");
    $("create-button").hidden = !state.plans.length;
    $("create-button").textContent = t(
      state.plans.length === 1
        ? "Ajouter {count} tâche"
        : "Ajouter {count} tâches",
      { count: number(state.plans.length) },
    );
    $("inspect-button").hidden = !!state.plans.length;
  } finally {
    finishInspection(inspection);
  }
};
$("create-button").onclick = async () => {
  $("create-button").disabled = true;
  $("add-error").textContent = "";
  const failed = [],
    errors = [];
  for (const plan of state.plans) {
    try {
      const result = await api("/api/jobs", {
        url: plan.url,
        mode: plan.mode,
        pattern: plan.pattern,
        plan_id: plan.plan_id,
        destination: $("job-destination").value,
        paused: $("start-paused").checked,
      });
      state.selected = result.id;
    } catch (error) {
      failed.push(plan);
      errors.push(error.message);
    }
  }
  $("create-button").disabled = false;
  state.plans = failed;
  renderPreviews();
  if (errors.length) $("add-error").textContent = errors.join(" · ");
  else {
    $("add-dialog").close();
    $("urls").value = "";
    toast(t("Téléchargements ajoutés. Un dossier sera créé pour chaque URL."));
  }
  await refresh();
};
function renderPreviews() {
  updateCapacity();
  $("preview-list").innerHTML = state.plans
    .map(
      (plan) =>
        `<div class="preview-item"><strong>▰ ${esc(plan.identifier)}</strong><p>${number(plan.selected_count ?? plan.file_count)} / ${number(plan.file_count)} ${esc(t("fichiers"))} · ${bytes(plan.selected_size ?? plan.total_size)}${plan.unknown_sizes ? t(" + tailles inconnues") : ""}</p>${plan.private_files ? `<p>${number(plan.private_files)} ${esc(t("fichiers privés exclus"))}</p>` : ""}<p><code>↳ ${esc(plan.sample?.[0])}${plan.file_count > 1 ? "…" : ""}</code></p>${plan.plan_id ? `<button type="button" data-select-plan="${esc(plan.plan_id)}" ${state.inspection ? "disabled" : ""}>${esc(t("Choisir les fichiers…"))}</button>` : ""}</div>`,
    )
    .join("");
}
let capacitySequence = 0;
async function updateCapacity() {
  const sequence = ++capacitySequence;
  $("capacity-summary").hidden = !state.plans.length;
  if (!state.plans.length) return;
  const required = state.plans.reduce(
    (sum, plan) => sum + (plan.selected_size ?? plan.total_size ?? 0),
    0,
  );
  try {
    const space = await api(
      `/api/capacity?path=${encodeURIComponent($("job-destination").value)}&required=${required}`,
    );
    if (sequence !== capacitySequence) return;
    $("capacity-summary").textContent =
      `${t("Taille")} : ${bytes(required)} · ${t("Après réserve et file active")} : ${bytes(space.available)}`;
    $("capacity-summary").classList.toggle("error", !space.fits);
  } catch (error) {
    if (sequence === capacitySequence)
      $("capacity-summary").textContent = error.message;
  }
}
$("job-destination").addEventListener("input", updateCapacity);
let selectionPlan = null,
  selectionPrefix = "",
  selectionOffset = 0,
  selectionBusy = false;
async function loadSelection(change) {
  selectionBusy = true;
  $("selection-controls").disabled = true;
  $("selection-close").disabled = true;
  $("selection-error").textContent = "";
  try {
    const data = await api(
      `/api/plans/${selectionPlan.plan_id}?prefix=${encodeURIComponent(selectionPrefix)}&offset=${selectionOffset}`,
      change,
    );
    Object.assign(selectionPlan, {
      selected_count: data.selected_count,
      selected_size: data.selected_size,
      unknown_sizes: data.unknown_sizes,
    });
    renderRefreshSummary();
    selectionOffset = data.offset;
    $("selection-path").textContent =
      selectionPrefix || selectionPlan.identifier;
    $("selection-total").textContent =
      `${number(data.selected_count)} / ${number(data.file_count)} ${t("fichiers")} · ${bytes(data.selected_size)}${data.unknown_sizes ? " +" : ""}`;
    $("selection-up").disabled = !selectionPrefix;
    $("selection-prev").disabled = !selectionOffset;
    $("selection-next").disabled = selectionOffset + 100 >= data.total;
    $("selection-rows").innerHTML = data.children
      .map(
        (file) =>
          `<label class="selection-row"><input type="checkbox" data-select-path="${esc(file.path)}" ${file.selected_count === file.file_count ? "checked" : ""} data-partial="${file.selected_count > 0 && file.selected_count < file.file_count}" aria-label="${esc(t("Sélectionner {name}", { name: file.name }))}"><span>${file.kind === "folder" ? `<button type="button" data-selection-folder="${esc(file.path)}">▰ ${esc(file.name)}</button>` : esc(file.name)}</span><small>${file.change ? esc(t(file.change === "new" ? "Nouveau" : "Modifié")) + " · " : ""}${number(file.selected_count)} / ${number(file.file_count)} · ${bytes(file.size)}${file.unknown_sizes ? " +" : ""}</small></label>`,
      )
      .join("");
    for (const input of $("selection-rows").querySelectorAll("input"))
      input.indeterminate = input.dataset.partial === "true";
    renderPreviews();
  } catch (error) {
    $("selection-error").textContent = error.message;
  } finally {
    selectionBusy = false;
    $("selection-controls").disabled = false;
    $("selection-close").disabled = false;
  }
}
$("preview-list").onclick = (event) => {
  const button = event.target.closest("[data-select-plan]");
  if (!button || state.inspection) return;
  selectionPlan = state.plans.find(
    (plan) => plan.plan_id === button.dataset.selectPlan,
  );
  selectionPrefix = "";
  selectionOffset = 0;
  $("selection-dialog").showModal();
  loadSelection();
};
$("selection-dialog").addEventListener("cancel", (event) => {
  if (selectionBusy) event.preventDefault();
});
$("selection-rows").onchange = (event) => {
  if (event.target.matches("[data-select-path]"))
    loadSelection({
      target: event.target.dataset.selectPath,
      selected: event.target.checked,
    });
};
$("selection-rows").onclick = (event) => {
  const button = event.target.closest("[data-selection-folder]");
  if (button) {
    event.preventDefault();
    selectionPrefix = button.dataset.selectionFolder;
    selectionOffset = 0;
    loadSelection();
  }
};
$("selection-up").onclick = () => {
  selectionPrefix = selectionPrefix.replace(/[^/]+\/$/, "");
  selectionOffset = 0;
  loadSelection();
};
$("selection-prev").onclick = () => {
  selectionOffset -= 100;
  loadSelection();
};
$("selection-next").onclick = () => {
  selectionOffset += 100;
  loadSelection();
};
$("selection-all").onclick = () => loadSelection({ selected: true });
$("selection-none").onclick = () => loadSelection({ selected: false });
$("selection-include").onclick = () =>
  loadSelection({
    pattern: $("selection-pattern").value.trim(),
    selected: true,
  });
$("selection-exclude").onclick = () =>
  loadSelection({
    pattern: $("selection-pattern").value.trim(),
    selected: false,
  });
function setDestinationLocked(locked) {
  state.destinationLocked = locked;
  for (const id of [
    "setting-destination",
    "browse-settings",
    "destination-edit",
  ])
    $(id).disabled = locked;
  $("destination-lock-notice").hidden = !locked;
  $("destination-edit").title = locked
    ? t(
        "Mettre les téléchargements en pause et attendre leur arrêt pour modifier la destination par défaut.",
      )
    : "";
  if (locked) {
    if (state.settings)
      $("setting-destination").value = state.settings.download_dir;
    if (
      state.folderTarget === "setting-destination" &&
      $("folder-dialog").open
    ) {
      state.folderSequence = (state.folderSequence || 0) + 1;
      state.folder = null;
      $("folder-dialog").close();
    }
  }
}
function timeInput(minutes) {
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}
function timeMinutes(value) {
  const [h, m] = value.split(":").map(Number);
  return h * 60 + m;
}
$("schedule-enabled").onchange = () => {
  $("schedule-fields").disabled = !$("schedule-enabled").checked;
};
async function openSettings(focusDestination = false) {
  try {
    await loadSettings();
    const s = state.settings;
    $("setting-language").replaceChildren(
      ...[
        ["auto", t("Automatique — langue DSM"), "🌐"],
        ...[...ArchiveI18n.languages].sort((a, b) =>
          a[1].localeCompare(b[1], ArchiveI18n.locale, { sensitivity: "base" }),
        ),
      ].map(([code, name, flag]) => new Option(`${flag} ${name}`, code)),
    );
    $("setting-language").value = s.language || "auto";
    $("setting-destination").value = s.download_dir;
    $("setting-connections").value = s.connections;
    $("setting-speed").value = s.speed_limit_kib;
    $("setting-retries").value = s.retries;
    $("setting-verify").checked = s.verify_checksums;
    $("setting-notifications").checked = s.notifications !== false;
    $("setting-reserve").value = s.disk_reserve_mib ?? 1024;
    $("schedule-enabled").checked = s.schedule_enabled || false;
    $("schedule-fields").disabled = !$("schedule-enabled").checked;
    $("schedule-zone").textContent = s.timezone || "—";
    $("schedule-start").value = timeInput(s.schedule_start || 0);
    $("schedule-end").value = timeInput(s.schedule_end || 0);
    $("schedule-outside").value = s.schedule_outside || "pause";
    $("schedule-limit").value = s.schedule_limit_kib || 1024;
    $("schedule-days").innerHTML = Array.from(
      { length: 7 },
      (_, i) =>
        `<label class="checkbox"><input type="checkbox" value="${i}" ${(s.schedule_days || [0, 1, 2, 3, 4, 5, 6]).includes(i) ? "checked" : ""}/>${esc(new Intl.DateTimeFormat(ArchiveI18n.locale, { weekday: "short", timeZone: "UTC" }).format(new Date(Date.UTC(2026, 9, 5 + i))))}</label>`,
    ).join("");
    $("setting-password").value = "";
    $("settings-error").textContent = "";
    $("settings-dialog").showModal();
    if (focusDestination && !state.destinationLocked)
      $("setting-destination").focus();
  } catch (error) {
    toast(error.message);
  }
}
$("settings-open").onclick = () => openSettings();
$("destination-edit").onclick = () => openSettings(true);
$("settings-form").onsubmit = async (event) => {
  event.preventDefault();
  $("settings-error").textContent = "";
  try {
    await api("/api/settings", {
      ...(state.destinationLocked
        ? {}
        : { download_dir: $("setting-destination").value }),
      connections: Number($("setting-connections").value),
      speed_limit_kib: Number($("setting-speed").value),
      retries: Number($("setting-retries").value),
      verify_checksums: $("setting-verify").checked,
      notifications: $("setting-notifications").checked,
      disk_reserve_mib: Number($("setting-reserve").value),
      schedule_enabled: $("schedule-enabled").checked,
      schedule_days: [
        ...$("schedule-days").querySelectorAll("input:checked"),
      ].map((input) => Number(input.value)),
      schedule_start: timeMinutes($("schedule-start").value),
      schedule_end: timeMinutes($("schedule-end").value),
      schedule_outside: $("schedule-outside").value,
      schedule_limit_kib: Number($("schedule-limit").value),
      language: $("setting-language").value,
    });
    if (authMode === "password" && $("setting-password").value) {
      await api("/api/password", { password: $("setting-password").value });
      showLogin();
      toast(t("Mot de passe modifié. Reconnectez-vous."));
    } else {
      $("settings-dialog").close();
      await loadSettings();
      toast(t("Paramètres enregistrés."));
    }
  } catch (error) {
    $("settings-error").textContent = error.message;
    await loadSettings().catch(() => {});
  }
};
function folderEntry(folder) {
  const readable = folder.readable !== false;
  const writable = folder.writable !== false;
  const access = !readable ? "blocked" : writable ? "writable" : "readonly";
  const label = !readable
    ? writable
      ? t("Écriture seule · parcours impossible")
      : t("Aucun accès · à autoriser")
    : writable
      ? t("Lecture/écriture")
      : t("Lecture seule");
  return `<button class="folder-entry ${access}" data-folder="${esc(folder.path)}" ${!readable ? "disabled" : ""}><span>${!readable ? "🔒" : "▰"} &nbsp;${esc(folder.name)}</span><small>${label}</small></button>`;
}
async function browse(path = "") {
  const sequence = (state.folderSequence || 0) + 1;
  state.folderSequence = sequence;
  state.folder = null;
  $("folder-select").disabled = true;
  $("folder-new").disabled = true;
  $("folder-create-form").hidden = true;
  $("folder-up").disabled = true;
  $("folder-list").innerHTML = `<p>${esc(t("Chargement des dossiers…"))}</p>`;
  $("folder-error").textContent = "";
  try {
    const data = await api(`/api/folders?path=${encodeURIComponent(path)}`);
    if (sequence !== state.folderSequence) return;
    state.folder = data;
    $("folder-path").textContent = data.path || t("Volumes autorisés");
    $("folder-up").disabled = data.parent === null;
    $("folder-select").disabled = !data.path || data.writable === false;
    $("folder-new").disabled = !data.path || data.writable === false;
    $("folder-list").innerHTML = data.folders.length
      ? data.folders.map(folderEntry).join("")
      : `<p>${esc(t("Aucun sous-dossier."))}</p>`;
    if (data.path && data.writable === false) {
      $("folder-error").textContent = t(
        "Ce dossier n’est pas accessible en écriture. Choisissez un sous-dossier autorisé ou accordez les permissions dans DSM.",
      );
    }
  } catch (error) {
    if (sequence !== state.folderSequence) return;
    $("folder-list").innerHTML =
      `<p>${esc(t("Impossible d’ouvrir ce dossier."))}</p>`;
    $("folder-error").textContent = error.message;
  }
}
$("folder-new").onclick = () => {
  $("folder-create-form").hidden = false;
  $("folder-name").value = "";
  $("folder-error").textContent = "";
  $("folder-name").focus();
};
$("folder-create-cancel").onclick = () => {
  $("folder-create-form").hidden = true;
};
$("folder-create-form").onsubmit = async (event) => {
  event.preventDefault();
  if (
    !state.folder?.path ||
    state.folder.writable === false ||
    state.creatingFolder
  )
    return;
  state.creatingFolder = true;
  $("folder-create-submit").disabled = true;
  $("folder-error").textContent = "";
  try {
    const folder = await api("/api/folders", {
      parent: state.folder.path,
      name: $("folder-name").value,
    });
    await browse(folder.path);
    toast(
      t("Dossier créé. Cliquez sur « Choisir ce dossier » pour l’utiliser."),
    );
  } catch (error) {
    $("folder-error").textContent = error.message;
  } finally {
    state.creatingFolder = false;
    $("folder-create-submit").disabled = false;
  }
};
for (const [button, target] of [
  ["browse-job", "job-destination"],
  ["browse-settings", "setting-destination"],
])
  $(button).onclick = async () => {
    state.folderTarget = target;
    $("folder-dialog").showModal();
    await browse();
  };
$("folder-list").onclick = (event) => {
  const b = event.target.closest("[data-folder]");
  if (b) browse(b.dataset.folder);
};
$("folder-up").onclick = () => browse(state.folder?.parent || "");
$("folder-select").onclick = () => {
  if (state.folder?.path) {
    $(state.folderTarget).value = state.folder.path;
    if (state.folderTarget === "job-destination") updateCapacity();
  }
  $("folder-dialog").close();
};
$("login-form").onsubmit = async (event) => {
  event.preventDefault();
  $("login-error").textContent = "";
  try {
    await api("/api/login", { password: $("password").value });
    $("password").value = "";
    showApp();
    await loadSettings();
    await refresh();
  } catch (error) {
    $("login-error").textContent = error.message;
  }
};
$("logout").onclick = async () => {
  try {
    await api("/api/logout", {});
    showLogin();
  } catch (error) {
    toast(error.message);
  }
};
async function init() {
  try {
    await ArchiveI18n.apply();
    const auth = await api("/api/auth");
    authMode = auth.mode;
    $("logout").hidden = authMode !== "password";
    $("local-password-settings").hidden = authMode !== "password";
    if (auth.authenticated) {
      restoreWindowLayout();
      showApp();
      await loadSettings();
      await refresh();
    } else {
      showLogin();
    }
  } catch (error) {
    showLogin();
    $("login-error").textContent = error.message;
  }
}
init();
setInterval(refresh, 1500);
setInterval(() => {
  if (!$("application").hidden) loadSettings().catch(() => {});
}, 30000);
