"""Wan2.2 / LTX のウェイト取得の事前チェック。

**実際に落として確かめない。** 1モデルで 40GB 級を取りに行くので、判断のところだけを見る。
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _bootstrap  # noqa: F401

_SETUP = Path(_bootstrap.SRC) / "setup"


def _load(name):
    sys.path.insert(0, str(_SETUP))
    spec = importlib.util.spec_from_file_location(name, _SETUP / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dvm = _load("download_video_models")


class DiskCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def _run(self, free_gb: float, models=("wan2.2",), extra=()):
        """main() を空き容量だけ差し替えて回す。取得には入らせない。"""
        self.enterContext(patch.object(
            shutil, "disk_usage",
            lambda _p: type("U", (), {"total": 0, "used": 0, "free": int(free_gb * 1024**3)})(),
        ))
        called = []
        self.enterContext(patch.object(dvm, "fetch", lambda *a, **k: called.append(a[0])))
        self.enterContext(patch.object(
            sys, "argv",
            ["download_video_models.py", "--comfy", str(self.tmp), "--models", *models, *extra],
        ))
        return dvm.main(), called

    def test_stops_before_downloading_when_disk_is_short(self):
        code, called = self._run(free_gb=20.0)
        self.assertEqual(code, 1)
        self.assertEqual(called, [])  # 1ファイルも取りに行かない

    def test_proceeds_when_disk_is_enough(self):
        """仕上げ用(補間・拡大)は 160MB なので、指定しなくても一緒に落とす。"""
        code, called = self._run(free_gb=80.0)
        self.assertEqual(code, 0)
        self.assertEqual(len(called), len(dvm.MODELS["wan2.2"]) + len(dvm.POSTPROCESS))

    def test_postprocess_can_be_skipped(self):
        code, called = self._run(free_gb=80.0, extra=["--no-postprocess"])
        self.assertEqual(code, 0)
        self.assertEqual(len(called), len(dvm.MODELS["wan2.2"]))


class ModelLayoutTest(unittest.TestCase):
    def test_shared_encoder_is_not_fetched_twice(self):
        """umt5 は Wan の全構成で共用。二重に数えると容量判定が狂う。"""
        plan = dvm.plan_for(["wan2.2", "wan2.2-5b"])
        self.assertEqual(len(plan), len(set(plan)))
        self.assertEqual(sum(1 for a in plan if "umt5" in a[2]), 1)

    def test_s2v_brings_the_audio_encoder(self):
        """音声エンコーダが無いと S2V は動かない。置き場も専用フォルダ。"""
        dests = {a[2] for a in dvm.MODELS["wan2.2-s2v"]}
        self.assertTrue(
            any(d.startswith("audio_encoders/") and "wav2vec2" in d for d in dests)
        )
        self.assertTrue(any(d.startswith("diffusion_models/") and "s2v" in d for d in dests))

    def test_ltx_lands_in_the_folders_comfyui_looks_at(self):
        """LTX はチェックポイント・LoRA・latent アップスケーラで置き場が別々。"""
        dests = {a[2].split("/")[0] for a in dvm.MODELS["ltx-2.3"]}
        self.assertEqual(
            dests, {"checkpoints", "text_encoders", "loras", "latent_upscale_models"}
        )

    def test_ltx_gguf_puts_the_audio_vae_and_connectors_in_checkpoints(self):
        """ComfyUI 側のローダが ckpt_name 入力から選ぶので、vae/ に置くと見えない。"""
        by_dest = {a[2] for a in dvm.MODELS["ltx-2.3-gguf"]}
        self.assertTrue(
            any(d.startswith("checkpoints/") and "audio_vae" in d for d in by_dest)
        )
        self.assertTrue(
            any(d.startswith("checkpoints/") and "connectors" in d for d in by_dest)
        )
        self.assertTrue(any(d.startswith("vae/") and "video_vae" in d for d in by_dest))
        self.assertTrue(
            any(d.startswith("diffusion_models/") and d.endswith(".gguf") for d in by_dest)
        )

    def test_ltx_gguf_is_small_enough_for_an_l4(self):
        """L4 は 24GB。本体が収まらないと部分オフロードに落ちて意味がなくなる。"""
        unet = next(a for a in dvm.MODELS["ltx-2.3-gguf"] if a[2].endswith(".gguf"))
        self.assertLess(unet[3], 20.0)
        fp8 = next(a for a in dvm.MODELS["ltx-2.3"] if "dev-fp8" in a[2])
        self.assertGreater(fp8[3], 24.0)  # 比較対象。fp8 は載らない


if __name__ == "__main__":
    unittest.main()
