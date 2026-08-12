"""ComfyUI API フォーマットのワークフローを組み立てる。

ComfyUI 公式テンプレート (Comfy-Org/workflow_templates の video_minimax_h3_*)
のサブグラフを展開し、API フォーマット(node_id -> class_type/inputs)にしたもの。
"""

from __future__ import annotations

import os

from video_common import ASPECTS, attach_upscale as _attach_upscale, canvas_size as _canvas_size

CANVAS_MULTIPLE = 32
FPS = 24
FRAME_GRID = 17  # H3 は 17k+5 フレームのグリッドに丸められる

# 生成尺の下限/上限(フレーム)。学習レンジは 124-362。
MIN_LENGTH = 124
MAX_LENGTH = 362

MODEL_NAME = "minimax-h3"

# H3 のネイティブキャンバスは短辺768px・最大 768x1344。
# ResolutionSelector 換算では 0.98 megapixels がその上限にあたる。
NATIVE_MAX_MEGAPIXELS = 0.98

DEFAULT_MODELS = {
    "fl2va": os.environ.get(
        "H3_FL2VA_MODEL", "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
    ),
    "ref2va": os.environ.get(
        "H3_REF2VA_MODEL", "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
    ),
    "clip": os.environ.get(
        "H3_TEXT_ENCODER", "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
    ),
    "vae": "minimax_h3_video_vae_fp16.safetensors",
    "audio_vae": "minimax_h3_audio_vae_fp32.safetensors",
}


def canvas_size(aspect: str, megapixels: float) -> tuple[int, int]:
    """アスペクト比と目標画素数から、32の倍数に丸めた width/height を返す。"""
    return _canvas_size(aspect, megapixels, CANVAS_MULTIPLE)


def frame_length(seconds: float) -> int:
    """秒数を H3 の 17k+5 フレームグリッドに切り上げる(24fps)。"""
    length = max(5, round(seconds * FPS))
    length += (5 - (length % FRAME_GRID)) % FRAME_GRID
    return length


def clamp_length(length: int) -> int:
    """学習レンジ内にグリッドを保ったまま収める。"""
    if length < MIN_LENGTH:
        return MIN_LENGTH
    if length > MAX_LENGTH:
        return MAX_LENGTH - ((MAX_LENGTH - 5) % FRAME_GRID)
    return length


def _base_nodes(
    unet: str, filename_prefix: str, steps: int, seed: int, scheduler: str = "simple"
) -> dict:
    """ローダ〜保存までの共通部分。conditioning/latent は呼び出し側が繋ぐ。

    scheduler は公式テンプレートが simple。ただし MiniMax の公式ドキュメントは
    参照の多いプロンプトで beta / normal の方が良好としているので、
    ref2v 側では beta を既定にしている。
    """
    return {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": unet, "weight_dtype": "default"},
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": DEFAULT_MODELS["clip"],
                "type": "minimax",
                "device": "default",
            },
        },
        "3": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": DEFAULT_MODELS["vae"]},
        },
        "4": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": DEFAULT_MODELS["audio_vae"]},
        },
        "6": {
            "class_type": "BasicGuider",
            "inputs": {"model": ["1", 0], "conditioning": ["5", 0]},
        },
        "7": {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": "res_multistep"},
        },
        "8": {
            "class_type": "BasicScheduler",
            "inputs": {
                "model": ["1", 0],
                "scheduler": scheduler,
                "steps": steps,
                "denoise": 1.0,
            },
        },
        "9": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "11": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["9", 0],
                "guider": ["6", 0],
                "sampler": ["7", 0],
                "sigmas": ["8", 0],
                "latent_image": ["5", 1],
            },
        },
        "12": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["11", 0], "vae": ["3", 0]},
        },
        "13": {
            "class_type": "VAEDecodeAudio",
            "inputs": {"samples": ["11", 0], "vae": ["4", 0]},
        },
        "14": {
            "class_type": "CreateVideo",
            "inputs": {
                "images": ["12", 0],
                "audio": ["13", 0],
                "fps": FPS,
                "bit_depth": 8,
            },
        },
        "15": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["14", 0],
                "filename_prefix": filename_prefix,
                "format": "auto",
                "codec": "auto",
            },
        },
    }


