// Ráfaga — descargas rápidas de modelos para ComfyUI (interfaz)
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const TAB_ID = "rafaga";

// ------------------------------------------------------------------ i18n ---
const STRINGS = {
  es: {
    tooltip: "Ráfaga: descargas rápidas de modelos",
    subtitle: "Descarga en varias conexiones a la vez y verifica el SHA-256.",
    missingBtn: "Bajar modelos faltantes del workflow",
    missingNone: "Este workflow no declara modelos para descargar.",
    missingAllThere: "Ya tenés todos los modelos de este workflow.",
    missingQueued: (n) => `${n} modelo(s) en cola`,
    urlSection: "Descargar desde una URL",
    urlPlaceholder: "https://huggingface.co/.../resolve/main/vae/modelo.safetensors",
    folderAuto: "Detectar carpeta desde la URL",
    filenamePlaceholder: "Nombre del archivo (opcional)",
    downloadBtn: "Descargar",
    jobsTitle: "Descargas",
    clearBtn: "Limpiar terminadas",
    noJobs: "Todavía no hay descargas.",
    cancel: "Cancelar",
    connections: (n) => `${n} conexiones`,
    status: {
      queued: "En cola", probing: "Consultando", downloading: "Descargando",
      verifying: "Verificando SHA-256", done: "Listo", exists: "Ya estaba",
      error: "Error", cancelled: "Cancelado",
    },
    verified: "SHA-256 OK",
    doneToast: (f) => `Listo: ${f}`,
    errorToast: (f) => `Falló: ${f}`,
    cmdMissing: "Ráfaga: bajar modelos faltantes",
    settingConnections: "Conexiones por archivo",
    settingConnectionsTip: "Cuántos tramos del mismo archivo se bajan en paralelo (1 a 16).",
    needUrl: "Pegá una URL que empiece con http:// o https://",
  },
  en: {
    tooltip: "Ráfaga: fast model downloads",
    subtitle: "Downloads over several connections at once and verifies SHA-256.",
    missingBtn: "Download this workflow's missing models",
    missingNone: "This workflow does not declare any downloadable models.",
    missingAllThere: "You already have every model this workflow needs.",
    missingQueued: (n) => `${n} model(s) queued`,
    urlSection: "Download from a URL",
    urlPlaceholder: "https://huggingface.co/.../resolve/main/vae/model.safetensors",
    folderAuto: "Detect folder from the URL",
    filenamePlaceholder: "File name (optional)",
    downloadBtn: "Download",
    jobsTitle: "Downloads",
    clearBtn: "Clear finished",
    noJobs: "No downloads yet.",
    cancel: "Cancel",
    connections: (n) => `${n} connections`,
    status: {
      queued: "Queued", probing: "Checking", downloading: "Downloading",
      verifying: "Verifying SHA-256", done: "Done", exists: "Already there",
      error: "Error", cancelled: "Cancelled",
    },
    verified: "SHA-256 OK",
    doneToast: (f) => `Done: ${f}`,
    errorToast: (f) => `Failed: ${f}`,
    cmdMissing: "Ráfaga: download missing models",
    settingConnections: "Connections per file",
    settingConnectionsTip: "How many parts of the same file download in parallel (1 to 16).",
    needUrl: "Paste a URL that starts with http:// or https://",
  },
};

function locale() {
  let loc = "";
  try { loc = app.extensionManager?.setting?.get?.("Comfy.Locale") || ""; } catch (_) { /* older frontend */ }
  loc = (loc || navigator.language || "en").toLowerCase();
  return loc.startsWith("es") ? "es" : "en";
}
const t = () => STRINGS[locale()];

