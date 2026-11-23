"""Tests for the path/folder validation in rafaga.server, using stand-ins for ComfyUI modules."""

import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TMP = tempfile.mkdtemp()
MODELS = os.path.join(TMP, "models")

# --- minimal stand-ins for ComfyUI's folder_paths and server modules ------------
folder_paths = types.ModuleType("folder_paths")
folder_paths.supported_pt_extensions = {".ckpt", ".pt", ".bin", ".pth", ".safetensors", ".sft"}
folder_paths.folder_names_and_paths = {
    "checkpoints": ([os.path.join(MODELS, "checkpoints")], folder_paths.supported_pt_extensions),
    "diffusion_models": ([os.path.join(MODELS, "diffusion_models")], folder_paths.supported_pt_extensions),
    "text_encoders": ([os.path.join(MODELS, "text_encoders")], folder_paths.supported_pt_extensions),
    "vae": ([os.path.join(MODELS, "vae")], folder_paths.supported_pt_extensions),
    "configs": ([os.path.join(MODELS, "configs")], [".yaml"]),
    "custom_nodes": ([os.path.join(TMP, "custom_nodes")], set()),
    "diffusers": ([os.path.join(MODELS, "diffusers")], ["folder"]),
}
folder_paths.filename_list_cache = {}
folder_paths.get_folder_paths = lambda name: folder_paths.folder_names_and_paths[name][0]
folder_paths.get_full_path = lambda name, f: None
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

    def test_auth_header_only_for_hugging_face(self):
        os.environ["HF_TOKEN"] = "hf_test"
        try:
            self.assertEqual(rs.auth_headers("https://huggingface.co/a/b"), {"Authorization": "Bearer hf_test"})
            self.assertEqual(rs.auth_headers("https://evil.example/huggingface.co"), {})
            self.assertEqual(rs.auth_headers("https://huggingface.co.evil.example/x"), {})
        finally:
            del os.environ["HF_TOKEN"]


if __name__ == "__main__":
    unittest.main()
