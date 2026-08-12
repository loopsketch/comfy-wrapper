"""Wan2.2 の ComfyUI ワークフロー(API フォーマット)を組み立てる。

ComfyUI 公式テンプレート (`video_wan2_2_14B_t2v` / `_i2v` / `_flf2v` /
`video_wan2_2_5B_ti2v`) のサブグラフを展開したもの。ノード構成・入力名は
公式実装 (`comfy_extras/nodes_wan.py`) に合わせてある。

**H3 と違って音声は出ない。** 映像だけなので、歌唱や環境音は assemble 側で載せる。

14B は high-noise と low-noise の2本立て(MoE)で、KSamplerAdvanced を
split_step で分けて前半を high、後半を low に通す。lightx2v の 4steps LoRA を
積むと 20step が 4step になり、実測で 5倍前後速くなる代わりに動きは大人しくなる。
"""

from __future__ import annotations

import os

from video_common import ASPECTS, attach_upscale as _attach_upscale, canvas_size, grid_length  # noqa: F401

# 公式テンプレートが置いている既定のネガティブ(中国語)。
# 過飽和・静止画然とした画・破綻した手足・字幕の写り込みを避ける指示。
WAN_NEGATIVE = (
    "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，"
    "整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，"
    "画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，"
    "静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走"
)

TEXT_ENCODER = os.environ.get(
    "WAN_TEXT_ENCODER", "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
)

MODELS: dict[str, dict] = {
    # 14B MoE。t2v と i2v でウェイトが別なので、タスクで読み分ける
    "wan2.2": {
        "kind": "14b",
        "fps": 16,
        "canvas_multiple": 16,
        "max_length": 161,  # 10秒。学習は 81 フレーム(5秒)なので伸ばすほど崩れる
        "vae": "wan_2.1_vae.safetensors",
        "unets": {
            "t2v": (
                "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
                "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
            ),
            "i2v": (
                "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
                "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
            ),
        },
        "loras": {
            "t2v": (
                "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
                "wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors",
            ),
            "i2v": (
                "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
                "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
            ),
        },
    },
    # S2V-14B。音声で口と動きを駆動する。**画像は先頭フレームではなく参照**で、
    # 構図はモデルが作り直す。1チャンク 77フレーム(16fps で 4.81秒)が上限で、
    # それより長い尺は前チャンクを引き継ぐループが要る(未実装)
    "wan2.2-s2v": {
        "kind": "s2v",
        "fps": 16,
        "canvas_multiple": 16,
        "max_length": 77,
        "unet": "wan2.2_s2v_14B_fp8_scaled.safetensors",
        "vae": "wan_2.1_vae.safetensors",
        "audio_encoder": "wav2vec2_large_english_fp16.safetensors",
        # 公式テンプレートは S2V にも t2v 用の 4steps LoRA を積む(high noise 側)
        "lora": "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
        "shift": 8.0,
        "steps": 20,
        "cfg": 6.0,
        "sampler": "uni_pc",
    },
    # TI2V-5B。1本のウェイトで t2v も i2v も回る軽量枠。24fps・1280x704 が素の解像度
    "wan2.2-5b": {
        "kind": "5b",
        "fps": 24,
        "canvas_multiple": 32,
        "max_length": 241,
        "unet": "wan2.2_ti2v_5B_fp16.safetensors",
        "vae": "wan2.2_vae.safetensors",
        "shift": 8.0,
        "steps": 20,
        "cfg": 5.0,
        "sampler": "uni_pc",
    },
}

FRAME_GRID = 4  # Wan は 4k+1 フレーム
MIN_LENGTH = 5

# 蒸留 LoRA を積むかどうかで、step 数・cfg・切り替え位置がまとめて変わる
LIGHTNING = {"steps": 4, "cfg": 1.0, "split": 2, "shift": 5.0}
FULL = {"steps": 20, "cfg": 3.5, "split": 10, "shift": 5.0}
# 先頭・末尾フレーム指定のときだけ、テンプレートは cfg と shift を上げている
FULL_FLF = {"steps": 20, "cfg": 4.0, "split": 10, "shift": 8.0}

SAVE_NODE_ID = "16"
DECODE_NODE_ID = "14"
VIDEO_NODE_ID = "15"

