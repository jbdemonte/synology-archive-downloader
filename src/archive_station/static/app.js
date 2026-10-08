"use strict";
const { t } = ArchiveI18n;
const $ = (id) => document.getElementById(id);
const state = {
  jobs: [],
  selected: null,
  filter: "all",
  search: "",
  settings: null,
  expanded: new Map(),
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
  if (!job.speed || job.unknown_sizes || job.total_size <= job.downloaded)
    return "";
  const seconds = Math.ceil((job.total_size - job.downloaded) / job.speed);
  return seconds > 3600
    ? t("Environ {hours} h {minutes} min restantes", {
        hours: Math.floor(seconds / 3600),
        minutes: Math.ceil((seconds % 3600) / 60),
      })
    : t("Environ {minutes} min restantes", {
        minutes: Math.max(1, Math.ceil(seconds / 60)),
      });
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
function treeRows(job, prefix, depth) {
  const branch = state.expanded.get(key(job.id, prefix));
  if (!branch) return "";
  if (branch.error)
    return `<tr class="child-row"><td colspan="5" class="error">${esc(branch.error)}</td></tr>`;
  if (!branch.data)
    return `<tr class="child-row"><td colspan="5">${esc(t("Chargement des fichiers…"))}</td></tr>`;
  let html = "";
  for (const row of branch.data.children) {
    const folder = row.kind === "folder";
    const open = state.expanded.has(key(job.id, row.path));
    let status = row.status;
    if (
      ["paused", "cancelled"].includes(job.status) &&
      status !== "completed" &&
      status !== "error"
    )
      status = job.status;
    const icon = folder ? "▰" : "▤";
    const toggle = folder
      ? `<button class="toggle" data-expand="${esc(job.id)}" data-prefix="${esc(row.path)}" aria-label="${esc(t(open ? "Replier" : "Déplier"))} ${esc(row.name)}" aria-expanded="${open}">${open ? "⌄" : "›"}</button>`
      : '<span style="width:14px;flex-shrink:0"></span>';
    html += `<tr class="child-row" data-job="${esc(job.id)}"><td><div class="tree-name" style="padding-left:${depth * 19}px">${toggle}<span class="tree-icon ${folder ? "" : "item-icon"}">${icon}</span><div class="name-text"><span title="${esc(row.path)}">${esc(row.name)}</span>${folder ? `<small>${number(row.completed_files)} / ${number(row.file_count)} ${esc(t("fichiers"))}</small>` : ""}</div></div></td><td>${bytes(row.size)}</td><td>${progress({ ...row, status }, row.size)}</td><td class="speed">${row.speed && !["paused", "cancelled"].includes(job.status) ? bytes(row.speed) + "/s" : "—"}</td><td>${badge(status, row.error)}</td></tr>`;
    if (folder && open) html += treeRows(job, row.path, depth + 1);
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
      all: "Tous les téléchargements",
      active: "Téléchargements en cours",
      paused: "Téléchargements en pause",
      completed: "Téléchargements terminés",
      error: "Téléchargements à vérifier",
    }[state.filter],
  );
  const sum = (field) =>
    state.jobs.reduce((total, job) => total + (job[field] || 0), 0);
  $("stat-speed").textContent = bytes(sum("speed")) + "/s";
  $("stat-files").innerHTML =
    `${number(sum("completed_files"))} <em>/ ${number(sum("file_count"))}</em>`;
  $("stat-bytes").textContent = bytes(sum("downloaded"));
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
  $("empty").hidden = state.jobs.length > 0;
  $("no-results").hidden = !state.jobs.length || !!visible.length;
  $("list-count").textContent = t(
    visible.length === 1 ? "{count} tâche" : "{count} tâches",
    { count: number(visible.length) },
  );
  $("download-rows").innerHTML = visible
    .map((job) => {
      const open = state.expanded.has(key(job.id, ""));
      return `<tr class="job-row ${state.selected === job.id ? "selected" : ""}" data-job="${esc(job.id)}" tabindex="0" aria-selected="${state.selected === job.id}"><td><div class="tree-name"><button class="toggle" data-expand="${esc(job.id)}" data-prefix="" aria-label="${esc(t(open ? "Replier" : "Déplier"))} ${esc(job.identifier)}" aria-expanded="${open}">${open ? "⌄" : "›"}</button><span class="tree-icon item-icon">▣</span><div class="name-text"><strong title="${esc(job.identifier)}">${esc(job.identifier)}</strong><small title="${esc(job.title)}">${number(job.completed_files)} / ${number(job.file_count)} ${esc(t("fichiers"))} · ${esc(job.title)}</small></div></div></td><td>${bytes(job.total_size)}${job.unknown_sizes ? " +" : ""}</td><td>${progress(job, job.total_size)}</td><td class="speed">${job.speed ? bytes(job.speed) + "/s" : "—"}</td><td>${badge(job.status)}</td></tr>${treeRows(job, "", 1)}`;
    })
    .join("");
  renderDetail();
}
function renderDetail() {
  const job = state.jobs.find((j) => j.id === state.selected);
  $("detail").hidden = !job;
  $("pause").disabled = !job || !["queued", "running"].includes(job.status);
  $("resume").disabled =
    !job || !["paused", "cancelled", "error"].includes(job.status);
  $("retry").disabled = !job || !job.failed_files;
  $("cancel").disabled =
    !job || ["completed", "cancelled"].includes(job.status);
  $("remove").disabled = !job || ["queued", "running"].includes(job.status);
  if (!job) return;
  $("detail-name").textContent = job.title;
  $("detail-eta").textContent = eta(job);
  $("detail-destination").textContent = `${job.destination}/${job.identifier}/`;
  $("detail-source").textContent = `archive.org/download/${job.identifier}`;
  $("detail-source").href =
    `https://archive.org/download/${encodeURIComponent(job.identifier)}`;
  $("detail-errors").textContent = job.failed_files
    ? t(
        "{count} fichier(s) en erreur. Dépliez les dossiers pour consulter leur état, puis utilisez Réessayer.",
        { count: number(job.failed_files) },
      )
    : "";
}
async function loadBranch(jobId, prefix, offset = 0) {
  const branch = { offset, data: state.expanded.get(key(jobId, prefix))?.data };
  state.expanded.set(key(jobId, prefix), branch);
  try {
    branch.data = await api(
      `/api/jobs/${jobId}/tree?prefix=${encodeURIComponent(prefix)}&offset=${offset}&limit=100`,
    );
  } catch (error) {
    branch.error = error.message;
  }
}
async function refresh() {
  if (state.polling || $("application").hidden) return;
  state.polling = true;
  try {
    state.jobs = (await api("/api/jobs")).jobs;
    for (const [id, branch] of [...state.expanded]) {
      const split = id.indexOf("|");
      const jobId = id.slice(0, split),
        prefix = id.slice(split + 1);
      if (!state.jobs.some((j) => j.id === jobId)) state.expanded.delete(id);
      else await loadBranch(jobId, prefix, branch.offset);
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
  $("preview-list").replaceChildren();
  $("create-button").hidden = true;
  $("inspect-button").hidden = false;
}
function finishInspection(inspection) {
  if (state.inspection !== inspection) return;
  for (const [control, disabled] of inspection.controls)
    control.disabled = disabled;
  state.inspection = null;
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
  $("view-title").textContent = {
    all: t("Tous les téléchargements"),
    active: t("Téléchargements en cours"),
    paused: t("Téléchargements en pause"),
    completed: t("Téléchargements terminés"),
    error: t("Téléchargements à vérifier"),
  }[state.filter];
  render();
};
$("search").oninput = () => {
  state.search = $("search").value;
  render();
};
$("download-rows").onclick = async (event) => {
  const expand = event.target.closest("[data-expand]"),
    page = event.target.closest("[data-page]"),
    row = event.target.closest("[data-job]");
  if (row) state.selected = row.dataset.job;
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
    if (!state.selected) return;
    try {
      await api(`/api/jobs/${state.selected}/${action}`, {});
      await refresh();
      toast(
        {
          pause: t("Mise en pause demandée. Les octets reçus sont conservés."),
          resume: t("Reprise du téléchargement."),
          retry: t("Les fichiers en erreur ont été remis en attente."),
        }[action],
      );
    } catch (error) {
      toast(error.message);
    }
  };
$("cancel").onclick = () => $("cancel-dialog").showModal();
$("cancel-confirm").onclick = async () => {
  try {
    await api(`/api/jobs/${state.selected}/cancel`, {});
    $("cancel-dialog").close();
    await refresh();
    toast(t("Téléchargement annulé. Les fichiers reçus sont conservés."));
  } catch (error) {
    $("cancel-dialog").close();
    toast(error.message);
  }
};
$("remove").onclick = () => $("remove-dialog").showModal();
$("remove-confirm").onclick = async () => {
  try {
    await api(`/api/jobs/${state.selected}/remove`, {});
    $("remove-dialog").close();
    state.selected = null;
    await refresh();
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
        $("preview-list").insertAdjacentHTML(
          "beforeend",
          `<div class="preview-item"><strong>▰ ${esc(info.identifier)}</strong><p>${number(info.file_count)} ${esc(t("fichiers"))} · ${bytes(info.total_size)}${info.unknown_sizes ? t(" + tailles inconnues") : ""}${info.private_files ? ` · ${info.private_files} ${esc(t("fichiers privés exclus"))}` : ""}</p><p><code>↳ ${esc(info.sample[0])}${info.file_count > 1 ? "…" : ""}</code></p></div>`,
        );
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
  if (errors.length) $("add-error").textContent = errors.join(" · ");
  else {
    $("add-dialog").close();
    $("urls").value = "";
    toast(t("Téléchargements ajoutés. Un dossier sera créé pour chaque URL."));
  }
  await refresh();
};
async function openSettings(focusDestination = false) {
  try {
    await loadSettings();
    const s = state.settings;
    $("setting-language").replaceChildren(
      ...[
        ["auto", t("Automatique — langue DSM"), "🌐"],
        ...ArchiveI18n.languages,
      ].map(([code, name, flag]) => new Option(`${flag} ${name}`, code)),
    );
    $("setting-language").value = s.language || "auto";
    $("setting-destination").value = s.download_dir;
    $("setting-connections").value = s.connections;
    $("setting-speed").value = s.speed_limit_kib;
    $("setting-retries").value = s.retries;
    $("setting-verify").checked = s.verify_checksums;
    $("setting-password").value = "";
    $("settings-error").textContent = "";
    $("settings-dialog").showModal();
    if (focusDestination) $("setting-destination").focus();
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
      download_dir: $("setting-destination").value,
      connections: Number($("setting-connections").value),
      speed_limit_kib: Number($("setting-speed").value),
      retries: Number($("setting-retries").value),
      verify_checksums: $("setting-verify").checked,
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
  if (state.folder?.path) $(state.folderTarget).value = state.folder.path;
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
