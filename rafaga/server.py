"""ComfyUI integration for Ráfaga: HTTP routes, job queue and model folders."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Optional

import aiohttp
from aiohttp import web

import folder_paths
from server import PromptServer

from .downloader import (
    DownloadCancelled,
    DownloadError,
    Progress,
    SegmentedDownload,
    filename_from_url,
    probe,
)

log = logging.getLogger("rafaga")

EVENT = "rafaga.progress"
DEFAULT_CONNECTIONS = 8
MAX_CONNECTIONS = 16
MAX_PARALLEL_FILES = 1
NON_MODEL_FOLDERS = {"configs", "custom_nodes", "datasets", "diffusers"}
EXTRA_EXTENSIONS = {".gguf", ".onnx"}
_BAD_NAME = re.compile(r'[<>:"|?*\x00-\x1f]')
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


# ---------------------------------------------------------------- folders ---

def model_folders() -> list[str]:
    names = []
    for name, (paths, _exts) in folder_paths.folder_names_and_paths.items():
        if name not in NON_MODEL_FOLDERS and paths:
            names.append(name)
    return sorted(names)


def default_dir(folder: str) -> str:
    """Directory where new files of this folder type are saved.

    ComfyUI Desktop registers the legacy aliases first (models/unet before
    models/diffusion_models, models/clip before models/text_encoders,
    models/t2i_adapter before models/controlnet), so prefer the directory
    named like the folder type and fall back to the first one.
    """
    paths = folder_paths.get_folder_paths(folder)
    for path in paths:
        if os.path.basename(os.path.normpath(path)).lower() == folder.lower():
            return path
    return paths[0]


def installed_path(folder: str, filename: str) -> Optional[str]:
    """Full path of `filename` if it already exists in any directory of the folder type."""
    try:
        return folder_paths.get_full_path(folder, filename)
    except Exception:
        return None


def allowed_extensions(folder: str) -> set[str]:
    allowed = set(folder_paths.supported_pt_extensions) | EXTRA_EXTENSIONS
    exts = folder_paths.folder_names_and_paths[folder][1]
    if isinstance(exts, (set, list, tuple)) and "folder" not in exts:
        allowed |= {e.lower() for e in exts if e}
    return allowed


def resolve_destination(folder: str, filename: str) -> str:
    """Absolute path for `filename` inside the folder's default directory.

    Rejects unknown folders, path traversal, odd characters and non-model extensions.
    """
    if folder not in model_folders():
        raise ValueError(f"Carpeta de modelos desconocida: {folder}")
    parts = [p for p in filename.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or _BAD_NAME.search(p) for p in parts):
        raise ValueError(f"Nombre de archivo inválido: {filename}")
    ext = os.path.splitext(parts[-1])[1].lower()
    if ext not in allowed_extensions(folder):
        raise ValueError(f"Extensión no permitida para modelos: {ext or '(sin extensión)'}")
    base = os.path.abspath(default_dir(folder))
    dest = os.path.abspath(os.path.join(base, *parts))
    if os.path.commonpath([dest, base]) != base:
        raise ValueError("La ruta de destino sale de la carpeta de modelos")
    return dest


def folder_from_segments(segments: list[str]) -> Optional[str]:
    """Closest path segment that names a model folder (innermost first)."""
    known = set(model_folders())
    for seg in reversed(segments):
        if seg in known:
            return seg
        if seg == "unet":
            return "diffusion_models"
        if seg == "clip":
            return "text_encoders"
    return None


def guess_folder(url: str) -> Optional[str]:
    """Hugging Face repos often mirror ComfyUI's layout: .../resolve/main/vae/x.safetensors"""
    return folder_from_segments(urllib.parse.urlparse(url).path.split("/")[:-1])


def auth_headers(url: str) -> dict:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if token and (host == "huggingface.co" or host.endswith(".huggingface.co")):
        return {"Authorization": f"Bearer {token}"}
    return {}


def refresh_model_lists(folder: str) -> None:
    """Make ComfyUI and its asset index notice a new model without a restart."""
    folder_paths.filename_list_cache.pop(folder, None)
    helper = getattr(folder_paths, "cache_helper", None)
    if helper is not None and hasattr(helper, "clear"):
        try:
            helper.clear()
        except Exception:
            pass
    asyncio.get_running_loop().create_task(_rescan_assets())


