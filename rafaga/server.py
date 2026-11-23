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
    return folder_paths.get_folder_paths(folder)[0]


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


def guess_folder(url: str) -> Optional[str]:
    """Hugging Face repos often mirror ComfyUI's layout: .../resolve/main/vae/x.safetensors"""
    segments = urllib.parse.urlparse(url).path.split("/")
    known = set(model_folders())
    for seg in reversed(segments[:-1]):
        if seg in known:
            return seg
        if seg == "unet":
            return "diffusion_models"
        if seg == "clip":
            return "text_encoders"
    return None


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
