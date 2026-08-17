"""LTX-2.5 の ComfyUI ワークフロー(API フォーマット)を組み立てる。

ComfyUI 公式テンプレート (`video_ltx2_5_t2v` / `_i2v` / `_flf2v`) のサブグラフを
展開したもの。ノード構成・入力名は公式実装 (`comfy_extras/nodes_lt*.py`) に合わせてある。

**2.3 とは別モジュールにしてある。** 2段構えの骨格とシグマ列は 2.3 と同じだが、
ローダ・ガイダ・サンプラ・デコードが総取っ替えになっていて、分岐で相乗りさせると
どちらの構成なのか読めなくなる。参照シート (IC-LoRA Ingredients) は 2.5 版が
まだ無いので、その経路は `ltx_workflows.py` (2.3) に残してある。

2.3 からの主な違い:

- チェックポイント1本ではなく `UNETLoader` + `CLIPLoader` + `VAELoader` x2。
  接続子はテキストエンコーダ (Gemma 4 12B with-proj) に入っており、別ファイルが要らない
- 蒸留済みのウェイトが本体として配られるので、蒸留 LoRA を積まない
- ガイダが `LTXVDualCFGGuider`。映像と音声に別々の CFG をかけられる(既定はどちらも 1.0)
- サンプラが `euler_ancestral`、デコードのタイルが 512/64/64/16、既定 fps が 24

生成は t2v / i2v が2段構え。前半は目標の**半分**の解像度で 8step 回し、latent のまま
x2 の空間アップサンプラを通してから、後半 3step で仕上げる。

**flf2v だけは構成が別。** 最初と最後のフレームを `LTXVAddGuide` で置く単パスで、
フル解像度のまま 8step 回してアップサンプラを通さない(公式テンプレートがそうしている)。
"""

from __future__ import annotations

import os

from video_common import ASPECTS, attach_upscale as _attach_upscale, canvas_size, grid_length  # noqa: F401

MODEL_NAME = "ltx-2.5"

# int8 convrot 版。bf16 は 42GB あって 1セッションに収まらない。
# dev と distilled はどちらも 21.5GB で、テンプレートの既定は distilled
UNET = os.environ.get(
    "LTX25_UNET", "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors"
)
# Gemma 4 12B に LTX 側の投影が入ったもの。2.3 の接続子 (別ファイル) はこれに畳まれた
TEXT_ENCODER = os.environ.get(
    "LTX25_TEXT_ENCODER", "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors"
)
VIDEO_VAE = os.environ.get("LTX25_VIDEO_VAE", "ltx-2.5-video-vae-bf16.safetensors")
# 2.3 では専用ローダが checkpoints/ から読んでいたが、2.5 は素の VAELoader で通る
# (LTXVAudioVAEDecode / LTXVEmptyLatentAudio の入力型が VAE のため)
AUDIO_VAE = os.environ.get("LTX25_AUDIO_VAE", "ltx-2.5-audio-vae-bf16.safetensors")
UPSCALER = os.environ.get(
    "LTX25_UPSCALER", "ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors"
)

# 前半(半解像度)と後半(x2 アップサンプル後)のシグマ列。テンプレートのまま。
# flf2v は単パスで前半のぶんだけを使う
SIGMAS_COARSE = "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"
SIGMAS_REFINE = "0.85, 0.7250, 0.4219, 0.0"

SAMPLER = "euler_ancestral"
# flf2v の SamplerEulerAncestral のみ eta / s_noise を明示する
FLF_ETA = 0.0
FLF_S_NOISE = 1.0

# 映像と音声で別々にかけられる。テンプレートはどちらも 1.0(= 蒸留ウェイト前提)
VIDEO_CFG = 1.0
AUDIO_CFG = 1.0

# 仕上げのノイズはテンプレートが固定値。seed を変えても構図は前半で決まる
REFINE_SEED = 42

DEFAULT_FPS = 24
FRAME_GRID = 8  # LTX は 8k+1 フレーム
MIN_LENGTH = 9
MAX_LENGTH = 401  # 24fps で 16.7秒。伸ばすほど VRAM と時間が効く

# **公式テンプレートの倍数は 32 だが、ここは 64 にしてある。** 前半を半解像度で
# 回すので、32 で丸めると半分が 16 の倍数にしかならない。緩められるかは実測で
# 確かめること (issue #1)
CANVAS_MULTIPLE = 64