async def _rescan_assets() -> None:
    try:
        from app.assets.seeder import asset_seeder  # newer ComfyUI only
    except Exception:
        return
    for _ in range(30):  # a scan may already be running: retry for ~1 minute
        try:
            if asset_seeder.start(roots=("models",)):
                return
        except Exception as e:
            log.debug("Ráfaga: no se pudo re-escanear los assets: %s", e)
            return
        await asyncio.sleep(2)


# ----------------------------------------------------------- hugging face ---

HF_BASE = "https://huggingface.co"
HF_HOSTS = {"huggingface.co", "www.huggingface.co", "hf.co"}
HF_MAX_PAGES = 20         # the tree API pages at ~1000 entries
HF_MAX_FILES = 2000       # model files returned at most
_HF_NOT_REPOS = {"api", "blog", "chat", "collections", "datasets", "docs", "join", "learn", "login",
                 "models", "organizations", "papers", "posts", "pricing", "settings", "spaces", "tasks"}
_HF_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass
class HFRepo:
    repo: str          # owner/name
    revision: str      # branch, tag or commit
    path: str = ""     # folder inside the repo ("" = everything)


def parse_hf_repo_url(url: str) -> Optional[HFRepo]:
    """Recognise a link to a whole Hugging Face model repo or to one of its folders.

        https://huggingface.co/<owner>/<name>
        https://huggingface.co/<owner>/<name>/tree/<revision>/<folder>

    Links to a single file (/resolve/, /blob/) and anything else return None.
    """
    try:
        parsed = urllib.parse.urlparse(url.strip())
        host = (parsed.hostname or "").lower()
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or host not in HF_HOSTS:
        return None
    parts = [urllib.parse.unquote(p) for p in parsed.path.split("/") if p]
    if len(parts) < 2 or parts[0] in _HF_NOT_REPOS:
        return None
    if not (_HF_ID.match(parts[0]) and _HF_ID.match(parts[1])):
        return None
    repo = f"{parts[0]}/{parts[1]}"
    if len(parts) == 2:
        return HFRepo(repo, "main")
    if parts[2] != "tree" or len(parts) < 4 or not parts[3]:
        return None
    sub = parts[4:]
    if any(p in (".", "..") for p in sub):
        return None
    return HFRepo(repo, parts[3], "/".join(sub))


def hf_file_url(info: HFRepo, path: str) -> str:
    q = urllib.parse.quote
    return f"{HF_BASE}/{q(info.repo, safe='/')}/resolve/{q(info.revision, safe='')}/{q(path, safe='/')}"


def _hf_tree_url(info: HFRepo) -> str:
    q = urllib.parse.quote
    url = f"{HF_BASE}/api/models/{q(info.repo, safe='/')}/tree/{q(info.revision, safe='')}"
    if info.path:
        url += "/" + q(info.path, safe="/")
    return url + "?recursive=true"


async def list_hf_repo(session: aiohttp.ClientSession, info: HFRepo) -> dict:
    """Model files of a Hugging Face repo, with size, SHA-256 and a suggested folder."""
    url = _hf_tree_url(info)
    api_host = urllib.parse.urlparse(HF_BASE).netloc
    entries: list = []
    truncated = False
    for page_number in range(HF_MAX_PAGES):
        async with session.get(url, headers=auth_headers(url)) as r:
            if r.status in (401, 403):
                raise DownloadError(
                    f"Hugging Face no muestra ese repo (HTTP {r.status}): no existe, es privado o pide "
                    "aceptar condiciones. Si tenés acceso, definí HF_TOKEN antes de abrir ComfyUI.")
            if r.status == 404:
                raise DownloadError("No encontré esa rama o carpeta en el repo (HTTP 404)")
            if r.status >= 400:
                raise DownloadError(f"Hugging Face respondió HTTP {r.status}")
            page = await r.json(content_type=None)
            next_link = r.links.get("next")
            next_url = str(next_link.get("url")) if next_link else None
        if not isinstance(page, list):
            raise DownloadError("Hugging Face respondió algo inesperado")
        entries.extend(e for e in page if isinstance(e, dict))
        # Follow pagination only on the same host (never send HF_TOKEN elsewhere).
        if not next_url or urllib.parse.urlparse(next_url).netloc != api_host:
            break
        if page_number == HF_MAX_PAGES - 1:
            truncated = True
        url = next_url

    model_exts = set(folder_paths.supported_pt_extensions) | EXTRA_EXTENSIONS
    files, skipped, diffusers = [], 0, False
    for entry in entries:
        if entry.get("type") != "file":
            continue
        path = str(entry.get("path", ""))
        name = path.rsplit("/", 1)[-1]
        if name == "model_index.json":
            diffusers = True  # a repo for the diffusers library, not single-file models
        if os.path.splitext(name)[1].lower() not in model_exts or _BAD_NAME.search(name):
            skipped += 1
            continue
        lfs = entry.get("lfs") if isinstance(entry.get("lfs"), dict) else {}
        sha = str(lfs.get("oid", "")).lower()
        size = lfs.get("size", entry.get("size"))
        folder = folder_from_segments(path.split("/")[:-1])
        # Where it is already installed: in its folder, or anywhere if no folder was detected.
        candidates = [folder] if folder else model_folders()
        installed_in = next((c for c in candidates if installed_path(c, name)), None)
        files.append({
            "path": path,
            "filename": name,
            "size": size if isinstance(size, int) else None,
            "sha256": sha if _SHA256.match(sha) else None,
            "url": hf_file_url(info, path),
            "folder": folder,
            "exists": installed_in is not None,
            "installed_in": installed_in,
        })
    files.sort(key=lambda f: f["path"].lower())
    if len(files) > HF_MAX_FILES:
        files, truncated = files[:HF_MAX_FILES], True
    return {"repo": info.repo, "revision": info.revision, "path": info.path, "diffusers": diffusers,
            "files": files, "skipped": skipped, "truncated": truncated}


