"""手元のスクリプトのうち、HTTP を伴わない部分。"""

from __future__ import annotations

import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

import importlib.util
import sys

SCRIPTS = Path(_bootstrap.SRC) / "scripts"


def _load(name: str):
    """scripts/ の単体スクリプトをモジュールとして読む。"""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


generate_image = _load("generate_image")


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


if __name__ == "__main__":
    unittest.main()
