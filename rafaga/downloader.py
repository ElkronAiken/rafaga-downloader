"""Segmented (multi-connection) HTTP downloader with resume and SHA-256 verification.

This module has no ComfyUI imports so it can be tested on its own.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
import urllib.parse
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

import aiohttp

READ_CHUNK = 1 << 20          # bytes read from the socket per iteration
WRITE_BUFFER = 4 << 20        # bytes buffered before writing to disk
MIN_SEGMENT = 8 << 20         # never split a file into segments smaller than this
STATE_SAVE_INTERVAL = 2.0     # seconds between resume-state saves
MAX_RETRIES = 8               # consecutive failures allowed per segment
PART_SUFFIX = ".rafaga.part"
STATE_SUFFIX = ".rafaga.json"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class DownloadError(Exception):
    """A download failed and cannot continue.

    `code` and `params` let the interface show the message in its own language;
    the (Spanish) text is the fallback.
    """

    def __init__(self, message: str, code: str = "error", **params) -> None:
        super().__init__(message)
        self.code = code
        self.params = params


class DownloadCancelled(DownloadError):
    """The download was cancelled by the user."""

    def __init__(self, message: str = "Cancelado") -> None:
        super().__init__(message, "cancelled")


@dataclass
class Probe:
    url: str
    final_url: str
    size: Optional[int]
    accepts_ranges: bool
    sha256: Optional[str]
    filename: Optional[str]


@dataclass
class Segment:
    start: int
    end: int          # inclusive
    pos: int          # next byte to fetch

    @property
    def done(self) -> bool:
        return self.pos > self.end


@dataclass
class Progress:
    total: Optional[int] = None
    done: int = 0
    speed: float = 0.0            # bytes/s, smoothed
    connections: int = 0
    phase: str = "probing"        # probing | downloading | verifying | done
    segments: list = field(default_factory=list)


def filename_from_headers(content_disposition: Optional[str]) -> Optional[str]:
    if not content_disposition:
        return None
    m = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", content_disposition, re.I)
    if m:
        return urllib.parse.unquote(m.group(1).strip().strip('"'))
    m = re.search(r'filename="?([^";]+)"?', content_disposition, re.I)
    return m.group(1).strip() if m else None


def filename_from_url(url: str) -> Optional[str]:
    path = urllib.parse.urlparse(url).path
    name = urllib.parse.unquote(path.rsplit("/", 1)[-1])
    return name or None


def _sha256_from_headers(headers) -> Optional[str]:
    # Hugging Face exposes the LFS object hash as X-Linked-ETag (quoted sha256).
    for key in ("X-Linked-ETag", "x-linked-etag"):
        value = headers.get(key)
        if value:
            value = value.strip().strip('"').lower()
            if _SHA256_RE.match(value):
                return value
    return None


def _size_from_headers(headers) -> Optional[int]:
    for key in ("X-Linked-Size", "x-linked-size"):
        if headers.get(key, "").isdigit():
            return int(headers[key])
    return None


async def probe(session: aiohttp.ClientSession, url: str, headers: Optional[dict] = None) -> Probe:
    """Find size, range support, expected hash and filename of a remote file."""
    headers = dict(headers or {})
    sha = size = None
    name = None
    final_url = url
    accepts = False
    try:
        async with session.head(url, headers=headers, allow_redirects=True) as r:
            for resp in [*r.history, r]:
                sha = sha or _sha256_from_headers(resp.headers)
                size = size or _size_from_headers(resp.headers)
                name = name or filename_from_headers(resp.headers.get("Content-Disposition"))
            if r.status < 400:
                final_url = str(r.url)
                if size is None and r.headers.get("Content-Length", "").isdigit():
                    size = int(r.headers["Content-Length"])
                accepts = r.headers.get("Accept-Ranges", "").lower() == "bytes"
            elif r.status in (401, 403):
                raise DownloadError(f"Acceso denegado (HTTP {r.status}). ¿El modelo requiere token?",
                                    "access_denied", status=r.status)
            elif r.status == 404:
                raise DownloadError("No encontrado (HTTP 404)", "not_found")
    except DownloadError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError):
        pass  # some servers reject HEAD: fall back to a 1-byte ranged GET

    if size is None or not accepts:
        try:
            async with session.get(url, headers={**headers, "Range": "bytes=0-0"}, allow_redirects=True) as r:
                for resp in [*r.history, r]:
                    sha = sha or _sha256_from_headers(resp.headers)
                    name = name or filename_from_headers(resp.headers.get("Content-Disposition"))
                if r.status in (401, 403):
                    raise DownloadError(f"Acceso denegado (HTTP {r.status}). ¿El modelo requiere token?",
                                    "access_denied", status=r.status)
                if r.status == 404:
                    raise DownloadError("No encontrado (HTTP 404)", "not_found")
                final_url = str(r.url)
                m = re.match(r"bytes \d+-\d+/(\d+)", r.headers.get("Content-Range", ""))
                if r.status == 206 and m:
                    accepts = True
                    size = size or int(m.group(1))
                elif size is None and r.headers.get("Content-Length", "").isdigit():
                    size = int(r.headers["Content-Length"])
        except DownloadError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise DownloadError(f"No se pudo conectar: {e}", "connect_failed", detail=str(e)) from e

    return Probe(url=url, final_url=final_url, size=size, accepts_ranges=accepts,
                 sha256=sha, filename=name or filename_from_url(final_url) or filename_from_url(url))


def plan_segments(size: int, connections: int) -> list[Segment]:
    """Split [0, size) into at most `connections` contiguous segments."""
    if size <= 0:
        return [Segment(0, -1, 0)]
    count = max(1, min(connections, size // MIN_SEGMENT or 1))
    chunk = -(-size // count)  # ceil division
    segments = []
    start = 0
    while start < size:
        end = min(start + chunk, size) - 1
        segments.append(Segment(start, end, start))
        start = end + 1
    return segments


async def sha256_file(path: str, cancel: Optional[asyncio.Event] = None) -> str:
    def _hash() -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                if cancel is not None and cancel.is_set():
                    raise DownloadCancelled()
                block = f.read(16 << 20)
                if not block:
                    break
                h.update(block)
        return h.hexdigest()

    return await asyncio.to_thread(_hash)


class SegmentedDownload:
    """Downloads `url` to `dest` using several ranged connections.

    The data goes to `dest + PART_SUFFIX`; segment positions are saved to
    `dest + STATE_SUFFIX` so an interrupted download resumes where it stopped.
    The final file only appears at `dest` after size (and hash) checks pass.
    """

    def __init__(
        self,
        url: str,
        dest: str,
        *,
        connections: int = 8,
        expected_sha256: Optional[str] = None,
        headers: Optional[dict] = None,
        on_progress: Optional[Callable[[Progress], None]] = None,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> None:
        self.url = url
        self.dest = dest
        self.part = dest + PART_SUFFIX
        self.state_path = dest + STATE_SUFFIX
        self.connections = max(1, min(int(connections), 32))
        self.expected_sha256 = expected_sha256.lower() if expected_sha256 else None
        self.headers = dict(headers or {})
        self.on_progress = on_progress
        self._external_session = session
        self.cancel_event = asyncio.Event()
        self.progress = Progress()
        self.probe: Optional[Probe] = None
        self.verified = False
        self._segments: list[Segment] = []
        self._last_save = 0.0
        self._speed_t = time.monotonic()
        self._speed_bytes = 0

    def cancel(self) -> None:
        self.cancel_event.set()

    # -- progress -----------------------------------------------------------

    def _emit(self) -> None:
        if self.on_progress:
            try:
                self.on_progress(self.progress)
            except Exception:
                pass

    def _count(self, n: int) -> None:
        self.progress.done += n
        now = time.monotonic()
        dt = now - self._speed_t
        if dt >= 0.5:
            inst = (self.progress.done - self._speed_bytes) / dt
            self.progress.speed = inst if self.progress.speed == 0 else 0.7 * self.progress.speed + 0.3 * inst
            self._speed_t, self._speed_bytes = now, self.progress.done
            self.progress.segments = [[s.start, s.end, s.pos] for s in self._segments]
            self._emit()
        if now - self._last_save >= STATE_SAVE_INTERVAL:
            self._save_state()

    # -- resume state -------------------------------------------------------

    def _save_state(self) -> None:
        if not self.probe or self.probe.size is None:
            return
        self._last_save = time.monotonic()
        data = {
            "url": self.url,
            "size": self.probe.size,
            "sha256": self.expected_sha256,
            "segments": [asdict(s) for s in self._segments],
        }
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, self.state_path)

    def _load_state(self, size: int) -> Optional[list[Segment]]:
        try:
            with open(self.state_path, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("url") != self.url or data.get("size") != size:
                return None
            if not os.path.exists(self.part) or os.path.getsize(self.part) != size:
                return None
            segments = [Segment(**s) for s in data["segments"]]
            if not segments or segments[0].start != 0 or segments[-1].end != size - 1:
                return None
            return segments
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def discard(self) -> None:
        """Delete partial data and resume state (used after a user cancel)."""
        self._cleanup_temp()

    def _cleanup_temp(self) -> None:
        for path in (self.part, self.state_path, self.state_path + ".tmp"):
            try:
                os.remove(path)
            except OSError:
                pass

    # -- download -----------------------------------------------------------

    async def run(self) -> str:
        session = self._external_session or aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=90))
        try:
            return await self._run(session)
        finally:
            if self._external_session is None:
                await session.close()

    async def _run(self, session: aiohttp.ClientSession) -> str:
        self.progress.phase = "probing"
        self._emit()
        self.probe = await probe(session, self.url, self.headers)
        if self.expected_sha256 is None and self.probe.sha256:
            self.expected_sha256 = self.probe.sha256
        size = self.probe.size
        self.progress.total = size
        os.makedirs(os.path.dirname(self.dest) or ".", exist_ok=True)

        if size is not None and self.probe.accepts_ranges and size > 0:
            await self._ranged(session, size)
        else:
            await self._single(session)

        if self.cancel_event.is_set():
            raise DownloadCancelled()

        actual = os.path.getsize(self.part)
        if size is not None and actual != size:
            raise DownloadError(f"Tamaño incorrecto: {actual} de {size} bytes", "wrong_size", actual=actual, size=size)

        if self.expected_sha256:
            self.progress.phase = "verifying"
            self._emit()
            digest = await sha256_file(self.part, self.cancel_event)
            if digest != self.expected_sha256:
                self._cleanup_temp()
                raise DownloadError("El SHA-256 no coincide: el archivo está corrupto y se descartó", "sha_mismatch")
            self.verified = True

        os.replace(self.part, self.dest)
        for path in (self.state_path, self.state_path + ".tmp"):
            try:
                os.remove(path)
            except OSError:
                pass
        self.progress.phase = "done"
        self.progress.done = os.path.getsize(self.dest)
        self._emit()
        return self.dest

    async def _ranged(self, session: aiohttp.ClientSession, size: int) -> None:
        segments = self._load_state(size)
        if segments is None:
            segments = plan_segments(size, self.connections)
            with open(self.part, "wb") as f:
                f.truncate(size)
        self._segments = segments
        self.progress.done = sum(s.pos - s.start for s in segments)
        self._speed_bytes = self.progress.done
        self.progress.connections = sum(1 for s in segments if not s.done)
        self.progress.phase = "downloading"
        self._save_state()
        self._emit()

        tasks = [asyncio.create_task(self._fetch_segment(session, s)) for s in segments if not s.done]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._save_state()
            raise
        self._save_state()

    async def _fetch_segment(self, session: aiohttp.ClientSession, seg: Segment) -> None:
        failures = 0
        while not seg.done:
            if self.cancel_event.is_set():
                raise DownloadCancelled()
            start_pos = seg.pos
            try:
                headers = {**self.headers, "Range": f"bytes={seg.pos}-{seg.end}"}
                async with session.get(self.url, headers=headers, allow_redirects=True) as r:
                    if r.status != 206:
                        raise DownloadError(f"El servidor respondió HTTP {r.status} a un pedido por tramos",
                                            "no_ranges", status=r.status)
                    with open(self.part, "r+b") as f:
                        f.seek(seg.pos)
                        buf = bytearray()

                        def flush() -> None:
                            if buf:
                                f.write(buf)
                                seg.pos += len(buf)
                                self._count(len(buf))
                                buf.clear()

                        try:
                            async for chunk in r.content.iter_chunked(READ_CHUNK):
                                if self.cancel_event.is_set():
                                    raise DownloadCancelled()
                                room = seg.end + 1 - (seg.pos + len(buf))
                                if room <= 0:
                                    break
                                buf += chunk[:room]
                                if len(buf) >= WRITE_BUFFER:
                                    flush()
                        finally:
                            # Keep what already arrived, even if the connection dropped.
                            flush()
            except DownloadCancelled:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, DownloadError) as e:
                if seg.pos > start_pos:
                    failures = 0  # it made progress: a dropped connection, not a dead server
                    continue
                failures += 1
                if failures > MAX_RETRIES:
                    raise DownloadError(f"Un tramo falló {failures} veces seguidas: {e}", "segment_failed",
                                        count=failures, detail=str(e)) from e
                await asyncio.sleep(min(2 ** failures, 30))

    async def _single(self, session: aiohttp.ClientSession) -> None:
        """Fallback for servers without range support: one connection, no resume."""
        self._segments = []
        self.progress.connections = 1
        self.progress.phase = "downloading"
        self.progress.done = 0
        self._emit()
        async with session.get(self.url, headers=self.headers, allow_redirects=True) as r:
            if r.status >= 400:
                raise DownloadError(f"HTTP {r.status}", "http_status", status=r.status)
            with open(self.part, "wb") as f:
                async for chunk in r.content.iter_chunked(READ_CHUNK):
                    if self.cancel_event.is_set():
                        raise DownloadCancelled()
                    f.write(chunk)
                    self._count(len(chunk))
