"""ワークフローの組み立て。ComfyUI へ投げずにグラフとして成立するかを見る。

ComfyUI は壊れたグラフを 400 (prompt_outputs_failed_validation) で弾くが、それが
分かるのは GPU を確保したあと。**参照先の無いノードや ID の衝突はここで落とす。**
"""

from __future__ import annotations

import unittest

import _bootstrap  # noqa: F401

import h3_workflows
import image_workflows
import ltx_workflows
import post_workflows
import video_common
import wan_workflows


def assert_graph(case: unittest.TestCase, wf: dict, save_node_id: str) -> None:
    """ComfyUI の API フォーマットとして最低限成立していることを確かめる。

    - どのノードも class_type と inputs を持つ
    - [node_id, index] の参照先が実在する
    - 保存ノードがあり、そこから全ノードへ遡れる(孤立ノードが無い)
    """
    case.assertTrue(wf, "ワークフローが空")
    for node_id, node in wf.items():
        case.assertIn("class_type", node, node_id)
        case.assertIn("inputs", node, node_id)
        for name, value in node["inputs"].items():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                case.assertIn(value[0], wf, f"{node_id}.{name} の参照先が無い")
                case.assertIsInstance(value[1], int, f"{node_id}.{name}")

    case.assertIn(save_node_id, wf, "保存ノードが無い")

    seen, stack = set(), [save_node_id]
    while stack:
        node_id = stack.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        for value in wf[node_id]["inputs"].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                stack.append(value[0])
    case.assertEqual(set(wf) - seen, set(), "保存ノードから辿れないノードがある")


class H3Test(unittest.TestCase):
    def build(self, **kwargs):
        args = dict(prompt="a cat", width=864, height=480, length=124, seed=1)
        args.update(kwargs)
        return h3_workflows.build_fl2va(**args)

    def test_t2v(self):
        assert_graph(self, self.build(), h3_workflows.SAVE_NODE_ID)

    def test_i2v(self):
        wf = self.build(first_frame="still.png")
        assert_graph(self, wf, h3_workflows.SAVE_NODE_ID)

    def test_first_and_last_frame(self):
        wf = self.build(first_frame="a.png", last_frame="b.png")
        assert_graph(self, wf, h3_workflows.SAVE_NODE_ID)

    def test_ref2va(self):
        wf = h3_workflows.build_ref2va(
            prompt="<Picture 1> が <Audio 1> に合わせて歌う",
            width=768, height=1344, length=124, seed=1,
            ref_images=["sheet.png"], ref_audios=["vocal.wav"],
        )
        assert_graph(self, wf, h3_workflows.SAVE_NODE_ID)

    def test_upscale_rewires_output(self):
        wf = h3_workflows.attach_upscale(self.build(), 1920, 1080, None)
        assert_graph(self, wf, h3_workflows.SAVE_NODE_ID)
        images = wf[h3_workflows.VIDEO_NODE_ID]["inputs"]["images"]
        self.assertNotEqual(images[0], h3_workflows.DECODE_NODE_ID,
                            "拡大を挟んだのに CreateVideo が直結のまま")

    def test_upscale_with_model(self):
        wf = h3_workflows.attach_upscale(self.build(), 1920, 1080, "RealESRGAN_x2.pth")
        assert_graph(self, wf, h3_workflows.SAVE_NODE_ID)

    def test_upscale_skipped_without_target(self):
        wf = h3_workflows.attach_upscale(self.build(), None, None, None)
        self.assertEqual(
            wf[h3_workflows.VIDEO_NODE_ID]["inputs"]["images"],
            [h3_workflows.DECODE_NODE_ID, 0],
        )

    def test_length_stays_on_grid(self):
        for seconds in (0.5, 5.0, 8.0, 15.0, 99.0):
            length = h3_workflows.frame_length(seconds)
            self.assertEqual((length - 5) % 17, 0, seconds)