# 画像を渡すときの「どれくらい元画像に従わせるか」。テンプレートは前半 0.7 / 後半 1.0
FIRST_FRAME_STRENGTH = 0.7
# flf2v は最初も最後も 0.7。単パスなので後半で締め直す機会が無い
GUIDE_STRENGTH = 0.7

LTX_NEGATIVE = "pc game, console game, video game, cartoon, childish, ugly"

# 参照ではなく「フレーム0」として画像を渡すので、圧縮を軽くかけて生成側に馴染ませる
IMG_COMPRESSION = 18

# デコードのタイル。2.3 の 768/64/4096/4 から変わっている
TILE_SIZE = 512
TILE_OVERLAP = 64
TEMPORAL_SIZE = 64
TEMPORAL_OVERLAP = 16

SAVE_NODE_ID = "54"
DECODE_NODE_ID = "51"
VIDEO_NODE_ID = "53"

TASKS = {"t2v", "i2v", "flf2v"}


def canvas(aspect: str, megapixels: float) -> tuple[int, int]:
    return canvas_size(aspect, megapixels, CANVAS_MULTIPLE)


def frame_length(seconds: float, fps: int = DEFAULT_FPS) -> int:
    return grid_length(
        seconds, fps, FRAME_GRID, offset=1, min_length=MIN_LENGTH, max_length=MAX_LENGTH
    )


def _loaders() -> dict:
    """本体・テキストエンコーダ・映像 VAE・音声 VAE のローダ。

    2.3 のようなチェックポイント1本ではなく、4つに分かれている。
    """
    return {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": UNET, "weight_dtype": "default"},
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": TEXT_ENCODER, "type": "ltxv", "device": "default"},
        },
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": AUDIO_VAE}},
        "10": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_VAE}},
    }


def _image_input(
    wf: dict, ids: tuple[str, str, str], image: str, width: int, height: int
) -> list:
    """LoadImage → ImageScale → LTXVPreprocess を置いて、出力の参照を返す。

    `LTXVImgToVideoInplace` は latent の寸法に合わせて内部で拡縮するが、そちらは
    bilinear なので、狙いのサイズには lanczos で先に寄せておく。
    """
    load, scale, pre = ids
    wf[load] = {"class_type": "LoadImage", "inputs": {"image": image}}
    wf[scale] = {
        "class_type": "ImageScale",
        "inputs": {
            "image": [load, 0],
            "upscale_method": "lanczos",
            "width": width,
            "height": height,
            "crop": "center",
        },
    }
    wf[pre] = {
        "class_type": "LTXVPreprocess",
        "inputs": {"image": [scale, 0], "img_compression": IMG_COMPRESSION},
    }
    return [pre, 0]


def _tail(wf: dict, video_latent: list, audio_latent: list, fps: int, filename_prefix: str) -> None:
    """decode から SaveVideo までの共通部分。"""
    wf[DECODE_NODE_ID] = {
        "class_type": "VAEDecodeTiled",
        "inputs": {
            "samples": video_latent,
            "vae": ["10", 0],
            "tile_size": TILE_SIZE,
            "overlap": TILE_OVERLAP,
            "temporal_size": TEMPORAL_SIZE,
            "temporal_overlap": TEMPORAL_OVERLAP,
        },
    }
    wf["52"] = {
        "class_type": "LTXVAudioVAEDecode",
        "inputs": {"samples": audio_latent, "audio_vae": ["3", 0]},
    }
    wf[VIDEO_NODE_ID] = {
        "class_type": "CreateVideo",
        "inputs": {
            "images": [DECODE_NODE_ID, 0],
            "audio": ["52", 0],
            "fps": fps,
            "bit_depth": 8,
        },
    }
    wf[SAVE_NODE_ID] = {
        "class_type": "SaveVideo",
        "inputs": {
            "video": [VIDEO_NODE_ID, 0],
            "filename_prefix": filename_prefix,
            "format": "auto",
            "codec": "auto",
        },
    }


