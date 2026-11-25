"""Tests for the path/folder validation in rafaga.server, using stand-ins for ComfyUI modules."""

import os
import sys
import tempfile
import types
import unittest

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TMP = tempfile.mkdtemp()
MODELS = os.path.join(TMP, "models")

# --- minimal stand-ins for ComfyUI's folder_paths and server modules ------------
folder_paths = types.ModuleType("folder_paths")
folder_paths.supported_pt_extensions = {".ckpt", ".pt", ".bin", ".pth", ".safetensors", ".sft"}
folder_paths.folder_names_and_paths = {
    "checkpoints": ([os.path.join(MODELS, "checkpoints")], folder_paths.supported_pt_extensions),
    # ComfyUI Desktop registers the legacy aliases first (see default_dir()).
    "controlnet": ([os.path.join(MODELS, "t2i_adapter"), os.path.join(MODELS, "controlnet")],
                   folder_paths.supported_pt_extensions),
    "diffusion_models": ([os.path.join(MODELS, "unet"), os.path.join(MODELS, "diffusion_models")],
                         folder_paths.supported_pt_extensions),
    "text_encoders": ([os.path.join(MODELS, "clip"), os.path.join(MODELS, "text_encoders")],
                      folder_paths.supported_pt_extensions),
    "vae": ([os.path.join(MODELS, "vae")], folder_paths.supported_pt_extensions),
    "configs": ([os.path.join(MODELS, "configs")], [".yaml"]),
    "custom_nodes": ([os.path.join(TMP, "custom_nodes")], set()),
    "diffusers": ([os.path.join(MODELS, "diffusers")], ["folder"]),
}
folder_paths.filename_list_cache = {}
folder_paths.get_folder_paths = lambda name: folder_paths.folder_names_and_paths[name][0]


def _get_full_path(name, filename):
    for base in folder_paths.folder_names_and_paths[name][0]:
        path = os.path.join(base, filename)
        if os.path.isfile(path):
            return path
    return None


folder_paths.get_full_path = _get_full_path
sys.modules["folder_paths"] = folder_paths


class _Routes:
    def _deco(self, _path):
        return lambda fn: fn
    get = post = _deco


server_mod = types.ModuleType("server")
server_mod.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(routes=_Routes(), send_sync=lambda *a: None))
sys.modules["server"] = server_mod

from rafaga import server as rs  # noqa: E402