# ウェイトの有無を見るための (ノード, 入力名, ファイル名)
READY_ASSET = {
    "wan2.2": ("UNETLoader", "unet_name", "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"),
    "wan2.2-5b": ("UNETLoader", "unet_name", "wan2.2_ti2v_5B_fp16.safetensors"),
}

TASKS = {"t2v", "i2v"}


def fps(model: str) -> int:
    return MODELS[model]["fps"]


def canvas(model: str, aspect: str, megapixels: float) -> tuple[int, int]:
    return canvas_size(aspect, megapixels, MODELS[model]["canvas_multiple"])


def frame_length(model: str, seconds: float) -> int:
    cfg = MODELS[model]
    return grid_length(
        seconds,
        cfg["fps"],
        FRAME_GRID,
        offset=1,
        min_length=MIN_LENGTH,
        max_length=cfg["max_length"],
    )


def _preset(model: str, last_frame: str | None, lightning: bool) -> dict:
    if MODELS[model]["kind"] == "5b":
        cfg = MODELS[model]
        return {"steps": cfg["steps"], "cfg": cfg["cfg"], "shift": cfg["shift"]}
    if lightning:
        return dict(LIGHTNING)
    return dict(FULL_FLF if last_frame else FULL)


def build(
    model: str,
    task: str,
    prompt: str,
    width: int,
    height: int,
    length: int,
    seed: int,
    negative: str | None = None,
    steps: int | None = None,
    first_frame: str | None = None,
    last_frame: str | None = None,
    audio: str | None = None,
    lightning: bool = True,
    filename_prefix: str = "video/mvc_wan",
) -> dict:
    """Wan2.2 のワークフローを返す。first_frame があれば i2v、無ければ t2v。"""
    if model not in MODELS:
        raise ValueError(f"未知のモデル: {model}")
    if MODELS[model]["kind"] == "s2v":
        if not audio:
            raise ValueError("wan2.2-s2v には音声が必要です")
        return _build_s2v(
            model, prompt, width, height, length, seed,
            negative=negative, steps=steps, ref_image=first_frame, audio=audio,
            lightning=lightning, filename_prefix=filename_prefix,
        )
    if MODELS[model]["kind"] == "5b":
        return _build_5b(
            model, prompt, width, height, length, seed,
            negative=negative, steps=steps, first_frame=first_frame,
            filename_prefix=filename_prefix,
        )
    return _build_14b(
        model, prompt, width, height, length, seed,
        negative=negative, steps=steps, first_frame=first_frame,
        last_frame=last_frame, lightning=lightning, filename_prefix=filename_prefix,
    )