def build_fl2va(
    prompt: str,
    width: int,
    height: int,
    length: int,
    seed: int,
    steps: int = 20,
    first_frame: str | None = None,
    last_frame: str | None = None,
    filename_prefix: str = "video/cw_h3",
    scheduler: str = "simple",
) -> dict:
    """t2v / i2v / 先頭・末尾フレーム指定(fl2va モデル)のワークフロー。"""
    wf = _base_nodes(DEFAULT_MODELS["fl2va"], filename_prefix, steps, seed, scheduler)
    inputs: dict = {
        "clip": ["2", 0],
        "vae": ["3", 0],
        "prompt": prompt,
        "width": width,
        "height": height,
        "length": length,
    }
    if first_frame:
        wf["20"] = {"class_type": "LoadImage", "inputs": {"image": first_frame}}
        inputs["first_frame"] = ["20", 0]
    if last_frame:
        wf["21"] = {"class_type": "LoadImage", "inputs": {"image": last_frame}}
        inputs["last_frame"] = ["21", 0]
    wf["5"] = {"class_type": "MiniMaxH3ImageToVideo", "inputs": inputs}
    return wf


def build_ref2va(
    prompt: str,
    width: int,
    height: int,
    length: int,
    seed: int,
    steps: int = 20,
    ref_images: list[str] | None = None,
    ref_videos: list[str] | None = None,
    ref_audios: list[str] | None = None,
    ref_image_size: str = "match",
    filename_prefix: str = "video/cw_h3",
    scheduler: str = "beta",
) -> dict:
    """参照画像・参照動画・参照音声つき(ref2va モデル)のワークフロー。

    プロンプト中では 1 始まりの <Picture i> / <Video k> / <Audio j> で参照を指す。
    参照動画は自身のサウンドトラックも同じ番号の <Audio> として渡す。
    scheduler の既定が beta なのは、公式ドキュメントが参照の多いプロンプトで
    simple より良好としているため。
    """
    wf = _base_nodes(DEFAULT_MODELS["ref2va"], filename_prefix, steps, seed, scheduler)
    inputs: dict = {
        "clip": ["2", 0],
        "vae": ["3", 0],
        "audio_vae": ["4", 0],
        "prompt": prompt,
        "width": width,
        "height": height,
        "length": length,
        "ref_image_size": ref_image_size,
    }
    for i, name in enumerate(ref_images or []):
        node_id = str(30 + i)
        wf[node_id] = {"class_type": "LoadImage", "inputs": {"image": name}}
        inputs[f"ref_images.ref_image_{i}"] = [node_id, 0]
    for i, name in enumerate(ref_videos or []):
        load_id, split_id = str(40 + i * 2), str(41 + i * 2)
        wf[load_id] = {"class_type": "LoadVideo", "inputs": {"file": name}}
        wf[split_id] = {
            "class_type": "GetVideoComponents",
            "inputs": {"video": [load_id, 0]},
        }
        inputs[f"ref_videos.ref_video_{i}"] = [split_id, 0]
        inputs[f"ref_video_audios.ref_video_audio_{i}"] = [split_id, 1]
    for i, name in enumerate(ref_audios or []):
        node_id = str(50 + i)
        wf[node_id] = {"class_type": "LoadAudio", "inputs": {"audio": name}}
        inputs[f"ref_audios.ref_audio_{i}"] = [node_id, 0]
    wf["5"] = {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": inputs}
    return wf


SAVE_NODE_ID = "15"
DECODE_NODE_ID = "12"
VIDEO_NODE_ID = "14"


def attach_upscale(
    wf: dict,
    target_width: int | None,
    target_height: int | None,
    model_name: str | None = None,
) -> dict:
    """VAEDecode と CreateVideo の間に拡大を挟み、出力を目標サイズちょうどにする。

    H3 のネイティブキャンバス(最大 1344x768)は 16:9 ちょうどではないため、
    1920x1080 のような厳密なサイズが要る場合は center crop でアスペクト差を吸収する。
    """
    return _attach_upscale(
        wf,
        target_width,
        target_height,
        model_name,
        images=[DECODE_NODE_ID, 0],
        video_node_id=VIDEO_NODE_ID,
        node_prefix="6",
    )
