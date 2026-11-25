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
    urlPlaceholder: "Link a un archivo o a un repo de Hugging Face",
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
    exploring: "Leyendo el repo…",
    pickHint: "Un repo suele traer variantes del mismo modelo (bf16, fp8, ema…). Elegí solo lo que usa tu workflow: no hace falta bajar todo.",
    diffusersWarn: "Este repo está en formato diffusers (para la librería diffusers). ComfyUI suele usar archivos únicos: buscá una versión del modelo para ComfyUI (por ejemplo, de Comfy-Org).",
    noModelFiles: "No hay archivos de modelo en este repo o carpeta.",
    noFolder: "Carpeta no detectada",
    folderHint: "¿Dónde va? checkpoints: modelo completo · diffusion_models: solo el modelo de difusión · text_encoders: text encoder · vae: VAE · loras: LoRA.",
    chooseFolder: "Elegí la carpeta…",
    selectGroup: "Seleccionar todos los de esta carpeta",
    installed: "Ya está",
    selectedSummary: (n, size) => `${n} seleccionado(s) · ${size}`,
    downloadSelected: "Bajar seleccionados",
    needFolder: (names) => `Elegí la carpeta para: ${names}`,
    sameName: (f) => `Dos archivos se llamarían igual (${f}) en la misma carpeta: elegí uno.`,
    skippedNote: (n) => `Se ocultaron ${n} archivo(s) que no son modelos.`,
    truncatedNote: "El repo es muy grande y se muestra solo una parte. Pegá el link de una carpeta (…/tree/main/carpeta) para ver el resto.",
    close: "Cerrar",
  },
  en: {
    tooltip: "Ráfaga: fast model downloads",
    subtitle: "Downloads over several connections at once and verifies SHA-256.",
    missingBtn: "Download this workflow's missing models",
    missingNone: "This workflow does not declare any downloadable models.",
    missingAllThere: "You already have every model this workflow needs.",
    missingQueued: (n) => `${n} model(s) queued`,
    urlSection: "Download from a URL",
    urlPlaceholder: "Link to a file or to a Hugging Face repo",
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
    exploring: "Reading the repo…",
    pickHint: "A repo usually holds variants of the same model (bf16, fp8, ema…). Pick only what your workflow uses: you don't need everything.",
    diffusersWarn: "This repo uses the diffusers format (for the diffusers library). ComfyUI usually loads single files: look for a ComfyUI version of the model (for example, from Comfy-Org).",
    noModelFiles: "There are no model files in this repo or folder.",
    noFolder: "Folder not detected",
    folderHint: "Where does it go? checkpoints: full model · diffusion_models: diffusion model only · text_encoders: text encoder · vae: VAE · loras: LoRA.",
    chooseFolder: "Choose a folder…",
    selectGroup: "Select every file in this folder",
    installed: "Installed",
    selectedSummary: (n, size) => `${n} selected · ${size}`,
    downloadSelected: "Download selected",
    needFolder: (names) => `Choose the folder for: ${names}`,
    sameName: (f) => `Two files would get the same name (${f}) in the same folder: pick one.`,
    skippedNote: (n) => `${n} non-model file(s) hidden.`,
    truncatedNote: "This repo is very large, so only part of it is shown. Paste a folder link (…/tree/main/folder) to see the rest.",
    close: "Close",
  },
};