def _build_14b(
    model: str,
    prompt: str,
    width: int,
    height: int,
    length: int,
    seed: int,
    negative: str | None,
    steps: int | None,
    first_frame: str | None,
    last_frame: str | None,
    lightning: bool,
    filename_prefix: str,
) -> dict:
    cfg = MODELS[model]
    weights = "i2v" if (first_frame or last_frame) else "t2v"
    high, low = cfg["unets"][weights]
    lora_high, lora_low = cfg["loras"][weights]
    preset = _preset(model, last_frame, lightning)
    total_steps = steps or preset["steps"]
    # split はテンプレートの比(4step なら 2、20step なら 10)を保って追従させる
    split = max(1, round(total_steps * preset["split"] / preset["steps"]))

    wf: dict = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": high, "weight_dtype": "default"}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": low, "weight_dtype": "default"}},
        "3": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": TEXT_ENCODER, "type": "wan", "device": "default"},
        },
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": cfg["vae"]}},
    }

    high_id, low_id = "1", "2"
    if lightning:
        wf["5"] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"model": ["1", 0], "lora_name": lora_high, "strength_model": 1.0},
        }
        wf["6"] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"model": ["2", 0], "lora_name": lora_low, "strength_model": 1.0},
        }
        high_id, low_id = "5", "6"

    wf["7"] = {
        "class_type": "ModelSamplingSD3",
        "inputs": {"model": [high_id, 0], "shift": preset["shift"]},
    }
    wf["8"] = {
        "class_type": "ModelSamplingSD3",
        "inputs": {"model": [low_id, 0], "shift": preset["shift"]},
    }
    wf["9"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": prompt}}
    wf["10"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"clip": ["3", 0], "text": WAN_NEGATIVE if negative is None else negative},
    }

    if first_frame or last_frame:
        inputs: dict = {
            "positive": ["9", 0],
            "negative": ["10", 0],
            "vae": ["4", 0],
            "width": width,
            "height": height,
            "length": length,
            "batch_size": 1,
        }
        if first_frame:
            wf["20"] = {"class_type": "LoadImage", "inputs": {"image": first_frame}}
            inputs["start_image"] = ["20", 0]
        if last_frame:
            wf["21"] = {"class_type": "LoadImage", "inputs": {"image": last_frame}}
            inputs["end_image"] = ["21", 0]
        # 末尾フレームを渡すときだけ専用ノードに替える(入力名も start/end で変わる)
        wf["11"] = {
            "class_type": "WanFirstLastFrameToVideo" if last_frame else "WanImageToVideo",
            "inputs": inputs,
        }
        positive, negative_cond, latent = ["11", 0], ["11", 1], ["11", 2]
    else:
        wf["11"] = {
            "class_type": "EmptyHunyuanLatentVideo",
            "inputs": {"width": width, "height": height, "length": length, "batch_size": 1},
        }
        positive, negative_cond, latent = ["9", 0], ["10", 0], ["11", 0]

    # 前半 (high noise) は残ノイズを次へ渡し、後半 (low noise) が仕上げる
    wf["12"] = {
        "class_type": "KSamplerAdvanced",
        "inputs": {
            "model": ["7", 0],
            "add_noise": "enable",
            "noise_seed": seed,
            "steps": total_steps,
            "cfg": preset["cfg"],
            "sampler_name": "euler",
            "scheduler": "simple",
            "positive": positive,
            "negative": negative_cond,
            "latent_image": latent,
            "start_at_step": 0,
            "end_at_step": split,
            "return_with_leftover_noise": "enable",
        },
    }
    wf["13"] = {
        "class_type": "KSamplerAdvanced",
        "inputs": {
            "model": ["8", 0],
            "add_noise": "disable",
            "noise_seed": 0,
            "steps": total_steps,
            "cfg": preset["cfg"],
            "sampler_name": "euler",
            "scheduler": "simple",
            "positive": positive,
            "negative": negative_cond,
            "latent_image": ["12", 0],
            "start_at_step": split,
            "end_at_step": 10000,
            "return_with_leftover_noise": "disable",
        },
    }
    wf[DECODE_NODE_ID] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["13", 0], "vae": ["4", 0]},
    }
    _finish(wf, cfg["fps"], filename_prefix)
    return wf


def _build_5b(
    model: str,
    prompt: str,
    width: int,
    height: int,
    length: int,
    seed: int,
    negative: str | None,
    steps: int | None,
    first_frame: str | None,
    filename_prefix: str,
) -> dict:
    cfg = MODELS[model]
    wf: dict = {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": cfg["unet"], "weight_dtype": "default"},
        },
        "3": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": TEXT_ENCODER, "type": "wan", "device": "default"},
        },
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": cfg["vae"]}},
        "8": {
            "class_type": "ModelSamplingSD3",
            "inputs": {"model": ["1", 0], "shift": cfg["shift"]},
        },
        "9": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": prompt}},
        "10": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["3", 0], "text": WAN_NEGATIVE if negative is None else negative},
        },
    }

    # 5B は conditioning に画像を混ぜず、latent 側に先頭フレームを焼き込む
    latent_inputs: dict = {
        "vae": ["4", 0],
        "width": width,
        "height": height,
        "length": length,
        "batch_size": 1,
    }
    if first_frame:
        wf["20"] = {"class_type": "LoadImage", "inputs": {"image": first_frame}}
        latent_inputs["start_image"] = ["20", 0]
    wf["11"] = {"class_type": "Wan22ImageToVideoLatent", "inputs": latent_inputs}

    wf["13"] = {
        "class_type": "KSampler",
        "inputs": {
            "model": ["8", 0],
            "positive": ["9", 0],
            "negative": ["10", 0],
            "latent_image": ["11", 0],
            "seed": seed,
            "steps": steps or cfg["steps"],
            "cfg": cfg["cfg"],
            "sampler_name": cfg["sampler"],
            "scheduler": "simple",
            "denoise": 1.0,
        },
    }
    wf[DECODE_NODE_ID] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["13", 0], "vae": ["4", 0]},
    }
    _finish(wf, cfg["fps"], filename_prefix)
    return wf