# ------------------------------------------------------------------- jobs ---

@dataclass
class Job:
    url: str
    folder: str
    filename: str
    dest: str
    connections: int
    expected_sha256: Optional[str] = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "queued"   # queued|probing|downloading|verifying|done|exists|error|cancelled
    total: Optional[int] = None
    done: int = 0
    speed: float = 0.0
    active_connections: int = 0
    verified: bool = False
    error: Optional[str] = None
    created: float = field(default_factory=time.time)
    finished: Optional[float] = None
    task: Optional[asyncio.Task] = field(default=None, repr=False)
    download: Optional[SegmentedDownload] = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "url": self.url, "folder": self.folder, "filename": self.filename,
            "dest": self.dest, "connections": self.connections, "status": self.status,
            "total": self.total, "done": self.done, "speed": self.speed,
            "active_connections": self.active_connections, "verified": self.verified,
            "expected_sha256": self.expected_sha256, "error": self.error,
            "created": self.created, "finished": self.finished,
        }


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self._sem: Optional[asyncio.Semaphore] = None

    def _semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(MAX_PARALLEL_FILES)
        return self._sem

    def broadcast(self, job: Job) -> None:
        try:
            PromptServer.instance.send_sync(EVENT, job.to_dict())
        except Exception:
            pass

    def find_active(self, dest: str) -> Optional[Job]:
        for job in self.jobs.values():
            if job.dest == dest and job.status in ("queued", "probing", "downloading", "verifying"):
                return job
        return None

    def add(self, job: Job) -> Job:
        self.jobs[job.id] = job
        # The file may already be in another directory of the same type (e.g. models/unet).
        installed = installed_path(job.folder, job.filename)
        if installed and os.path.isfile(installed):
            job.dest = installed
        if os.path.exists(job.dest):
            job.status, job.finished = "exists", time.time()
            job.total = job.done = os.path.getsize(job.dest)
        else:
            job.task = asyncio.get_running_loop().create_task(self._run(job))
        self.broadcast(job)
        return job

    def cancel(self, job_id: str) -> Optional[Job]:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        if job.download is not None:
            job.download.cancel()
        elif job.status == "queued":
            job.status, job.finished = "cancelled", time.time()
            if job.task:
                job.task.cancel()
            self.broadcast(job)
        return job

    def clear_finished(self) -> None:
        for job_id in [j.id for j in self.jobs.values() if j.status in ("done", "exists", "error", "cancelled")]:
            del self.jobs[job_id]

    async def _run(self, job: Job) -> None:
        try:
            async with self._semaphore():
                if job.status == "cancelled":
                    return

                def on_progress(p: Progress) -> None:
                    job.status = p.phase if p.phase != "done" else job.status
                    job.total, job.done, job.speed = p.total, p.done, p.speed
                    job.active_connections = p.connections
                    self.broadcast(job)

                job.download = SegmentedDownload(
                    job.url, job.dest, connections=job.connections,
                    expected_sha256=job.expected_sha256, headers=auth_headers(job.url),
                    on_progress=on_progress,
                )
                await job.download.run()
                job.verified = job.download.verified
                job.expected_sha256 = job.download.expected_sha256
                job.status = "done"
                log.info("Ráfaga: %s listo%s", job.dest, " (SHA-256 OK)" if job.verified else "")
                refresh_model_lists(job.folder)
        except (DownloadCancelled, asyncio.CancelledError):
            job.status = "cancelled"
            if job.download is not None:
                job.download.discard()
        except DownloadError as e:
            job.status, job.error = "error", str(e)
        except Exception as e:  # never let a bug kill the server loop silently
            log.exception("Ráfaga: error inesperado")
            job.status, job.error = "error", f"{type(e).__name__}: {e}"
        finally:
            job.finished = time.time()
            job.speed = 0.0
            job.download = None
            self.broadcast(job)


