"""手元のスクリプトのうち、HTTP を伴わない部分。"""

from __future__ import annotations

import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

import importlib.util
import json
import os
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

SCRIPTS = Path(_bootstrap.SRC) / "scripts"


def _load(name: str):
    """scripts/ の単体スクリプトをモジュールとして読む。"""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


generate_image = _load("generate_image")
postprocess = _load("postprocess")

SETUPS = ("colab_setup", "colab_image_setup", "colab_video_setup")


def _transfer_block(script: str) -> str:
    """SCRIPT から転送モードを決める if ブロックだけを取り出す。"""
    lines = script.splitlines()
    start = next(
        i for i, line in enumerate(lines)
        if line.startswith("if ") and "CW_XET_HIGH_PERFORMANCE" in line
    )
    end = next(i for i in range(start, len(lines)) if lines[i] == "fi")
    return "\n".join(lines[start:end + 1])


class SetupTransferModeTest(unittest.TestCase):
    """構築スクリプトが選ぶ転送経路。

    **hf_transfer はもう使われない。** huggingface_hub 1.x は
    HF_HUB_ENABLE_HF_TRANSFER を受け取っても FutureWarning を出すだけで、
    Xet 対応リポジトリでは file_download.py が Xet を先に選ぶ。後継は
    HF_XET_HIGH_PERFORMANCE で、こちらは hf-xet 自体を高性能設定で回す。
    """

    def _script(self, name: str) -> str:
        return _load(name).SCRIPT

    def test_every_setup_is_valid_shell(self):
        for name in SETUPS:
            with self.subTest(name):
                r = subprocess.run(
                    ["bash", "-n"], input=self._script(name),
                    capture_output=True, text=True,
                )
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_high_performance_is_on_by_default(self):
        for name in SETUPS:
            with self.subTest(name):
                block = _transfer_block(self._script(name))
                r = subprocess.run(
                    ["bash", "-c", block + "\necho \"[$HF_XET_HIGH_PERFORMANCE]\""],
                    capture_output=True, text=True, env={"PATH": os.environ["PATH"]},
                )
                self.assertIn("[1]", r.stdout)

    def test_it_can_be_turned_off(self):
        """帯域と CPU を使い切る設定なので、切る道は残す。"""
        for name in SETUPS:
            with self.subTest(name):
                block = _transfer_block(self._script(name))
                r = subprocess.run(
                    ["bash", "-c", block + "\necho \"[$HF_XET_HIGH_PERFORMANCE]\""],
                    capture_output=True, text=True,
                    env={"PATH": os.environ["PATH"], "CW_XET_HIGH_PERFORMANCE": "0"},
                )
                self.assertIn("[]", r.stdout)

    def test_the_dead_hf_transfer_switch_is_gone(self):
        """効かない口を残すと、設定したのに変わらないという誤解を生む。"""
        for name in SETUPS:
            with self.subTest(name):
                script = self._script(name)
                # コメントでの言及は残す (なぜ使わないかを書いてある)。
                # 消したいのは実際に効かせる行のほう
                self.assertNotIn("export HF_HUB_ENABLE_HF_TRANSFER", script)
                self.assertNotIn("pip install -q hf_transfer", script)


class ParseLoraTest(unittest.TestCase):
    def test_with_strength(self):
        self.assertEqual(
            generate_image._parse_lora("anime.safetensors:0.8"),
            ("anime.safetensors", 0.8),
        )

    def test_without_strength(self):
        self.assertEqual(
            generate_image._parse_lora("anime.safetensors"),
            ("anime.safetensors", 1.0),
        )


class HintTest(unittest.TestCase):
    def test_model_mismatch_is_explained(self):
        """構築したモデルと --model がずれたときに、次の一手を出すこと。"""
        error = ("ワークフロー投入に失敗 (400): "
                 '{"node_errors": {"1": {"errors": [{"type": "value_not_in_list"}]}}}')
        self.assertIn("--model", generate_image._hint(error))

    def test_other_errors_get_no_noise(self):
        self.assertEqual(generate_image._hint("CUDA out of memory"), "")
        self.assertEqual(generate_image._hint(None), "")


class ProbeTest(unittest.TestCase):
    """仕上げの入力読み。自前 -> ffprobe -> 名指しして停止、の三段。

    **黙って既定値で代用しない。** 見積もりが入力と噛み合わないまま投入すると、
    倍率も RAM も当てにならないまま GPU 時間を捨てることになる。
    """

    def _write(self, name: str, data: bytes) -> Path:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / name
        path.write_bytes(data)
        return path

    def test_mp4_is_read_without_ffprobe(self):
        """**mp4 なら外部コマンドを呼ばない。** 仕上げのために ffmpeg を入れさせない。"""
        clip = next(iter(sorted(Path(_bootstrap.ROOT).glob("works/*.mp4"))), None)
        if clip is None:
            self.skipTest("手元に mp4 が無い")
        with mock.patch.object(postprocess.subprocess, "run",
                               side_effect=AssertionError("ffprobe を呼んではいけない")):
            got = postprocess._probe(clip)
        self.assertGreater(got["width"], 0)
        self.assertGreater(got["frames"], 0)

    def test_other_formats_fall_back_to_ffprobe(self):
        path = self._write("clip.webm", b"\x1a\x45\xdf\xa3" + b"\x00" * 64)
        answer = json.dumps({"streams": [{"width": 1920, "height": 1080,
                                          "r_frame_rate": "30/1", "nb_frames": "90"}]})
        with mock.patch.object(postprocess.subprocess, "run",
                               return_value=SimpleNamespace(stdout=answer)):
            got = postprocess._probe(path)
        self.assertEqual((got["width"], got["height"], got["frames"]), (1920, 1080, 90))

    def test_missing_ffprobe_names_what_is_needed(self):
        path = self._write("clip.webm", b"\x1a\x45\xdf\xa3" + b"\x00" * 64)
        with mock.patch.object(postprocess.subprocess, "run", side_effect=FileNotFoundError):
            with self.assertRaises(SystemExit) as cm:
                postprocess._probe(path)
        self.assertIn("ffmpeg", str(cm.exception))
        self.assertIn("mp4", str(cm.exception))

    def test_unreadable_input_is_reported(self):
        path = self._write("clip.webm", b"\x1a\x45\xdf\xa3" + b"\x00" * 64)
        error = subprocess.CalledProcessError(1, "ffprobe", stderr="moov atom not found")
        with mock.patch.object(postprocess.subprocess, "run", side_effect=error):
            with self.assertRaises(SystemExit) as cm:
                postprocess._probe(path)
        self.assertIn("moov atom not found", str(cm.exception))

    def test_missing_file_is_reported(self):
        with self.assertRaises(SystemExit) as cm:
            postprocess._probe(Path("/nonexistent/clip.mp4"))
        self.assertIn("読めませんでした", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