def build(
    prompt: str,
    width: int,
    height: int,
    length: int,
    seed: int,
    fps: int = DEFAULT_FPS,
    negative: str | None = None,
    first_frame: str | None = None,
    last_frame: str | None = None,
    filename_prefix: str = "video/cw_ltx25",
) -> dict:
    """LTX-2.5 のワークフローを返す。

    - first_frame も last_frame も無ければ t2v
    - first_frame だけなら i2v(2段構え。width / height は**最終**の解像度で、前半は半分)
    - 両方あれば flf2v(単パス・フル解像度)

    last_frame だけを渡すことはできない。最後のフレームは最初のフレームからの
    到達点として置くもので、始点が無いと `LTXVAddGuide` の frame_idx=-1 が
    何に対する終わりなのか決まらない。
    """
    if width % CANVAS_MULTIPLE or height % CANVAS_MULTIPLE:
        raise ValueError(f"width/height は {CANVAS_MULTIPLE} の倍数にしてください")
    if last_frame and not first_frame:
        raise ValueError("last_frame を使うときは first_frame も渡してください")

    wf = _loaders()
    wf.update({
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["2", 0], "text": LTX_NEGATIVE if negative is None else negative},
        },
        "7": {
            "class_type": "LTXVConditioning",
            "inputs": {"positive": ["5", 0], "negative": ["6", 0], "frame_rate": fps},
        },
        "32": {
            "class_type": "LTXVEmptyLatentAudio",
            "inputs": {
                "frames_number": length,
                "frame_rate": fps,
                "batch_size": 1,
                "audio_vae": ["3", 0],
            },
        },
    })

    if last_frame:
        _build_flf2v(wf, width, height, length, seed, fps, first_frame, last_frame, filename_prefix)
    else:
        _build_two_pass(wf, width, height, length, seed, fps, first_frame, filename_prefix)
    return wf