class ServerHelperTests(unittest.TestCase):
    def test_model_folders_exclude_non_model_entries(self):
        folders = rs.model_folders()
        self.assertIn("vae", folders)
        for name in ("configs", "custom_nodes", "diffusers"):
            self.assertNotIn(name, folders)

    def test_resolve_destination_ok(self):
        dest = rs.resolve_destination("vae", "qwen_image_2.1_vae_bf16.safetensors")
        self.assertEqual(dest, os.path.abspath(os.path.join(MODELS, "vae", "qwen_image_2.1_vae_bf16.safetensors")))
        sub = rs.resolve_destination("checkpoints", "sd15/model.ckpt")
        self.assertTrue(sub.endswith(os.path.join("checkpoints", "sd15", "model.ckpt")))

    def test_resolve_destination_rejects_traversal_and_bad_names(self):
        for bad in ("../evil.safetensors", "..\\..\\evil.safetensors", "a/../../x.safetensors", "con:.pt", "", "/"):
            with self.assertRaises(ValueError, msg=bad):
                rs.resolve_destination("vae", bad)

    def test_resolve_destination_rejects_non_model_extensions(self):
        for bad in ("script.py", "run.bat", "payload.exe", "noext"):
            with self.assertRaises(ValueError, msg=bad):
                rs.resolve_destination("vae", bad)
        self.assertTrue(rs.resolve_destination("vae", "x.gguf").endswith("x.gguf"))

    def test_resolve_destination_rejects_unknown_folder(self):
        with self.assertRaises(ValueError):
            rs.resolve_destination("custom_nodes", "x.safetensors")
        with self.assertRaises(ValueError):
            rs.resolve_destination("nope", "x.safetensors")

    def test_guess_folder_from_hf_url(self):
        base = "https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/"
        self.assertEqual(rs.guess_folder(base + "vae/qwen_image_2.1_vae_bf16.safetensors"), "vae")
        self.assertEqual(rs.guess_folder(base + "split_files/diffusion_models/x.safetensors"), "diffusion_models")
        self.assertEqual(rs.guess_folder(base + "unet/x.safetensors"), "diffusion_models")
        self.assertEqual(rs.guess_folder(base + "clip/x.safetensors"), "text_encoders")
        self.assertIsNone(rs.guess_folder("https://example.com/files/x.safetensors"))

    def test_default_dir_prefers_folder_named_like_the_type(self):
        # Desktop lists models/unet before models/diffusion_models, models/clip before
        # models/text_encoders and models/t2i_adapter before models/controlnet.
        self.assertEqual(rs.default_dir("diffusion_models"), os.path.join(MODELS, "diffusion_models"))
        self.assertEqual(rs.default_dir("text_encoders"), os.path.join(MODELS, "text_encoders"))
        self.assertEqual(rs.default_dir("controlnet"), os.path.join(MODELS, "controlnet"))
        self.assertEqual(rs.default_dir("vae"), os.path.join(MODELS, "vae"))
        dest = rs.resolve_destination("diffusion_models", "x.safetensors")
        self.assertEqual(dest, os.path.abspath(os.path.join(MODELS, "diffusion_models", "x.safetensors")))

    def test_file_already_in_an_alias_folder_is_not_downloaded_again(self):
        old = os.path.join(MODELS, "unet", "already_here.safetensors")
        os.makedirs(os.path.dirname(old), exist_ok=True)
        with open(old, "wb") as f:
            f.write(b"x" * 10)
        job = rs.Job(url="https://example.com/already_here.safetensors", folder="diffusion_models",
                     filename="already_here.safetensors",
                     dest=rs.resolve_destination("diffusion_models", "already_here.safetensors"), connections=8)
        try:
            rs.manager.add(job)  # no event loop needed: nothing should be scheduled
            self.assertEqual(job.status, "exists")
            self.assertEqual(job.dest, old)
            self.assertEqual(job.total, 10)
            self.assertIsNone(job.task)
        finally:
            rs.manager.jobs.pop(job.id, None)

    def test_auth_header_only_for_hugging_face(self):
        os.environ["HF_TOKEN"] = "hf_test"
        try:
            self.assertEqual(rs.auth_headers("https://huggingface.co/a/b"), {"Authorization": "Bearer hf_test"})
            self.assertEqual(rs.auth_headers("https://evil.example/huggingface.co"), {})
            self.assertEqual(rs.auth_headers("https://huggingface.co.evil.example/x"), {})
        finally:
            del os.environ["HF_TOKEN"]


SHA_VAE = "a" * 64
SHA_UNET = "b" * 64