class WanTest(unittest.TestCase):
    def test_each_model(self):
        for model in wan_workflows.MODELS:
            kind = wan_workflows.MODELS[model]["kind"]
            kwargs = dict(
                model=model, task="i2v", prompt="a cat",
                width=832, height=480, length=81, seed=1, first_frame="still.png",
            )
            if kind == "s2v":
                kwargs["audio"] = "vocal.wav"
            wf = wan_workflows.build(**kwargs)
            assert_graph(self, wf, wan_workflows.SAVE_NODE_ID)

    def test_t2v(self):
        wf = wan_workflows.build(
            model="wan2.2", task="t2v", prompt="a cat",
            width=832, height=480, length=81, seed=1,
        )
        assert_graph(self, wf, wan_workflows.SAVE_NODE_ID)

    def test_last_frame(self):
        wf = wan_workflows.build(
            model="wan2.2", task="i2v", prompt="a cat",
            width=832, height=480, length=81, seed=1,
            first_frame="a.png", last_frame="b.png",
        )
        assert_graph(self, wf, wan_workflows.SAVE_NODE_ID)

    def test_lightning_changes_steps(self):
        common = dict(
            model="wan2.2", task="t2v", prompt="a cat",
            width=832, height=480, length=81, seed=1,
        )
        fast = wan_workflows.build(**common, lightning=True)
        slow = wan_workflows.build(**common, lightning=False)
        self.assertNotEqual(_steps(fast), _steps(slow))

    def test_s2v_requires_audio(self):
        with self.assertRaises(ValueError):
            wan_workflows.build(
                model="wan2.2-s2v", task="i2v", prompt="a cat",
                width=832, height=480, length=77, seed=1, first_frame="a.png",
            )

    def test_unknown_model(self):
        with self.assertRaises(ValueError):
            wan_workflows.build(
                model="wan9.9", task="t2v", prompt="a cat",
                width=832, height=480, length=81, seed=1,
            )

    def test_upscale(self):
        wf = wan_workflows.build(
            model="wan2.2", task="t2v", prompt="a cat",
            width=832, height=480, length=81, seed=1,
        )
        wf = wan_workflows.attach_upscale(wf, 1920, 1080, None)
        assert_graph(self, wf, wan_workflows.SAVE_NODE_ID)


class LtxTest(unittest.TestCase):
    def build(self, **kwargs):
        args = dict(prompt="a cat", width=1280, height=704, length=129, seed=1)
        args.update(kwargs)
        return ltx_workflows.build(**args)

    def test_t2v(self):
        assert_graph(self, self.build(), ltx_workflows.SAVE_NODE_ID)

    def test_i2v(self):
        assert_graph(self, self.build(first_frame="still.png"), ltx_workflows.SAVE_NODE_ID)

    def test_gguf(self):
        assert_graph(self, self.build(gguf=True), ltx_workflows.SAVE_NODE_ID)

    def test_ref_sheet(self):
        wf = self.build(first_frame="still.png", ref_sheet="sheet.png")
        assert_graph(self, wf, ltx_workflows.SAVE_NODE_ID)

    def test_canvas_multiple_is_enforced(self):
        with self.assertRaises(ValueError):
            self.build(width=1290, height=704)

    def test_upscale(self):
        wf = ltx_workflows.attach_upscale(self.build(), 1920, 1088, None)
        assert_graph(self, wf, ltx_workflows.SAVE_NODE_ID)