// Server errors arrive with a code (and params); the Spanish text the server sends is the fallback.
const ERRORS = {
  es: {
    bad_json: () => "Pedido inválido",
    bad_url: () => "La URL tiene que empezar con http:// o https://",
    need_folder: () => "Elegí la carpeta de destino en la lista (no se pudo deducir de la URL)",
    unknown_folder: (p) => `Carpeta de modelos desconocida: ${p.folder}`,
    bad_filename: (p) => `Nombre de archivo inválido: ${p.filename}`,
    bad_extension: (p) => `Extensión no permitida para modelos: ${p.ext || "(sin extensión)"}`,
    outside_folder: () => "La ruta de destino sale de la carpeta de modelos",
    not_repo: () => "No es un link a un repo de Hugging Face (huggingface.co/<dueño>/<repo>)",
    hf_denied: (p) => `Hugging Face no muestra ese repo (HTTP ${p.status}): no existe, es privado o pide aceptar condiciones. Si tenés acceso, definí HF_TOKEN antes de abrir ComfyUI.`,
    hf_not_found: () => "No encontré esa rama o carpeta en el repo (HTTP 404)",
    hf_file_not_found: () => "No encontré ese archivo en el repo",
    hf_http: (p) => `Hugging Face respondió HTTP ${p.status}`,
    hf_unexpected: () => "Hugging Face respondió algo inesperado",
    hf_connect: (p) => `No se pudo consultar Hugging Face: ${p.detail}`,
    job_not_found: () => "No existe esa descarga",
    access_denied: (p) => `Acceso denegado (HTTP ${p.status}). ¿El modelo requiere token?`,
    not_found: () => "No encontrado (HTTP 404)",
    connect_failed: (p) => `No se pudo conectar: ${p.detail}`,
    wrong_size: (p) => `Tamaño incorrecto: ${p.actual} de ${p.size} bytes`,
    sha_mismatch: () => "El SHA-256 no coincide: el archivo está corrupto y se descartó",
    no_ranges: (p) => `El servidor respondió HTTP ${p.status} a un pedido por tramos`,
    segment_failed: (p) => `Un tramo falló ${p.count} veces seguidas: ${p.detail}`,
    http_status: (p) => `El servidor respondió HTTP ${p.status}`,
    cancelled: () => "Cancelado",
    unexpected: (p) => `Error inesperado: ${p.detail}`,
  },
  en: {
    bad_json: () => "Invalid request",
    bad_url: () => "The URL must start with http:// or https://",
    need_folder: () => "Choose the destination folder in the list (it couldn't be deduced from the URL)",
    unknown_folder: (p) => `Unknown model folder: ${p.folder}`,
    bad_filename: (p) => `Invalid file name: ${p.filename}`,
    bad_extension: (p) => `File type not allowed for models: ${p.ext || "(no extension)"}`,
    outside_folder: () => "The destination path leaves the models folder",
    not_repo: () => "Not a Hugging Face repo link (huggingface.co/<owner>/<repo>)",
    hf_denied: (p) => `Hugging Face won't show that repo (HTTP ${p.status}): it doesn't exist, it's private or it asks you to accept its terms. If you have access, set HF_TOKEN before opening ComfyUI.`,
    hf_not_found: () => "Couldn't find that branch or folder in the repo (HTTP 404)",
    hf_file_not_found: () => "Couldn't find that file in the repo",
    hf_http: (p) => `Hugging Face answered HTTP ${p.status}`,
    hf_unexpected: () => "Hugging Face sent an unexpected answer",
    hf_connect: (p) => `Couldn't reach Hugging Face: ${p.detail}`,
    job_not_found: () => "That download doesn't exist",
    access_denied: (p) => `Access denied (HTTP ${p.status}). Does the model need a token?`,
    not_found: () => "Not found (HTTP 404)",
    connect_failed: (p) => `Couldn't connect: ${p.detail}`,
    wrong_size: (p) => `Wrong size: ${p.actual} of ${p.size} bytes`,
    sha_mismatch: () => "SHA-256 doesn't match: the file is corrupt and was discarded",
    no_ranges: (p) => `The server answered HTTP ${p.status} to a ranged request`,
    segment_failed: (p) => `A part failed ${p.count} times in a row: ${p.detail}`,
    http_status: (p) => `The server answered HTTP ${p.status}`,
    cancelled: () => "Cancelled",
    unexpected: (p) => `Unexpected error: ${p.detail}`,
  },
};

function errorText(code, params, fallback) {
  const fn = ERRORS[locale()][code];
  try { return fn ? fn(params || {}) : fallback; } catch (_) { return fallback; }
}