def hf_tree_app(*, status=200, foreign_next=False, seen=None):
    """Imitates GET /api/models/<owner>/<name>/tree/<rev>[/<path>]?recursive=true (2 pages)."""

    async def tree(request: web.Request) -> web.Response:
        if seen is not None:
            seen.append(str(request.rel_url))
        if status != 200:
            return web.json_response({"error": "Repository not found"}, status=status)
        if "cursor" not in request.query:
            page = [
                {"type": "directory", "path": "split_files"},
                {"type": "file", "path": ".gitattributes", "size": 1500},
                {"type": "file", "path": "README.md", "size": 900},
                {"type": "file", "path": "split_files/vae/qwen_vae.safetensors", "size": 135,
                 "lfs": {"oid": SHA_VAE, "size": 254000000, "pointerSize": 135}},
                {"type": "file", "path": "split_files/diffusion_models/qwen fp8.safetensors", "size": 135,
                 "lfs": {"oid": SHA_UNET, "size": 20400000000, "pointerSize": 135}},
            ]
            host = "https://example.invalid" if foreign_next else f"http://{request.host}"
            link = f'<{host}{request.path}?recursive=true&cursor=2>; rel="next"'
            return web.json_response(page, headers={"Link": link})
        page = [
            {"type": "file", "path": "base-model.ckpt", "size": 135,
             "lfs": {"oid": "not-a-sha", "size": 5210000000, "pointerSize": 135}},
            {"type": "file", "path": "split_files/text_encoders/tokenizer.json", "size": 2000},
            {"type": "file", "path": "split_files/text_encoders/te.gguf", "size": 4096},
        ]
        return web.json_response(page)

    app = web.Application()
    app.router.add_get("/api/models/{owner}/{name}/tree/{rev}", tree)
    app.router.add_get("/api/models/{owner}/{name}/tree/{rev}/{path:.*}", tree)
    return app


class HuggingFaceRepoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_base = rs.HF_BASE

    async def asyncTearDown(self):
        rs.HF_BASE = self.original_base

    async def serve(self, **kwargs):
        server = TestServer(hf_tree_app(**kwargs))
        await server.start_server()
        self.addAsyncCleanup(server.close)
        rs.HF_BASE = str(server.make_url("")).rstrip("/")

    def test_parse_repo_urls(self):
        p = rs.parse_hf_repo_url
        self.assertEqual(p("https://huggingface.co/Comfy-Org/Qwen-Image"), rs.HFRepo("Comfy-Org/Qwen-Image", "main", ""))
        self.assertEqual(p("https://huggingface.co/Comfy-Org/Qwen-Image/"), rs.HFRepo("Comfy-Org/Qwen-Image", "main", ""))
        self.assertEqual(p("https://huggingface.co/Comfy-Org/Qwen-Image/tree/main/split_files/vae/"),
                         rs.HFRepo("Comfy-Org/Qwen-Image", "main", "split_files/vae"))
        self.assertEqual(p("https://hf.co/a/b.c_d-e/tree/v1.0"), rs.HFRepo("a/b.c_d-e", "v1.0", ""))
        self.assertEqual(p("https://huggingface.co/a/b/tree/refs%2Fpr%2F3/x").revision, "refs/pr/3")
        for not_repo in (
            "https://huggingface.co/a/b/resolve/main/x.safetensors",   # a single file
            "https://huggingface.co/a/b/blob/main/x.safetensors",
            "https://huggingface.co/a/b/discussions",
            "https://huggingface.co/a/b/tree/main/../../c",
            "https://huggingface.co/Comfy-Org",
            "https://huggingface.co/datasets/a/b",
            "https://huggingface.co/spaces/a/b",
            "https://huggingface.co.evil.example/a/b",
            "https://evil.example/huggingface.co/a/b",
            "ftp://huggingface.co/a/b",
            "not a url",
        ):
            self.assertIsNone(p(not_repo), not_repo)

    async def test_lists_only_model_files_with_folder_hash_and_size(self):
        seen = []
        await self.serve(seen=seen)
        for folder, name in (("vae", "qwen_vae.safetensors"), ("checkpoints", "base-model.ckpt")):
            os.makedirs(os.path.join(MODELS, folder), exist_ok=True)
            with open(os.path.join(MODELS, folder, name), "wb") as f:
                f.write(b"x")
        info = rs.parse_hf_repo_url("https://huggingface.co/Comfy-Org/Qwen-Image")
        async with aiohttp.ClientSession() as session:
            listing = await rs.list_hf_repo(session, info)

        self.assertEqual(len(seen), 2)  # followed the pagination link
        by_path = {f["path"]: f for f in listing["files"]}
        self.assertEqual(sorted(by_path), [
            "base-model.ckpt",
            "split_files/diffusion_models/qwen fp8.safetensors",
            "split_files/text_encoders/te.gguf",
            "split_files/vae/qwen_vae.safetensors",
        ])
        self.assertEqual(listing["skipped"], 3)  # .gitattributes, README.md, tokenizer.json
        self.assertFalse(listing["diffusers"])
        self.assertFalse(listing["truncated"])

        vae = by_path["split_files/vae/qwen_vae.safetensors"]
        self.assertEqual((vae["folder"], vae["sha256"], vae["size"], vae["exists"], vae["installed_in"]),
                         ("vae", SHA_VAE, 254000000, True, "vae"))
        unet = by_path["split_files/diffusion_models/qwen fp8.safetensors"]
        self.assertEqual((unet["folder"], unet["filename"], unet["exists"], unet["installed_in"]),
                         ("diffusion_models", "qwen fp8.safetensors", False, None))
        self.assertEqual(unet["url"],
                         rs.HF_BASE + "/Comfy-Org/Qwen-Image/resolve/main/split_files/diffusion_models/qwen%20fp8.safetensors")
        self.assertEqual(by_path["split_files/text_encoders/te.gguf"]["folder"], "text_encoders")
        root = by_path["base-model.ckpt"]
        self.assertIsNone(root["folder"])     # nothing in the path says where it goes
        self.assertIsNone(root["sha256"])     # malformed hashes are ignored
        self.assertEqual(root["size"], 5210000000)
        # ...but it is found wherever it was installed before.
        self.assertEqual((root["exists"], root["installed_in"]), (True, "checkpoints"))

    async def test_folder_link_lists_that_folder(self):
        seen = []
        await self.serve(seen=seen)
        info = rs.parse_hf_repo_url("https://huggingface.co/Comfy-Org/Qwen-Image/tree/main/split_files/vae")
        async with aiohttp.ClientSession() as session:
            await rs.list_hf_repo(session, info)
        self.assertTrue(seen[0].startswith("/api/models/Comfy-Org/Qwen-Image/tree/main/split_files/vae?"), seen[0])

    async def test_pagination_to_another_host_is_not_followed(self):
        seen = []
        await self.serve(foreign_next=True, seen=seen)
        info = rs.parse_hf_repo_url("https://huggingface.co/a/b")
        async with aiohttp.ClientSession() as session:
            listing = await rs.list_hf_repo(session, info)
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(listing["files"]), 2)

    async def test_private_or_missing_repo_explains_hf_token(self):
        await self.serve(status=401)
        info = rs.parse_hf_repo_url("https://huggingface.co/a/private")
        async with aiohttp.ClientSession() as session:
            with self.assertRaises(rs.DownloadError) as ctx:
                await rs.list_hf_repo(session, info)
        self.assertIn("HF_TOKEN", str(ctx.exception))

    async def test_diffusers_repo_is_detected(self):
        async def tree(_request):
            return web.json_response([
                {"type": "file", "path": "model_index.json", "size": 500},
                {"type": "file", "path": "unet/diffusion_pytorch_model.safetensors", "size": 135,
                 "lfs": {"oid": SHA_UNET, "size": 3400000000}},
            ])
        app = web.Application()
        app.router.add_get("/api/models/{owner}/{name}/tree/{rev}", tree)
        server = TestServer(app)
        await server.start_server()
        self.addAsyncCleanup(server.close)
        rs.HF_BASE = str(server.make_url("")).rstrip("/")
        async with aiohttp.ClientSession() as session:
            listing = await rs.list_hf_repo(session, rs.parse_hf_repo_url("https://huggingface.co/a/sd"))
        self.assertTrue(listing["diffusers"])
        self.assertEqual(listing["files"][0]["folder"], "diffusion_models")


if __name__ == "__main__":
    unittest.main()
