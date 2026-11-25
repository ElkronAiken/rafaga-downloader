"""Tests for rafaga.downloader against a local aiohttp server (no network needed).

Run from the repository root:  python -m unittest discover -s tests -v
"""

import asyncio
import hashlib
import json
import os
import sys
import tempfile
import unittest

from aiohttp import web
from aiohttp.test_utils import TestServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rafaga import downloader as dl  # noqa: E402

DATA = os.urandom(3 * dl.MIN_SEGMENT + 12345)   # ~24 MB, not a multiple of the segment size
SHA = hashlib.sha256(DATA).hexdigest()


def make_app(*, ranges=True, sha_header=True, fail_after=None, counter=None):
    """fail_after: cut each ranged response after N bytes (simulates dropped connections)."""

    async def handler(request: web.Request) -> web.StreamResponse:
        if counter is not None:
            counter.append(request.method + " " + request.headers.get("Range", "-"))
        headers = {"Content-Disposition": 'attachment; filename="model.safetensors"'}
        if sha_header:
            headers["X-Linked-ETag"] = f'"{SHA}"'
        rng = request.headers.get("Range")
        if ranges:
            headers["Accept-Ranges"] = "bytes"
        if request.method == "HEAD":
            headers["Content-Length"] = str(len(DATA))
            return web.Response(status=200, headers=headers)
        if ranges and rng:
            start, end = rng.split("=")[1].split("-")
            start, end = int(start), int(end or len(DATA) - 1)
            body = DATA[start:end + 1]
            headers["Content-Range"] = f"bytes {start}-{end}/{len(DATA)}"
            resp = web.StreamResponse(status=206, headers=headers)
            await resp.prepare(request)
            payload = body if fail_after is None else body[:fail_after]
            await resp.write(payload)
            if fail_after is not None and len(body) > fail_after:
                request.transport.close()  # abrupt cut
                return resp
            await resp.write_eof()
            return resp
        return web.Response(body=DATA, headers=headers)

    app = web.Application()
    app.router.add_route("*", "/model.safetensors", handler)
    return app


class DownloaderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dest = os.path.join(self.tmp.name, "out", "model.safetensors")

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def serve(self, **kwargs):
        server = TestServer(make_app(**kwargs))
        await server.start_server()
        self.addAsyncCleanup(server.close)
        return str(server.make_url("/model.safetensors"))

    def read_dest(self):
        with open(self.dest, "rb") as f:
            return f.read()

    async def test_plan_segments_covers_file_exactly(self):
        for size in (1, dl.MIN_SEGMENT - 1, dl.MIN_SEGMENT * 5 + 7, 10 ** 10):
            segs = dl.plan_segments(size, 8)
            self.assertEqual(segs[0].start, 0)
            self.assertEqual(segs[-1].end, size - 1)
            for a, b in zip(segs, segs[1:]):
                self.assertEqual(a.end + 1, b.start)
            self.assertLessEqual(len(segs), 8)

    async def test_probe_reads_size_hash_and_name(self):
        url = await self.serve()
        import aiohttp
        async with aiohttp.ClientSession() as s:
            p = await dl.probe(s, url)
        self.assertEqual(p.size, len(DATA))
        self.assertTrue(p.accepts_ranges)
        self.assertEqual(p.sha256, SHA)
        self.assertEqual(p.filename, "model.safetensors")

    async def test_segmented_download_is_identical_and_verified(self):
        requests = []
        url = await self.serve(counter=requests)
        job = dl.SegmentedDownload(url, self.dest, connections=4)
        await job.run()
        self.assertEqual(self.read_dest(), DATA)
        self.assertTrue(job.verified)
        self.assertGreaterEqual(sum(1 for r in requests if r.startswith("GET bytes=")), 3)
        self.assertFalse(os.path.exists(self.dest + dl.PART_SUFFIX))
        self.assertFalse(os.path.exists(self.dest + dl.STATE_SUFFIX))

    async def test_wrong_hash_is_rejected_and_discarded(self):
        url = await self.serve(sha_header=False)
        job = dl.SegmentedDownload(url, self.dest, connections=4, expected_sha256="0" * 64)
        with self.assertRaises(dl.DownloadError) as ctx:
            await job.run()
        self.assertEqual(ctx.exception.code, "sha_mismatch")  # the interface translates it
        self.assertFalse(os.path.exists(self.dest))
        self.assertFalse(os.path.exists(self.dest + dl.PART_SUFFIX))

    async def test_missing_file_error_has_code(self):
        url = (await self.serve()).replace("model.safetensors", "missing.safetensors")
        import aiohttp
        async with aiohttp.ClientSession() as s:
            with self.assertRaises(dl.DownloadError) as ctx:
                await dl.probe(s, url)
        self.assertEqual(ctx.exception.code, "not_found")
        self.assertEqual(dl.DownloadCancelled().code, "cancelled")

    async def test_server_without_ranges_uses_single_connection(self):
        url = await self.serve(ranges=False)
        job = dl.SegmentedDownload(url, self.dest, connections=8)
        await job.run()
        self.assertEqual(self.read_dest(), DATA)
        self.assertEqual(job.progress.connections, 1)

    async def test_dropped_connections_are_retried(self):
        url = await self.serve(fail_after=dl.MIN_SEGMENT // 3)
        original_sleep = asyncio.sleep
        dl.asyncio.sleep = lambda *_a, **_k: original_sleep(0)  # no backoff wait in tests
        try:
            job = dl.SegmentedDownload(url, self.dest, connections=4)
            await job.run()
        finally:
            dl.asyncio.sleep = original_sleep
        self.assertEqual(self.read_dest(), DATA)

    async def test_resume_from_saved_state(self):
        url = await self.serve()
        size = len(DATA)
        segs = dl.plan_segments(size, 4)
        os.makedirs(os.path.dirname(self.dest), exist_ok=True)
        # Simulate a previous run: first segment complete, the rest untouched.
        with open(self.dest + dl.PART_SUFFIX, "wb") as f:
            f.truncate(size)
            f.seek(0)
            f.write(DATA[segs[0].start:segs[0].end + 1])
        segs[0].pos = segs[0].end + 1
        with open(self.dest + dl.STATE_SUFFIX, "w") as f:
            json.dump({"url": url, "size": size, "sha256": SHA,
                       "segments": [s.__dict__ for s in segs]}, f)
        job = dl.SegmentedDownload(url, self.dest, connections=4)
        await job.run()
        self.assertEqual(self.read_dest(), DATA)
        self.assertTrue(job.verified)
        # Only the unfinished segments were downloaded again.
        self.assertEqual(job.progress.connections, len(segs) - 1)

    async def test_cancel_stops_and_keeps_no_final_file(self):
        url = await self.serve()
        job = dl.SegmentedDownload(url, self.dest, connections=4)

        def stop_early(progress):
            if progress.done > 0:
                job.cancel()

        job.on_progress = stop_early
        with self.assertRaises(dl.DownloadCancelled):
            await job.run()
        self.assertFalse(os.path.exists(self.dest))
        job.discard()
        self.assertFalse(os.path.exists(self.dest + dl.PART_SUFFIX))

    async def test_filename_helpers(self):
        self.assertEqual(dl.filename_from_url("https://h/a/b/c%20d.safetensors?download=true"), "c d.safetensors")
        self.assertEqual(dl.filename_from_headers('attachment; filename="x.gguf"'), "x.gguf")
        self.assertEqual(dl.filename_from_headers("attachment; filename*=UTF-8''r%C3%A1faga.pt"), "ráfaga.pt")


if __name__ == "__main__":
    unittest.main()
