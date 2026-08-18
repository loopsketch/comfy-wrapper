"""モデルカタログ (lib/model_catalog.py)。

**ここで守りたいのは「モデルを足してカタログを忘れた」を落とすこと。** カタログは
`GET /v1/models` で呼ぶ側へ配られ、向こうは解像度・音声の有無・スループットを
これだけに頼る。抜けていると、生成は通るのに見積もりが 0円 になるような静かな
食い違いになる(実際 music-video-creator2 が ltx-2.5 でそうなっていた)。

`server/models.py` は pydantic を要求するのでこのコンテナでは import できない。
モデル名の一覧は ast で読み出す。
"""

from __future__ import annotations

import ast
import importlib.util
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from lib import model_catalog
from lib.video_sizes import AUDIO_MODELS, MODEL_RESOLUTIONS

SRC = Path(_bootstrap.SRC)


def _literal_values(path: Path, name: str) -> set[str]:
    """`Name = Literal["a", "b"]` から値を取り出す。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        targets = getattr(node, "targets", [])
        if not (targets and isinstance(targets[0], ast.Name) and targets[0].id == name):
            continue
        return {e.value for e in node.value.slice.elts}
    raise AssertionError(f"{name} が {path} に無い")


def _setup_models(module: str) -> set[str]:
    """setup/download_*.py の MODELS のキー。"""
    path = SRC / "setup" / f"{module}.py"
    spec = importlib.util.spec_from_file_location(module, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return set(mod.MODELS)


class CoverageTest(unittest.TestCase):
    def test_video_models_match_the_api_literal(self):
        """API が受け付けるモデルと、カタログが説明するモデルを一致させる。

        ここがずれると、投げれば通るのにカタログに出ないモデルができる。
        """
        declared = _literal_values(SRC / "server" / "models.py", "VideoModel")
        self.assertEqual(set(model_catalog.ids("video")), declared)

    def test_image_models_match_the_downloadable_ones(self):
        """静止画は setup 側が実体を持つ。カタログはその部分集合であってはならない。"""
        downloadable = _setup_models("download_image_models")
        # anime / anime-edit は LoRA 構成の別名で、API の model 値ではない
        downloadable -= {"anime", "anime-edit"}
        self.assertEqual(set(model_catalog.ids("image")), downloadable)

    def test_every_video_model_has_all_resolution_tiers(self):
        """呼ぶ側は 480p/720p/1080p の名前で指定する。欠けると KeyError になる。"""
        for entry in model_catalog.catalog():
            if entry["kind"] != "video":
                continue
            with self.subTest(model=entry["id"]):
                self.assertEqual(set(entry["resolutions"]), {"480p", "720p", "1080p"})

    def test_resolutions_come_from_video_sizes(self):
        """写しを作らない。表は video_sizes が持ち、カタログはそれを配るだけ。"""
        for entry in model_catalog.catalog():
            if entry["kind"] != "video":
                continue
            source = MODEL_RESOLUTIONS[entry["id"]]
            got = {k: (v["megapixels"], v["short_edge"]) for k, v in entry["resolutions"].items()}
            with self.subTest(model=entry["id"]):
                self.assertEqual(got, source)

    def test_audio_flag_comes_from_video_sizes(self):
        for entry in model_catalog.catalog():
            if entry["kind"] != "video":
                continue
            with self.subTest(model=entry["id"]):
                self.assertEqual(entry["audio_out"], entry["id"] in AUDIO_MODELS)

    def test_every_model_declares_its_weight_size(self):
        """1セッションに1モデルの判断に使う。0 や欠損だと同居できるように見える。"""
        for entry in model_catalog.catalog():
            with self.subTest(model=entry["id"]):
                self.assertGreater(entry["weights_gb"], 0)


class ContentTest(unittest.TestCase):
    def test_only_h3_takes_references(self):
        """参照つき生成 (r2v) は H3 だけ。ここが緩むと他モデルへ参照を渡してしまう。"""
        by_id = {e["id"]: e for e in model_catalog.catalog()}
        r2v = {k for k, v in by_id.items() if "r2v" in v.get("tasks", [])}
        self.assertEqual(r2v, {"minimax-h3"})

    def test_last_frame_is_limited_to_three_models(self):
        """末尾フレームを置けるのは H3 / wan2.2 / ltx-2.5 だけ(README のとおり)。"""
        by_id = {e["id"]: e for e in model_catalog.catalog()}
        supported = {k for k, v in by_id.items() if v.get("last_frame")}
        self.assertEqual(supported, {"minimax-h3", "wan2.2", "ltx-2.5"})

    def test_only_s2v_is_audio_driven(self):
        by_id = {e["id"]: e for e in model_catalog.catalog()}
        driven = {k for k, v in by_id.items() if v.get("audio_in")}
        self.assertEqual(driven, {"wan2.2-s2v"})

    def test_throughput_says_whether_it_was_measured(self):
        """概算を実測と混ぜない。混ぜると数字の信頼度が分からなくなる。"""
        for entry in model_catalog.catalog():
            if entry["kind"] != "video":
                continue
            rate = entry["seconds_per_output_second"]
            with self.subTest(model=entry["id"]):
                self.assertIn("L4", rate)
                self.assertIn(rate["measured"], (True, False))

    def test_gguf_is_the_fastest_on_an_l4(self):
        """README の結論。逆転したら測り直したか、表を壊したかのどちらか。"""
        rates = {
            e["id"]: e["seconds_per_output_second"]["L4"]
            for e in model_catalog.catalog() if e["kind"] == "video"
        }
        self.assertEqual(min(rates, key=rates.get), "ltx-2.3-gguf")


if __name__ == "__main__":
    unittest.main()