manager = JobManager()


# ----------------------------------------------------------------- routes ---

routes = PromptServer.instance.routes


def _json_error(message: str, status: int = 400) -> web.Response:
    return web.json_response({"error": message}, status=status)


@routes.get("/rafaga/folders")
async def get_folders(_request: web.Request) -> web.Response:
    folders = []
    for name in model_folders():
        try:
            folders.append({"name": name, "path": default_dir(name)})
        except Exception:
            continue
    return web.json_response({"folders": folders})


@routes.get("/rafaga/jobs")
async def get_jobs(_request: web.Request) -> web.Response:
    jobs = sorted(manager.jobs.values(), key=lambda j: j.created)
    return web.json_response({"jobs": [j.to_dict() for j in jobs]})


@routes.post("/rafaga/jobs")
async def post_job(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _json_error("JSON inválido")

    url = str(body.get("url", "")).strip()
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        return _json_error("La URL tiene que empezar con http:// o https://")

    folder = (body.get("folder") or guess_folder(url) or "").strip()
    if not folder:
        return _json_error("Elegí la carpeta de destino (no se pudo deducir de la URL)")

    try:
        connections = int(body.get("connections") or DEFAULT_CONNECTIONS)
    except (TypeError, ValueError):
        connections = DEFAULT_CONNECTIONS
    connections = max(1, min(connections, MAX_CONNECTIONS))

    sha = body.get("sha256")
    sha = sha.lower() if isinstance(sha, str) and _SHA256.match(sha) else None

    filename = str(body.get("filename") or "").strip() or filename_from_url(url) or ""
    if not os.path.splitext(filename)[1]:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                info = await probe(session, url, auth_headers(url))
            filename = info.filename or filename
        except DownloadError as e:
            return _json_error(str(e))

    try:
        dest = resolve_destination(folder, filename)
    except ValueError as e:
        return _json_error(str(e))

    existing = manager.find_active(dest)
    if existing:
        return web.json_response(existing.to_dict())

    job = manager.add(Job(url=url, folder=folder, filename=filename, dest=dest,
                          connections=connections, expected_sha256=sha))
    return web.json_response(job.to_dict())


@routes.get("/rafaga/hf/files")
async def get_hf_files(request: web.Request) -> web.Response:
    """Model files of a Hugging Face repo (or a folder of it), to pick which ones to download."""
    info = parse_hf_repo_url(request.query.get("url", ""))
    if info is None:
        return _json_error("No es un link a un repo de Hugging Face (huggingface.co/<dueño>/<repo>)")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            listing = await list_hf_repo(session, info)
    except DownloadError as e:
        return _json_error(str(e))
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
        return _json_error(f"No se pudo consultar Hugging Face: {e}", 502)
    return web.json_response(listing)


@routes.post("/rafaga/jobs/{job_id}/cancel")
async def cancel_job(request: web.Request) -> web.Response:
    job = manager.cancel(request.match_info["job_id"])
    if job is None:
        return _json_error("No existe esa descarga", 404)
    return web.json_response(job.to_dict())


@routes.post("/rafaga/jobs/clear")
async def clear_jobs(_request: web.Request) -> web.Response:
    manager.clear_finished()
    return web.json_response({"ok": True})


@routes.post("/rafaga/check")
async def check_models(request: web.Request) -> web.Response:
    """Which of the given models ({name, directory}) are already installed."""
    try:
        body = await request.json()
        models = body.get("models", [])
        assert isinstance(models, list)
    except Exception:
        return _json_error("JSON inválido")
    results = []
    for m in models[:500]:
        name = str(m.get("name", ""))
        directory = str(m.get("directory", ""))
        exists = False
        if name and directory in folder_paths.folder_names_and_paths:
            try:
                exists = folder_paths.get_full_path(directory, name) is not None
            except Exception:
                exists = False
        results.append({"name": name, "directory": directory, "exists": exists})
    return web.json_response({"results": results})
