"""寸法と尺の計算。GPU も Colab も要らない純粋な計算だけを見る。"""

from __future__ import annotations

import unittest

import _bootstrap  # noqa: F401

import video_common
from lib.video_sizes import MODEL_RESOLUTIONS, output_size


class CanvasSizeTest(unittest.TestCase):
    def test_readme_table(self):
        """README の megapixels -> 実サイズの表と一致すること。"""
        self.assertEqual(video_common.canvas_size("16x9", 0.2), (608, 352))
        self.assertEqual(video_common.canvas_size("16x9", 0.4), (864, 480))
        self.assertEqual(video_common.canvas_size("16x9", 0.9), (1280, 736))
        self.assertEqual(video_common.canvas_size("16x9", 0.98), (1344, 768))
        self.assertEqual(video_common.canvas_size("9x16", 0.4), (480, 864))
        self.assertEqual(video_common.canvas_size("9x16", 0.98), (768, 1344))

    def test_multiple(self):
        """LTX は 64 の倍数が要る。"""
        for aspect in video_common.ASPECTS:
            width, height = video_common.canvas_size(aspect, 0.4, multiple=64)
            self.assertEqual((width % 64, height % 64), (0, 0), aspect)

    def test_never_zero(self):
        width, height = video_common.canvas_size("21x9", 0.01)
        self.assertGreaterEqual(min(width, height), 32)

    def test_unknown_aspect(self):
        with self.assertRaises(ValueError):
            video_common.canvas_size("5x4", 0.4)


class GridLengthTest(unittest.TestCase):
    def test_readme_table(self):
        """5秒がモデルごとに何フレームに丸められるか(README の表)。"""
        # H3: 17k+5 @24fps -> 5.17秒 124F
        self.assertEqual(video_common.grid_length(5.0, 24, 17, 5, 124, 362), 124)
        # Wan 14B: 4k+1 @16fps -> 5.06秒 81F
        self.assertEqual(video_common.grid_length(5.0, 16, 4, 1, 5, 161), 81)
        # Wan 5B: 4k+1 @24fps -> 5.04秒 121F
        self.assertEqual(video_common.grid_length(5.0, 24, 4, 1, 5, 241), 121)
        # LTX: 8k+1 @25fps -> 5.16秒 129F
        self.assertEqual(video_common.grid_length(5.0, 25, 8, 1, 9, 401), 129)

    def test_always_on_grid(self):
        for seconds in (0.2, 1.0, 3.3, 5.0, 7.5, 12.0, 30.0):
            length = video_common.grid_length(seconds, 24, 17, 5, 124, 362)
            self.assertEqual((length - 5) % 17, 0, seconds)
            self.assertGreaterEqual(length, 124)
            self.assertLessEqual(length, 362)

    def test_rounds_up(self):
        """指定した秒数より短くならない(上限に当たる場合を除く)。"""
        self.assertGreaterEqual(video_common.grid_length(5.1, 25, 8, 1), 128)

    def test_max_stays_on_grid(self):
        length = video_common.grid_length(999.0, 16, 4, 1, 5, 161)
        self.assertEqual(length, 161)
        self.assertEqual((length - 1) % 4, 0)


class OutputSizeTest(unittest.TestCase):
    def test_readme_table(self):
        self.assertEqual(output_size("16x9", 480), (854, 480))
        self.assertEqual(output_size("16x9", 720), (1280, 720))
        self.assertEqual(output_size("16x9", 1080), (1920, 1080))
        self.assertEqual(output_size("9x16", 1080), (1080, 1920))

    def test_even(self):
        """h264 は奇数寸法を扱えない。"""
        for aspect in ("16x9", "9x16", "1x1", "4x3", "3x4", "21x9"):
            for short_edge in (480, 720, 1080):
                width, height = output_size(aspect, short_edge)
                self.assertEqual((width % 2, height % 2), (0, 0), (aspect, short_edge))

    def test_short_edge_is_short(self):
        for aspect in ("16x9", "9x16", "4x3", "3x4", "21x9"):
            width, height = output_size(aspect, 720)
            self.assertEqual(min(width, height), 720, aspect)


class ModelResolutionsTest(unittest.TestCase):
    def test_all_models_have_three_steps(self):
        for model, table in MODEL_RESOLUTIONS.items():
            self.assertEqual(set(table), {"480p", "720p", "1080p"}, model)
            for name, (megapixels, short_edge) in table.items():
                self.assertGreater(megapixels, 0, (model, name))
                self.assertIn(short_edge, (480, 720, 1080), (model, name))


if __name__ == "__main__":
    unittest.main()