class ImageTest(unittest.TestCase):
    def test_each_model_and_aspect(self):
        for model in image_workflows.MODELS:
            for aspect in image_workflows.ASPECT_SIZES:
                wf = image_workflows.build_t2i(model, "a cat", aspect=aspect, seed=1)
                assert_graph(self, wf, image_workflows.SAVE_NODE_ID)

    def test_edit(self):
        wf = image_workflows.build_edit("image 1 の猫", ["ref.png"], aspect="9x16", seed=1)
        assert_graph(self, wf, image_workflows.SAVE_NODE_ID)

    def test_edit_three_refs(self):
        wf = image_workflows.build_edit("a", ["1.png", "2.png", "3.png"], seed=1)
        assert_graph(self, wf, image_workflows.SAVE_NODE_ID)

    def test_edit_rejects_too_many_refs(self):
        with self.assertRaises(ValueError):
            image_workflows.build_edit("a", ["1.png", "2.png", "3.png", "4.png"])

    def test_edit_needs_refs(self):
        with self.assertRaises(ValueError):
            image_workflows.build_edit("a", [])

    def test_loras(self):
        wf = image_workflows.build_t2i(
            "qwen-image", "a cat", loras=[("anime.safetensors", 1.0)], seed=1
        )
        assert_graph(self, wf, image_workflows.SAVE_NODE_ID)

    def test_unknown_model(self):
        with self.assertRaises(ValueError):
            image_workflows.build_t2i("dall-e", "a cat")

    def test_edit_output_size_follows_aspect(self):
        """参照の寸法ではなく aspect で出力が決まる(縦のカットが横で出ないこと)。"""
        self.assertEqual(image_workflows.canvas_size("9x16"), (928, 1664))


class PostTest(unittest.TestCase):
    def test_interpolate_and_upscale(self):
        wf = post_workflows.build(
            "clip.mp4", out_fps=24, multiplier=3, target_width=2160, target_height=3840
        )
        assert_graph(self, wf, post_workflows.SAVE_NODE_ID)

    def test_upscale_only(self):
        wf = post_workflows.build(
            "clip.mp4", out_fps=24, multiplier=1, target_width=3840, target_height=2160
        )
        assert_graph(self, wf, post_workflows.SAVE_NODE_ID)

    def test_output_frames(self):
        # 両端は据え置きなので (N-1)*m + 1
        self.assertEqual(post_workflows.output_frames(41, 3), 121)
        self.assertEqual(post_workflows.output_frames(61, 2), 121)
        self.assertEqual(post_workflows.output_frames(41, 1), 41)

    def test_pick_upscale(self):
        # 1080p -> 4K は 2倍なので x2。480p -> 4K は x4
        self.assertEqual(post_workflows.pick_upscale(1920, 1088, 3840, 2160), 2)
        self.assertEqual(post_workflows.pick_upscale(480, 854, 2160, 3840), 4)

    def test_ram_estimate_matches_readme(self):
        """README の見積もり(1920x1088 を x4、120フレームで約 45GB)。"""
        gb = post_workflows.ram_estimate_gb(1920, 1088, 120, scale=4)
        self.assertGreater(gb, 40)
        self.assertLess(gb, 50)
        # x2 なら 1/4 で収まる
        self.assertLess(post_workflows.ram_estimate_gb(1920, 1088, 120, scale=2), 15)

    def test_ram_counts_target_too(self):
        """中間と最終が同時に載るので、目標を渡すと見積もりは増える。"""
        without = post_workflows.ram_estimate_gb(480, 854, 121, scale=4)
        with_target = post_workflows.ram_estimate_gb(
            480, 854, 121, scale=4, target=(2160, 3840)
        )
        self.assertGreater(with_target, without)


class AttachUpscaleTest(unittest.TestCase):
    def test_node_id_collision_is_rejected(self):
        """拡大段の ID が本体と衝突したら黙って上書きせず落とすこと。"""
        wf = {
            "90": {"class_type": "Foo", "inputs": {}},
            "14": {"class_type": "CreateVideo", "inputs": {"images": ["12", 0]}},
            "12": {"class_type": "VAEDecode", "inputs": {}},
        }
        with self.assertRaises(ValueError):
            video_common.attach_upscale(
                wf, 1920, 1080, None,
                images=["12", 0], video_node_id="14", node_prefix="9",
            )


def _steps(wf: dict) -> list:
    return [n["inputs"].get("steps") for n in wf.values() if "steps" in n["inputs"]]


if __name__ == "__main__":
    unittest.main()