function locale() {
  let loc = "";
  try { loc = app.extensionManager?.setting?.get?.("Comfy.Locale") || ""; } catch (_) { /* older frontend */ }
  loc = (loc || navigator.language || "en").toLowerCase();
  return loc.startsWith("es") ? "es" : "en";
}
const t = () => STRINGS[locale()];

// ---------------------------------------------------------------- helpers ---
function fmtBytes(n) {
  // Decimal units (1 GB = 10^9 bytes), the same ones Hugging Face shows.
  if (n == null) return "?";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1000 && i < u.length - 1) { n /= 1000; i++; }
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
  if (!res.ok) {
    const err = new Error(errorText(data.code, data.params, data.error || `HTTP ${res.status}`));
    err.code = data.code;
    throw err;
  }
  return data;
}

// ----------------------------------------------------------------- state ---
const state = { jobs: new Map(), folders: [], root: null, lastStatus: new Map(), explorer: null, urlInput: null };

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

function jobError(job) {
  return errorText(job.error_code, job.error_params, job.error || "");
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
      toast("error", `${t().errorToast(job.filename)} — ${jobError(job)}`);
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

// ------------------------------------------------- Hugging Face explorer ---
// A link to a whole repo (or to a folder of it) lists its model files so you can
// pick which ones to download instead of pasting one link per file.
function isHfRepoUrl(value) {
  try {
    const u = new URL(value);
    if (!/^(www\.)?(huggingface\.co|hf\.co)$/i.test(u.hostname)) return false;
    const parts = u.pathname.split("/").filter(Boolean);
    return parts.length === 2 || (parts.length >= 4 && parts[2] === "tree");
  } catch (_) { return false; }
}

function isHfFileUrl(value) {
  try {
    const u = new URL(value);
    if (!/^(www\.)?(huggingface\.co|hf\.co)$/i.test(u.hostname)) return false;
    const parts = u.pathname.split("/").filter(Boolean);
    return parts.length >= 5 && (parts[2] === "blob" || parts[2] === "resolve");
  } catch (_) { return false; }
}

async function openExplorer(value, defaultFolder) {
  state.explorer = { loading: true };
  renderExplorer();
  try {
    const listing = await apiJson(`/rafaga/hf/files?url=${encodeURIComponent(value)}`);
    const groups = new Map();
    for (const f of listing.files) {
      const key = f.folder || "";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(f);
    }
    // Detected folders first (alphabetical), files without a folder last.
    const keys = [...groups.keys()].sort((a, b) => (!a) - (!b) || a.localeCompare(b));
    state.explorer = {
      listing,
      groups: new Map(keys.map((k) => [k, groups.get(k)])),
      folders: new Map(keys.map((k) => [k, k || defaultFolder || ""])),
      // A link to one file comes with that file already ticked (unless it is installed).
      selected: new Set(listing.file ? listing.files.filter((f) => !f.exists).map((f) => f.path) : []),
    };
  } catch (e) {
    state.explorer = null;
    toast("error", e.message);
  }
  renderExplorer();
}

function closeExplorer() {
  state.explorer = null;
  renderExplorer();
}

async function queueSelected() {
  const s = t();
  const ex = state.explorer;
  if (!ex?.listing) return;
  const folderOf = (f) => ex.folders.get(f.folder || "");
  const chosen = ex.listing.files.filter((f) => ex.selected.has(f.path));
  const noFolder = chosen.filter((f) => !folderOf(f));
  if (noFolder.length) return toast("warn", s.needFolder(noFolder.map((f) => f.filename).join(", ")));
  const names = new Set();
  for (const f of chosen) {
    const key = `${folderOf(f)}/${f.filename}`.toLowerCase();
    if (names.has(key)) return toast("warn", s.sameName(f.filename));
    names.add(key);
  }
  let queued = 0;
  for (const f of chosen) {
    try {
      onJob(await apiJson("/rafaga/jobs", {
        method: "POST",
        body: JSON.stringify({
          url: f.url, folder: folderOf(f), filename: f.filename,
          sha256: f.sha256 || undefined, connections: connectionsSetting(),
        }),
      }));
      queued++;
    } catch (e) {
      toast("error", `${f.filename}: ${e.message}`);
    }
  }
  if (queued) {
    toast("info", t().missingQueued(queued));
    if (state.urlInput) state.urlInput.value = "";
    closeExplorer();
  }
}

function renderExplorer() {
  const box = state.root?.querySelector(".explorer");
  if (!box) return;
  const s = t();
  const ex = state.explorer;
  box.replaceChildren();
  if (!ex) return;
  if (ex.loading) { box.append(el("div", { class: "empty" }, s.exploring)); return; }

  const { listing } = ex;
  const title = listing.path ? `${listing.repo}/${listing.path}` : listing.repo;
  box.append(el("div", { class: "row" },
    el("strong", { class: "name" }, title),
    el("button", { title: s.close, onclick: closeExplorer }, el("i", { class: "pi pi-times" }))));
  if (listing.diffusers) box.append(el("div", { class: "warnbox" }, s.diffusersWarn));
  if (!listing.files.length) {
    box.append(el("div", { class: "empty" }, s.noModelFiles));
    return;
  }
  if (!listing.file) box.append(el("div", { class: "hint" }, s.pickHint));

  const folderOptions = (value) => [
    el("option", { value: "" }, s.chooseFolder),
    ...state.folders.map((f) => el("option", { value: f.name, title: f.path, selected: f.name === value ? "" : undefined }, f.name)),
  ];
  for (const [key, files] of ex.groups) {
    const selectable = files.filter((f) => !f.exists);
    const allOn = selectable.length > 0 && selectable.every((f) => ex.selected.has(f.path));
    const select = el("select", { onchange: (e) => { ex.folders.set(key, e.target.value); } },
      ...folderOptions(ex.folders.get(key)));
    select.value = ex.folders.get(key);
    const group = el("div", { class: "group" },
      el("label", { class: "group-head", title: s.selectGroup },
        el("input", {
          type: "checkbox", checked: allOn ? "" : undefined, disabled: selectable.length ? undefined : "",
          onchange: (e) => {
            for (const f of selectable) e.target.checked ? ex.selected.add(f.path) : ex.selected.delete(f.path);
            renderExplorer();
          },
        }),
        el("span", {}, key || s.noFolder)),
      select,
      key ? null : el("div", { class: "hint" }, s.folderHint));
    for (const f of files) {
      const dir = f.path.includes("/") ? f.path.slice(0, f.path.lastIndexOf("/")) : "";
      group.append(el("label", { class: `file${f.exists ? " exists" : ""}`, title: f.path },
        el("input", {
          type: "checkbox", checked: ex.selected.has(f.path) ? "" : undefined, disabled: f.exists ? "" : undefined,
          onchange: (e) => { e.target.checked ? ex.selected.add(f.path) : ex.selected.delete(f.path); renderExplorer(); },
        }),
        el("span", { class: "info" },
          el("span", { class: "fname" }, f.filename),
          el("span", { class: "sub" },
            el("span", { class: "meta" }, [dir, fmtBytes(f.size)].filter(Boolean).join(" · ")),
            f.exists ? el("span", { class: "badge exists", title: f.installed_in || "" },
              f.installed_in && f.installed_in !== f.folder ? `${s.installed} · ${f.installed_in}` : s.installed) : null))));
    }
    box.append(group);
  }

  const chosen = listing.files.filter((f) => ex.selected.has(f.path));
  const total = chosen.reduce((sum, f) => sum + (f.size || 0), 0);
  box.append(el("div", { class: "row" },
    el("span", { class: "meta" }, s.selectedSummary(chosen.length, fmtBytes(total))),
    el("button", { class: "primary", disabled: chosen.length ? undefined : "", onclick: queueSelected },
      el("i", { class: "pi pi-download" }), " ", s.downloadSelected)));
  if (listing.skipped) box.append(el("div", { class: "meta" }, s.skippedNote(listing.skipped)));
  if (listing.truncated) box.append(el("div", { class: "meta" }, s.truncatedNote));
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
.rafaga button:disabled { opacity: .5; cursor: default; filter: none; }
.rafaga input[type=checkbox] { width: auto; margin: 2px 0 0; flex: none; cursor: pointer; }
.rafaga .explorer { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
.rafaga .explorer:empty { display: none; }
.rafaga .hint { color: var(--descrip-text, #999); font-size: 12px; }
.rafaga .warnbox { border: 1px solid #f59e0b66; color: #fbbf24; border-radius: 6px; padding: 6px 8px; font-size: 12px; }
.rafaga .group { border: 1px solid var(--border-color, #444); border-radius: 8px; padding: 6px 8px; display: flex; flex-direction: column; gap: 6px; }
.rafaga .group-head { display: flex; align-items: center; gap: 6px; font-weight: 600; cursor: pointer; }
.rafaga .file { display: flex; align-items: flex-start; gap: 6px; cursor: pointer; }
.rafaga .file.exists { opacity: .6; cursor: default; }
.rafaga .file .info { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.rafaga .file .fname { word-break: break-all; }
.rafaga .file .sub { display: flex; flex-wrap: wrap; align-items: center; gap: 2px 6px; }
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
      job.error ? el("div", { class: "err" }, jobError(job)) : null,
    ));
  }
}

function mount(root) {
  const s = t();
  state.root = root;
  root.replaceChildren();
  root.append(el("style", {}, CSS));

  const url = el("input", { type: "url", placeholder: s.urlPlaceholder });
  state.urlInput = url;
  const folder = el("select", {}, el("option", { value: "" }, s.folderAuto),
    ...state.folders.map((f) => el("option", { value: f.name, title: f.path }, f.name)));
  const filename = el("input", { type: "text", placeholder: s.filenamePlaceholder });

  const startUrlDownload = async () => {
    const value = url.value.trim();
    if (!/^https?:\/\//i.test(value)) return toast("warn", s.needUrl);
    closeExplorer();  // a new link replaces whatever was listed before
    if (isHfRepoUrl(value)) return openExplorer(value, folder.value);
    try {
      onJob(await apiJson("/rafaga/jobs", {
        method: "POST",
        body: JSON.stringify({
          url: toResolveUrl(value), folder: folder.value || undefined,
          filename: filename.value.trim() || undefined, connections: connectionsSetting(),
        }),
      }));
      url.value = ""; filename.value = "";
    } catch (e) {
      if (e.code === "need_folder") {
        // A Hugging Face file: show it in the explorer (installed status + folder picker).
        if (isHfFileUrl(value)) return openExplorer(value, "");
        folder.focus();
        return toast("warn", e.message);
      }
      toast("error", e.message);
    }
  };

  root.append(el("div", { class: "rafaga" },
    el("h2", {}, el("i", { class: "pi pi-bolt" }), "Ráfaga"),
    el("p", { class: "sub" }, s.subtitle),
    el("button", { class: "primary", onclick: () => downloadMissing().catch((e) => toast("error", e.message)) },
      el("i", { class: "pi pi-bolt" }), " ", s.missingBtn),
    el("details", {},
      el("summary", {}, s.urlSection),
      el("div", { class: "form" }, url, folder, filename,
        el("button", { onclick: startUrlDownload }, el("i", { class: "pi pi-download" }), " ", s.downloadBtn)),
      el("div", { class: "explorer" })),
    el("div", { class: "row" },
      el("strong", {}, s.jobsTitle),
      el("button", { onclick: () => apiJson("/rafaga/jobs/clear", { method: "POST" }).then(() => {
        for (const [id, j] of state.jobs) if (!["queued", "probing", "downloading", "verifying"].includes(j.status)) state.jobs.delete(id);
        renderJobs();
      }).catch(() => {}) }, s.clearBtn)),
    el("div", { class: "jobs" }),
  ));
  renderJobs();
  renderExplorer();
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
