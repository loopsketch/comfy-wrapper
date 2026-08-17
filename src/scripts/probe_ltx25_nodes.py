#!/usr/bin/env python3
"""LTX-2.5 のワークフローが使うノードと入力名が、この ComfyUI に在るかを見る。

    src/scripts/colab.sh exec -s comfy -f src/scripts/probe_ltx25_nodes.py

**ウェイトが無くても走る。** object_info はノードの定義を返すだけなので、
取得に失敗したセッションでもグラフの妥当性だけは確かめられる。投入して
実行時エラーを待つより早く、GPU 時間も食わない。

このスクリプトは colab exec で単体送信されるので、他のスクリプトを import しない。
"""

import json
import urllib.request

COMFY = "http://127.0.0.1:8188"

# ltx25_workflows.py が使うノードと、渡している入力名
EXPECTED = {
    "UNETLoader": ["unet_name", "weight_dtype"],
    "CLIPLoader": ["clip_name", "type", "device"],
    "VAELoader": ["vae_name"],
    "CLIPTextEncode": ["clip", "text"],
    "LTXVConditioning": ["positive", "negative", "frame_rate"],
    "LTXVEmptyLatentAudio": ["frames_number", "frame_rate", "batch_size", "audio_vae"],
    "EmptyLTXVLatentVideo": ["width", "height", "length", "batch_size"],
    "LoadImage": ["image"],
    "ImageScale": ["image", "upscale_method", "width", "height", "crop"],
    "LTXVPreprocess": ["image", "img_compression"],
    "LTXVImgToVideoInplace": ["vae", "image", "latent", "strength", "bypass"],
    "LTXVAddGuide": ["positive", "negative", "vae", "latent", "image", "frame_idx", "strength"],
    "LTXVCropGuides": ["positive", "negative", "latent"],
    "LTXVConcatAVLatent": ["video_latent", "audio_latent"],
    "LTXVSeparateAVLatent": ["av_latent"],
    "LTXVDualCFGGuider": ["model", "positive", "negative", "video_cfg", "audio_cfg"],
    "ManualSigmas": ["sigmas"],
    "KSamplerSelect": ["sampler_name"],
    "SamplerEulerAncestral": ["eta", "s_noise"],
    "RandomNoise": ["noise_seed"],
    "SamplerCustomAdvanced": ["noise", "guider", "sampler", "sigmas", "latent_image"],
    "LatentUpscaleModelLoader": ["model_name"],
    "LTXVLatentUpsampler": ["samples", "upscale_model", "vae"],
    "VAEDecodeTiled": ["samples", "vae", "tile_size", "overlap", "temporal_size", "temporal_overlap"],
    "LTXVAudioVAEDecode": ["samples", "audio_vae"],
    "CreateVideo": ["images", "audio", "fps", "bit_depth"],
    "SaveVideo": ["video", "filename_prefix", "format", "codec"],
}

# 値そのものが選択肢に含まれている必要がある入力 (COMBO)
EXPECTED_CHOICES = {
    ("CLIPLoader", "type"): "ltxv",
    ("KSamplerSelect", "sampler_name"): "euler_ancestral",
}


def main() -> int:
    with urllib.request.urlopen(f"{COMFY}/object_info", timeout=120) as res:
        info = json.load(res)

    missing_nodes, missing_inputs, bad_choices = [], [], []
    for node, names in EXPECTED.items():
        spec = info.get(node)
        if spec is None:
            missing_nodes.append(node)
            continue
        inputs = spec.get("input", {})
        known = set(inputs.get("required", {})) | set(inputs.get("optional", {}))
        for name in names:
            if name not in known:
                missing_inputs.append(f"{node}.{name} (在るのは: {sorted(known)})")

    for (node, name), want in EXPECTED_CHOICES.items():
        spec = info.get(node)
        if spec is None:
            continue
        inputs = spec.get("input", {})
        entry = inputs.get("required", {}).get(name) or inputs.get("optional", {}).get(name)
        options = entry[0] if isinstance(entry, list) and isinstance(entry[0], list) else None
        if options is not None and want not in options:
            bad_choices.append(f"{node}.{name} に {want} が無い (在るのは: {options})")

    for label, items in (
        ("無いノード", missing_nodes),
        ("入力名が違う", missing_inputs),
        ("選択肢に無い", bad_choices),
    ):
        if items:
            print(f"[NG] {label}:")
            for item in items:
                print(f"  - {item}")

    if not (missing_nodes or missing_inputs or bad_choices):
        print(f"[OK] {len(EXPECTED)} ノードすべて、入力名も選択肢も一致しました")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