def _build_s2v(
    model: str,
    prompt: str,
    width: int,
    height: int,
    length: int,
    seed: int,
    negative: str | None,
    steps: int | None,
    ref_image: str | None,
    audio: str,
    lightning: bool,
    filename_prefix: str,
) -> dict:
    """音声で駆動する S2V。**画像は参照で、先頭フレームではない。**

    出力には駆動に使った音声をそのまま載せる(口が合っているかを見るため)。
    """
    cfg = MODELS[model]
    wf: dict = {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": cfg["unet"], "weight_dtype": "default"},
        },
        "3": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": TEXT_ENCODER, "type": "wan", "device": "default"},
        },
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": cfg["vae"]}},
        "9": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["3", 0], "text": prompt}},
        "10": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["3", 0], "text": WAN_NEGATIVE if negative is None else negative},
        },
        # 音声は wav2vec2 で埋め込みにしてから conditioning に載る
        "23": {"class_type": "LoadAudio", "inputs": {"audio": audio}},
        "24": {
            "class_type": "AudioEncoderLoader",
            "inputs": {"audio_encoder_name": cfg["audio_encoder"]},
        },
        "25": {
            "class_type": "AudioEncoderEncode",
            "inputs": {"audio_encoder": ["24", 0], "audio": ["23", 0]},
        },
    }

    model_id = "1"
    if lightning:
        wf["5"] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"model": ["1", 0], "lora_name": cfg["lora"], "strength_model": 1.0},
        }
        model_id = "5"
    wf["8"] = {
        "class_type": "ModelSamplingSD3",
        "inputs": {"model": [model_id, 0], "shift": cfg["shift"]},
    }

    inputs: dict = {
        "positive": ["9", 0],
        "negative": ["10", 0],
        "vae": ["4", 0],
        "width": width,
        "height": height,
        "length": length,
        "batch_size": 1,
        "audio_encoder_output": ["25", 0],
    }
    if ref_image:
        wf["20"] = {"class_type": "LoadImage", "inputs": {"image": ref_image}}
        inputs["ref_image"] = ["20", 0]
    wf["11"] = {"class_type": "WanSoundImageToVideo", "inputs": inputs}

    wf["13"] = {
        "class_type": "KSampler",
        "inputs": {
            "model": ["8", 0],
            "positive": ["11", 0],
            "negative": ["11", 1],
            "latent_image": ["11", 2],
            "seed": seed,
            "steps": steps or (LIGHTNING["steps"] if lightning else cfg["steps"]),
            "cfg": LIGHTNING["cfg"] if lightning else cfg["cfg"],
            "sampler_name": cfg["sampler"],
            "scheduler": "simple",
            "denoise": 1.0,
        },
    }
    wf[DECODE_NODE_ID] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["13", 0], "vae": ["4", 0]},
    }
    _finish(wf, cfg["fps"], filename_prefix, audio_node=["23", 0])
    return wf


def _finish(
    wf: dict, video_fps: int, filename_prefix: str, audio_node: list | None = None
) -> None:
    """デコード後の共通部分(mp4 化と保存)。

    Wan は音声を生成しない。S2V のときだけ、駆動に使った音声をそのまま載せる。
    """
    video_inputs = {"images": [DECODE_NODE_ID, 0], "fps": video_fps, "bit_depth": 8}
    if audio_node:
        video_inputs["audio"] = audio_node
    wf[VIDEO_NODE_ID] = {"class_type": "CreateVideo", "inputs": video_inputs}
    wf[SAVE_NODE_ID] = {
        "class_type": "SaveVideo",
        "inputs": {
            "video": [VIDEO_NODE_ID, 0],
            "filename_prefix": filename_prefix,
            "format": "auto",
            "codec": "auto",
        },
    }


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
        node_prefix="3",
    )