def _build_two_pass(
    wf: dict,
    width: int,
    height: int,
    length: int,
    seed: int,
    fps: int,
    first_frame: str | None,
    filename_prefix: str,
) -> None:
    """t2v / i2v。半解像度で構図と音を決め、x2 に上げてから仕上げる。"""
    image_source = (
        _image_input(wf, ("20", "21", "22"), first_frame, width, height) if first_frame else None
    )

    # --- 前半: 半解像度 ---
    wf["30"] = {
        "class_type": "EmptyLTXVLatentVideo",
        "inputs": {"width": width // 2, "height": height // 2, "length": length, "batch_size": 1},
    }
    video_latent = ["30", 0]
    if image_source:
        wf["31"] = {
            "class_type": "LTXVImgToVideoInplace",
            "inputs": {
                "vae": ["10", 0],
                "image": image_source,
                "latent": ["30", 0],
                "strength": FIRST_FRAME_STRENGTH,
                "bypass": False,
            },
        }
        video_latent = ["31", 0]

    wf["33"] = {
        "class_type": "LTXVConcatAVLatent",
        "inputs": {"video_latent": video_latent, "audio_latent": ["32", 0]},
    }
    wf["34"] = {"class_type": "ManualSigmas", "inputs": {"sigmas": SIGMAS_COARSE}}
    wf["35"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": SAMPLER}}
    wf["36"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}}
    wf["37"] = {
        "class_type": "LTXVDualCFGGuider",
        "inputs": {
            "model": ["1", 0],
            "positive": ["7", 0],
            "negative": ["7", 1],
            "video_cfg": VIDEO_CFG,
            "audio_cfg": AUDIO_CFG,
        },
    }
    wf["38"] = {
        "class_type": "SamplerCustomAdvanced",
        "inputs": {
            "noise": ["36", 0],
            "guider": ["37", 0],
            "sampler": ["35", 0],
            "sigmas": ["34", 0],
            "latent_image": ["33", 0],
        },
    }
    wf["39"] = {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["38", 0]}}

    # --- 後半: latent のまま x2 に上げて仕上げる ---
    wf["41"] = {"class_type": "LatentUpscaleModelLoader", "inputs": {"model_name": UPSCALER}}
    wf["42"] = {
        "class_type": "LTXVLatentUpsampler",
        "inputs": {"samples": ["39", 0], "upscale_model": ["41", 0], "vae": ["10", 0]},
    }
    refined_latent = ["42", 0]
    if image_source:
        wf["43"] = {
            "class_type": "LTXVImgToVideoInplace",
            "inputs": {
                "vae": ["10", 0],
                "image": image_source,
                "latent": ["42", 0],
                "strength": 1.0,
                "bypass": False,
            },
        }
        refined_latent = ["43", 0]

    wf["44"] = {
        "class_type": "LTXVConcatAVLatent",
        "inputs": {"video_latent": refined_latent, "audio_latent": ["39", 1]},
    }
    wf["45"] = {"class_type": "ManualSigmas", "inputs": {"sigmas": SIGMAS_REFINE}}
    wf["46"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": SAMPLER}}
    wf["47"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": REFINE_SEED}}
    wf["48"] = {
        "class_type": "LTXVDualCFGGuider",
        "inputs": {
            "model": ["1", 0],
            "positive": ["7", 0],
            "negative": ["7", 1],
            "video_cfg": VIDEO_CFG,
            "audio_cfg": AUDIO_CFG,
        },
    }
    wf["49"] = {
        "class_type": "SamplerCustomAdvanced",
        "inputs": {
            "noise": ["47", 0],
            "guider": ["48", 0],
            "sampler": ["46", 0],
            "sigmas": ["45", 0],
            "latent_image": ["44", 0],
        },
    }
    wf["50"] = {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["49", 0]}}
    _tail(wf, ["50", 0], ["50", 1], fps, filename_prefix)


def _build_flf2v(
    wf: dict,
    width: int,
    height: int,
    length: int,
    seed: int,
    fps: int,
    first_frame: str,
    last_frame: str,
    filename_prefix: str,
) -> None:
    """最初と最後のフレームを置いて、その間を埋める。単パス・フル解像度。

    ガイドとして置いたフレームは latent の後ろに積まれるので、サンプリングのあと
    `LTXVCropGuides` で落としてからデコードする(落とさないとガイドぶんまで
    映像として出てしまう)。
    """
    first = _image_input(wf, ("20", "21", "22"), first_frame, width, height)
    last = _image_input(wf, ("25", "26", "27"), last_frame, width, height)

    wf["30"] = {
        "class_type": "EmptyLTXVLatentVideo",
        "inputs": {"width": width, "height": height, "length": length, "batch_size": 1},
    }
    wf["28"] = {
        "class_type": "LTXVAddGuide",
        "inputs": {
            "positive": ["7", 0],
            "negative": ["7", 1],
            "vae": ["10", 0],
            "latent": ["30", 0],
            "image": first,
            "frame_idx": 0,
            "strength": GUIDE_STRENGTH,
        },
    }
    wf["29"] = {
        "class_type": "LTXVAddGuide",
        "inputs": {
            "positive": ["28", 0],
            "negative": ["28", 1],
            "vae": ["10", 0],
            "latent": ["28", 2],
            "image": last,
            "frame_idx": -1,
            "strength": GUIDE_STRENGTH,
        },
    }
    wf["33"] = {
        "class_type": "LTXVConcatAVLatent",
        "inputs": {"video_latent": ["29", 2], "audio_latent": ["32", 0]},
    }
    wf["34"] = {"class_type": "ManualSigmas", "inputs": {"sigmas": SIGMAS_COARSE}}
    wf["35"] = {
        "class_type": "SamplerEulerAncestral",
        "inputs": {"eta": FLF_ETA, "s_noise": FLF_S_NOISE},
    }
    wf["36"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}}
    wf["37"] = {
        "class_type": "LTXVDualCFGGuider",
        "inputs": {
            "model": ["1", 0],
            "positive": ["29", 0],
            "negative": ["29", 1],
            "video_cfg": VIDEO_CFG,
            "audio_cfg": AUDIO_CFG,
        },
    }
    wf["38"] = {
        "class_type": "SamplerCustomAdvanced",
        "inputs": {
            "noise": ["36", 0],
            "guider": ["37", 0],
            "sampler": ["35", 0],
            "sigmas": ["34", 0],
            "latent_image": ["33", 0],
        },
    }
    wf["39"] = {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["38", 0]}}
    wf["40"] = {
        "class_type": "LTXVCropGuides",
        "inputs": {"positive": ["29", 0], "negative": ["29", 1], "latent": ["39", 0]},
    }
    _tail(wf, ["40", 2], ["39", 1], fps, filename_prefix)


def attach_upscale(
    wf: dict,
    target_width: int | None,
    target_height: int | None,
    model_name: str | None = None,
) -> dict:
    return _attach_upscale(
        wf,
        target_width,
        target_height,
        model_name,
        images=[DECODE_NODE_ID, 0],
        video_node_id=VIDEO_NODE_ID,
        node_prefix="6",
    )