// ---------------------------------------------------------------- helpers ---
function fmtBytes(n) {
  if (n == null) return "?";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i >= 3 ? 2 : i ? 1 : 0)} ${u[i]}`;
}
function fmtEta(job) {
  if (!job.speed || !job.total) return "";
  const s = Math.max(0, (job.total - job.done) / job.speed);
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
  return `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`;
}
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null) node.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c != null) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return node;
}
function toast(severity, detail) {
  try { app.extensionManager.toast.add({ severity, summary: "Ráfaga", detail, life: 5000 }); }
  catch (_) { console.log(`[Ráfaga] ${detail}`); }
}
function connectionsSetting() {
  try { return Number(app.extensionManager.setting.get("Rafaga.Connections")) || 8; } catch (_) { return 8; }
}
async function apiJson(path, options = {}) {
  const res = await api.fetchApi(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

// ----------------------------------------------------------------- state ---
const state = { jobs: new Map(), folders: [], root: null, lastStatus: new Map() };

async function loadFolders() {
  try { state.folders = (await apiJson("/rafaga/folders")).folders || []; } catch (_) { state.folders = []; }
}
async function loadJobs() {
  try {
    for (const job of (await apiJson("/rafaga/jobs")).jobs || []) {
      state.jobs.set(job.id, job);
      state.lastStatus.set(job.id, job.status);
    }
  } catch (_) { /* server without the extension loaded */ }
}

function onJob(job) {
  const prev = state.lastStatus.get(job.id);
  state.jobs.set(job.id, job);
  state.lastStatus.set(job.id, job.status);
  if (prev !== job.status) {
    if (job.status === "done") {
      toast("success", t().doneToast(job.filename) + (job.verified ? ` · ${t().verified}` : ""));
      refreshModelLists();
    } else if (job.status === "error") {
      toast("error", `${t().errorToast(job.filename)} — ${job.error || ""}`);
    }
  }
  renderJobs();
}

let refreshTimer = null;
function refreshModelLists() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(async () => {
    try { await app.extensionManager.command.execute("Comfy.RefreshNodeDefinitions"); }
    catch (_) { try { await app.refreshComboInNodes(); } catch (_) { /* ignore */ } }
  }, 1500);
}

// ------------------------------------------------------- missing models ---
function toResolveUrl(url) {
  // Hugging Face "blob" links show an HTML page; "resolve" links serve the file.
  return url.replace(/^(https:\/\/huggingface\.co\/[^?#]+?)\/blob\//, "$1/resolve/");
}

async function collectWorkflowModels() {
  let wf;
  try { wf = (await app.graphToPrompt()).workflow; } catch (_) { wf = app.graph.serialize(); }
  const found = new Map();
  const add = (m) => {
    if (!m || !m.name || !m.url || !m.directory) return;
    const key = `${m.directory}/${m.name}`;
    if (!found.has(key)) found.set(key, m);
  };
  const visit = (nodes) => { for (const n of nodes || []) for (const m of n?.properties?.models || []) add(m); };
  visit(wf?.nodes);
  for (const sg of wf?.definitions?.subgraphs || []) visit(sg.nodes);
  for (const m of wf?.models || []) add(m);
  return [...found.values()];
}

async function downloadMissing() {
  const models = await collectWorkflowModels();
  if (!models.length) return toast("info", t().missingNone);
  const { results } = await apiJson("/rafaga/check", {
    method: "POST",
    body: JSON.stringify({ models: models.map(({ name, directory }) => ({ name, directory })) }),
  });
  const missing = models.filter((m, i) => !results[i]?.exists);
  if (!missing.length) return toast("success", t().missingAllThere);
  let queued = 0;
  for (const m of missing) {
    const sha = (m.hash_type || m.hashType || "").toLowerCase() === "sha256" ? m.hash : undefined;
    try {
      onJob(await apiJson("/rafaga/jobs", {
        method: "POST",
        body: JSON.stringify({
          url: toResolveUrl(m.url), folder: m.directory, filename: m.name,
          connections: connectionsSetting(), sha256: sha,
        }),
      }));
      queued++;
    } catch (e) {
      toast("error", `${m.name}: ${e.message}`);
    }
  }
  if (queued) toast("info", t().missingQueued(queued));
  openPanel();
}

function openPanel() {
  try {
    const tabs = app.extensionManager.sidebarTab;
    if (tabs?.activeSidebarTabId !== TAB_ID) {
      app.extensionManager.command.execute(`Workspace.ToggleSidebarTab.${TAB_ID}`);
    }
  } catch (_) { /* ignore */ }
}

// -------------------------------------------------------------------- UI ---
const CSS = `
.rafaga { padding: 12px; display: flex; flex-direction: column; gap: 12px; font-size: 13px; color: var(--fg-color, #ddd); }
.rafaga h2 { margin: 0; font-size: 16px; display: flex; align-items: center; gap: 6px; }
.rafaga .sub { margin: 0; color: var(--descrip-text, #999); font-size: 12px; }
.rafaga button { cursor: pointer; border-radius: 6px; border: 1px solid var(--border-color, #444);
  background: var(--comfy-input-bg, #222); color: var(--input-text, #ddd); padding: 6px 10px; font: inherit; }
.rafaga button:hover { filter: brightness(1.2); }
.rafaga button.primary { background: var(--p-primary-color, #3b82f6); border-color: transparent; color: #fff; padding: 8px 10px; }
.rafaga input, .rafaga select { width: 100%; box-sizing: border-box; border-radius: 6px; padding: 6px 8px; font: inherit;
  border: 1px solid var(--border-color, #444); background: var(--comfy-input-bg, #222); color: var(--input-text, #ddd); }
.rafaga details { border: 1px solid var(--border-color, #444); border-radius: 8px; padding: 8px; }
.rafaga summary { cursor: pointer; font-weight: 600; }
.rafaga .form { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
.rafaga .row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.rafaga .jobs { display: flex; flex-direction: column; gap: 8px; }
.rafaga .job { border: 1px solid var(--border-color, #444); border-radius: 8px; padding: 8px; display: flex; flex-direction: column; gap: 4px; }
.rafaga .name { font-weight: 600; word-break: break-all; }
.rafaga .meta { color: var(--descrip-text, #999); font-size: 11px; }
.rafaga .bar { height: 6px; border-radius: 3px; background: var(--border-color, #444); overflow: hidden; }
.rafaga .bar > div { height: 100%; background: var(--p-primary-color, #3b82f6); transition: width .4s; }
.rafaga .badge { font-size: 11px; padding: 1px 6px; border-radius: 10px; border: 1px solid var(--border-color, #444); white-space: nowrap; }
.rafaga .badge.done, .rafaga .badge.exists, .rafaga .badge.ok { color: #4ade80; border-color: #4ade8066; }
.rafaga .badge.error { color: #f87171; border-color: #f8717166; }
.rafaga .err { color: #f87171; font-size: 12px; }
.rafaga .empty { color: var(--descrip-text, #999); font-size: 12px; }
`;

function renderJobs() {
  const list = state.root?.querySelector(".jobs");
  if (!list) return;
  const s = t();
  const jobs = [...state.jobs.values()].sort((a, b) => b.created - a.created);
  list.replaceChildren();
  if (!jobs.length) { list.append(el("div", { class: "empty" }, s.noJobs)); return; }
  for (const job of jobs) {
    const pct = job.total ? Math.min(100, (job.done / job.total) * 100) : 0;
    const active = ["queued", "probing", "downloading", "verifying"].includes(job.status);
    const meta = [`${job.folder}`, `${fmtBytes(job.done)} / ${fmtBytes(job.total)}`];
    if (job.status === "downloading") {
      meta.push(`${fmtBytes(job.speed)}/s`, s.connections(job.active_connections || job.connections));
      const eta = fmtEta(job);
      if (eta) meta.push(eta);
    }
    list.append(el("div", { class: "job" },
      el("div", { class: "row" },
        el("span", { class: "name", title: job.dest }, job.filename),
        el("span", { class: `badge ${job.status}` }, s.status[job.status] || job.status)),
      el("div", { class: "bar" }, el("div", { style: `width:${job.status === "done" || job.status === "exists" ? 100 : pct}%` })),
      el("div", { class: "row" },
        el("span", { class: "meta" }, meta.join(" · ")),
        job.verified ? el("span", { class: "badge ok" }, s.verified) : null,
        active ? el("button", { onclick: () => apiJson(`/rafaga/jobs/${job.id}/cancel`, { method: "POST" }).then(onJob).catch(() => {}) }, s.cancel) : null),
      job.error ? el("div", { class: "err" }, job.error) : null,
    ));
  }
}

function mount(root) {
  const s = t();
  state.root = root;
  root.replaceChildren();
  root.append(el("style", {}, CSS));

  const url = el("input", { type: "url", placeholder: s.urlPlaceholder });
  const folder = el("select", {}, el("option", { value: "" }, s.folderAuto),
    ...state.folders.map((f) => el("option", { value: f.name, title: f.path }, f.name)));
  const filename = el("input", { type: "text", placeholder: s.filenamePlaceholder });

  const startUrlDownload = async () => {
    const value = url.value.trim();
    if (!/^https?:\/\//i.test(value)) return toast("warn", s.needUrl);
    try {
      onJob(await apiJson("/rafaga/jobs", {
        method: "POST",
        body: JSON.stringify({
          url: toResolveUrl(value), folder: folder.value || undefined,
          filename: filename.value.trim() || undefined, connections: connectionsSetting(),
        }),
      }));
      url.value = ""; filename.value = "";
    } catch (e) { toast("error", e.message); }
  };

  root.append(el("div", { class: "rafaga" },
    el("h2", {}, el("i", { class: "pi pi-bolt" }), "Ráfaga"),
    el("p", { class: "sub" }, s.subtitle),
    el("button", { class: "primary", onclick: () => downloadMissing().catch((e) => toast("error", e.message)) },
      el("i", { class: "pi pi-bolt" }), " ", s.missingBtn),
    el("details", {},
      el("summary", {}, s.urlSection),
      el("div", { class: "form" }, url, folder, filename,
        el("button", { onclick: startUrlDownload }, el("i", { class: "pi pi-download" }), " ", s.downloadBtn))),
    el("div", { class: "row" },
      el("strong", {}, s.jobsTitle),
      el("button", { onclick: () => apiJson("/rafaga/jobs/clear", { method: "POST" }).then(() => {
        for (const [id, j] of state.jobs) if (!["queued", "probing", "downloading", "verifying"].includes(j.status)) state.jobs.delete(id);
        renderJobs();
      }).catch(() => {}) }, s.clearBtn)),
    el("div", { class: "jobs" }),
  ));
  renderJobs();
}

// -------------------------------------------------------------- register ---
app.registerExtension({
  name: "rafaga.downloader",
  settings: [{
    id: "Rafaga.Connections",
    category: ["Ráfaga", "Descargas", "Conexiones"],
    name: STRINGS[locale()].settingConnections,
    tooltip: STRINGS[locale()].settingConnectionsTip,
    type: "slider",
    attrs: { min: 1, max: 16, step: 1 },
    defaultValue: 8,
  }],
  commands: [{
    id: "Rafaga.DownloadMissing",
    label: STRINGS[locale()].cmdMissing,
    icon: "pi pi-bolt",
    function: () => downloadMissing().catch((e) => toast("error", e.message)),
  }],
  menuCommands: [{ path: ["Extensions", "Ráfaga"], commands: ["Rafaga.DownloadMissing"] }],
  async setup() {
    await Promise.all([loadFolders(), loadJobs()]);
    api.addEventListener("rafaga.progress", (e) => onJob(e.detail));
    const tab = {
      id: TAB_ID,
      icon: "pi pi-bolt",
      title: "Ráfaga",
      tooltip: t().tooltip,
      type: "custom",
      render: (container) => mount(container),
    };
    const registry = app.extensionManager.sidebarTab;
    if (registry?.registerSidebarTab) registry.registerSidebarTab(tab);
    else app.extensionManager.registerSidebarTab(tab);
  },
});
