"""LTX-2.3 の ComfyUI ワークフロー(API フォーマット)を組み立てる。

ComfyUI 公式テンプレート (`video_ltx2_3_t2v` / `_i2v`) のサブグラフを展開したもの。
ノード構成・入力名は公式実装 (`comfy_extras/nodes_lt*.py`) に合わせてある。

**H3 と同じく映像と音声を1パスで出す。**

ウェイトの持ち方が2通りある。fp8 (29GB) は1本のチェックポイントに MODEL・VAE・
音声 VAE・接続子が全部入っていて、蒸留 LoRA を後から積む。GGUF (Q4_K_M 14.2GB) は
蒸留済みで LoRA が要らず、VAE 類は別ファイルから読む(`gguf=True`)。
**L4 (24GB) に載るのは GGUF の方**なので、L4 で回すならこちら。

生成は2段構え。前半は目標の**半分**の解像度で 8step 回し、latent のまま x2 の
空間アップサンプラを通してから、後半 3step で仕上げる。テンプレートの構造をそのまま
写してあるので、段数やシグマ列を変えるときは公式テンプレートを見ること。

蒸留 LoRA (distilled 1.1) を strength 0.5 で積むのがテンプレートの既定で、
cfg=1.0・合計 11step で回る。外すと step 数を大幅に増やすことになる。
"""

from __future__ import annotations

import os

from video_common import ASPECTS, attach_upscale as _attach_upscale, canvas_size, grid_length  # noqa: F401

MODEL_NAME = "ltx-2.3"
GGUF_MODEL_NAME = "ltx-2.3-gguf"

CHECKPOINT = os.environ.get("LTX_CHECKPOINT", "ltx-2.3-22b-dev-fp8.safetensors")
TEXT_ENCODER = os.environ.get("LTX_TEXT_ENCODER", "gemma_3_12B_it_fp4_mixed.safetensors")
UPSCALER = os.environ.get("LTX_UPSCALER", "ltx-2.3-spatial-upscaler-x2-1.1.safetensors")
DISTILLED_LORA = os.environ.get(
    "LTX_DISTILLED_LORA",
    "ltx_2.3_22b_distilled_1.1_lora_dynamic_fro09_avg_rank_111_bf16.safetensors",
)
DISTILLED_STRENGTH = 0.5

# GGUF 経路。**蒸留済みのウェイトを直接使うので LoRA は積まない。**
# fp8 のチェックポイントは 29GB あって L4 (24GB) に載らず、部分オフロードで
# 大きく遅くなる。Q4_K_M は 14.2GB なので載る。
#
# チェックポイント1本に入っていた VAE・音声 VAE・接続子は、GGUF では別ファイルに
# 分かれている(unsloth/LTX-2.3-GGUF が同梱)。ComfyUI 本体のローダがそのまま読める
# キー構成になっていることは確認済み:
#   video VAE   decoder.up_blocks.0.res_blocks.0.conv1.conv.weight (VAELoader の判定キー)
#   audio VAE   audio_vae.* / vocoder.*   (LTXVAudioVAELoader が期待するプレフィクス)
#   接続子      text_embedding_projection.*  (LTXAVTextEncoderLoader が読む)
# 音声 VAE と接続子は ckpt_name 入力から選ぶので、checkpoints/ に置く。
GGUF_UNET = os.environ.get(
    "LTX_GGUF_UNET", "ltx-2.3-22b-distilled-1.1-Q4_K_M.gguf"
)
GGUF_VIDEO_VAE = os.environ.get(
    "LTX_GGUF_VIDEO_VAE", "ltx-2.3-22b-distilled_video_vae.safetensors"
)
GGUF_AUDIO_VAE = os.environ.get(
    "LTX_GGUF_AUDIO_VAE", "ltx-2.3-22b-distilled_audio_vae.safetensors"
)
GGUF_CONNECTORS = os.environ.get(
    "LTX_GGUF_CONNECTORS", "ltx-2.3-22b-distilled_embeddings_connectors.safetensors"
)

# IC-LoRA "Ingredients"。**参照は1枚の「参照シート」にまとめる。**
# キャラ・小物・場所を並べた1枚を条件にして、それらの見た目を通しで保つ LoRA。
# H3 の r2v のように9枚を別スロットへ渡す形ではない。
IC_LORA = os.environ.get(
    "LTX_IC_LORA", "ltx-2.3-22b-ic-lora-ingredients-0.9.safetensors"
)
# 参照シートは全フレームぶんに複製して渡す(公式テンプレートと同じ)。
# in-context の参照ストリームとして latent に併走するので、通しで効く
IC_STEPS = 8
IC_SAMPLER = "euler_ancestral"
IC_SCHEDULER = "linear_quadratic"

# 前半(半解像度)と後半(x2 アップサンプル後)のシグマ列。テンプレートのまま
SIGMAS_COARSE = "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"
SIGMAS_REFINE = "0.85, 0.7250, 0.4219, 0.0"

# 仕上げのノイズはテンプレートが固定値。seed を変えても構図は前半で決まる
REFINE_SEED = 42

DEFAULT_FPS = 25
FRAME_GRID = 8  # LTX は 8k+1 フレーム
MIN_LENGTH = 9
MAX_LENGTH = 401  # 25fps で 16秒。伸ばすほど VRAM と時間が効く

# 半解像度が 32 の倍数である必要があるので、最終サイズは 64 の倍数にする
CANVAS_MULTIPLE = 64

# 画像を渡すときの「どれくらい元画像に従わせるか」。テンプレートは前半 0.7 / 後半 1.0
FIRST_FRAME_STRENGTH = 0.7

LTX_NEGATIVE = "pc game, console game, video game, cartoon, childish, ugly"

# 参照ではなく「フレーム0」として画像を渡すので、圧縮を軽くかけて生成側に馴染ませる
IMG_COMPRESSION = 18

SAVE_NODE_ID = "54"
DECODE_NODE_ID = "51"
VIDEO_NODE_ID = "53"

TASKS = {"t2v", "i2v"}


def canvas(aspect: str, megapixels: float) -> tuple[int, int]:
    return canvas_size(aspect, megapixels, CANVAS_MULTIPLE)


def frame_length(seconds: float, fps: int = DEFAULT_FPS) -> int:
    return grid_length(
        seconds, fps, FRAME_GRID, offset=1, min_length=MIN_LENGTH, max_length=MAX_LENGTH
    )


def _loaders(gguf: bool) -> tuple[dict, list, list]:
    """ローダ群と、(モデルの参照, VAE の参照) を返す。

    fp8 は1本のチェックポイントに MODEL・VAE・接続子・音声 VAE が全部入っていて、
    蒸留 LoRA を後から積む。GGUF は蒸留済みのウェイトそのものなので LoRA を積まず、
    VAE 類は別ファイルから読む。
    """
    if not gguf:
        nodes = {
            # チェックポイントから MODEL(0) と VAE(2) を取る。CLIP は専用ローダ側
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": CHECKPOINT},
            },
            "2": {
                "class_type": "LTXAVTextEncoderLoader",
                "inputs": {
                    "text_encoder": TEXT_ENCODER,
                    "ckpt_name": CHECKPOINT,
                    "device": "default",
                },
            },
            "3": {"class_type": "LTXVAudioVAELoader", "inputs": {"ckpt_name": CHECKPOINT}},
            "4": {
                "class_type": "LoraLoaderModelOnly",
                "inputs": {
                    "model": ["1", 0],
                    "lora_name": DISTILLED_LORA,
                    "strength_model": DISTILLED_STRENGTH,
                },
            },
        }
        return nodes, ["4", 0], ["1", 2]

    nodes = {
        # UnetLoaderGGUF は ComfyUI 本体には無い(custom_nodes の ComfyUI-GGUF)
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": GGUF_UNET}},
        "2": {
            "class_type": "LTXAVTextEncoderLoader",
            "inputs": {
                "text_encoder": TEXT_ENCODER,
                "ckpt_name": GGUF_CONNECTORS,
                "device": "default",
            },
        },
        "3": {"class_type": "LTXVAudioVAELoader", "inputs": {"ckpt_name": GGUF_AUDIO_VAE}},
        "10": {"class_type": "VAELoader", "inputs": {"vae_name": GGUF_VIDEO_VAE}},
    }
    return nodes, ["1", 0], ["10", 0]


def build(
    prompt: str,
    width: int,
    height: int,
    length: int,
    seed: int,
    fps: int = DEFAULT_FPS,
    negative: str | None = None,
    first_frame: str | None = None,
    ref_sheet: str | None = None,
    ic_strength: float = 1.0,
    filename_prefix: str = "video/cw_ltx",
    gguf: bool = False,
) -> dict:
    """LTX-2.3 のワークフローを返す。first_frame があれば i2v、無ければ t2v。

    width / height は**最終**の解像度。前半はこの半分で回る。

    ref_sheet を渡すと IC-LoRA "Ingredients" を積み、参照シート(キャラ・小物・場所を
    1枚に並べたもの)を in-context の参照として通しで効かせる。**引いたときに初めて
    見える形**をここで補う(先頭フレームだけだと、画面外だった部分をモデルが作り話で
    埋める)。

    公式の IC テンプレートは 8step の単パスだが、こちらは2段構えのまま足している。
    単パスにするとカメラが破綻することがあり、構図を保っているのは前半パス。
    """
    if width % CANVAS_MULTIPLE or height % CANVAS_MULTIPLE:
        raise ValueError(f"width/height は {CANVAS_MULTIPLE} の倍数にしてください")

    wf, model, vae = _loaders(gguf)
    if ref_sheet:
        wf["70"] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"model": model, "lora_name": IC_LORA, "strength_model": ic_strength},
        }
        wf["71"] = {
            "class_type": "GetICLoRAParameters",
            "inputs": {"iclora_model": ["70", 0]},
        }
        wf["75"] = {"class_type": "LoadImage", "inputs": {"image": ref_sheet}}
        wf["76"] = {
            "class_type": "RepeatImageBatch",
            "inputs": {"image": ["75", 0], "amount": length},
        }
        model = ["70", 0]
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
    })

    image_source = None
    if first_frame:
        wf["20"] = {"class_type": "LoadImage", "inputs": {"image": first_frame}}
        wf["21"] = {
            "class_type": "ImageScale",
            "inputs": {
                "image": ["20", 0],
                "upscale_method": "lanczos",
                "width": width,
                "height": height,
                "crop": "center",
            },
        }
        wf["22"] = {
            "class_type": "LTXVPreprocess",
            "inputs": {"image": ["21", 0], "img_compression": IMG_COMPRESSION},
        }
        image_source = ["22", 0]

    # --- 前半: 半解像度で構図と音を決める ---
    wf["30"] = {
        "class_type": "EmptyLTXVLatentVideo",
        "inputs": {"width": width // 2, "height": height // 2, "length": length, "batch_size": 1},
    }
    video_latent = ["30", 0]
    if image_source:
        wf["31"] = {
            "class_type": "LTXVImgToVideoInplace",
            "inputs": {
                "vae": vae,
                "image": image_source,
                "latent": ["30", 0],
                "strength": FIRST_FRAME_STRENGTH,
                "bypass": False,
            },
        }
        video_latent = ["31", 0]

    wf["32"] = {
        "class_type": "LTXVEmptyLatentAudio",
        "inputs": {
            "frames_number": length,
            "frame_rate": fps,
            "batch_size": 1,
            "audio_vae": ["3", 0],
        },
    }
    positive, negative_cond = ["7", 0], ["7", 1]
    if ref_sheet:
        wf["72"] = {
            "class_type": "LTXVAddGuide",
            "inputs": {
                "positive": ["7", 0],
                "negative": ["7", 1],
                "vae": vae,
                "latent": video_latent,
                "image": ["76", 0],
                "frame_idx": 0,
                "strength": 1.0,
                "iclora_parameters": ["71", 0],
            },
        }
        video_latent = ["72", 2]
        positive, negative_cond = ["72", 0], ["72", 1]

    wf["33"] = {
        "class_type": "LTXVConcatAVLatent",
        "inputs": {"video_latent": video_latent, "audio_latent": ["32", 0]},
    }
    wf["34"] = {"class_type": "ManualSigmas", "inputs": {"sigmas": SIGMAS_COARSE}}
    wf["35"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
    wf["36"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}}
    wf["37"] = {
        "class_type": "CFGGuider",
        "inputs": {
            "model": model,
            "positive": positive,
            "negative": negative_cond,
            "cfg": 1.0,
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
    wf["40"] = {
        "class_type": "LTXVCropGuides",
        "inputs": {
            "positive": positive,
            "negative": negative_cond,
            "latent": ["39", 0],
        },
    }
    wf["41"] = {"class_type": "LatentUpscaleModelLoader", "inputs": {"model_name": UPSCALER}}
    # **参照フレームを落としてから拡大する。** 落とさないとシートごと x2 に上げて
    # decode まで運んでしまう(参照つきのときだけ crop 済みの latent を使う)
    wf["42"] = {
        "class_type": "LTXVLatentUpsampler",
        "inputs": {
            "samples": ["40", 2] if ref_sheet else ["39", 0],
            "upscale_model": ["41", 0],
            "vae": vae,
        },
    }
    refined_latent = ["42", 0]
    if image_source:
        wf["43"] = {
            "class_type": "LTXVImgToVideoInplace",
            "inputs": {
                "vae": vae,
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
    wf["46"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
    wf["47"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": REFINE_SEED}}
    wf["48"] = {
        "class_type": "CFGGuider",
        "inputs": {"model": model, "positive": ["40", 0], "negative": ["40", 1], "cfg": 1.0},
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

    # 全長を一度に decode すると詰まるのでタイル分割する(テンプレートの値のまま)
    wf[DECODE_NODE_ID] = {
        "class_type": "VAEDecodeTiled",
        "inputs": {
            "samples": ["50", 0],
            "vae": vae,
            "tile_size": 768,
            "overlap": 64,
            "temporal_size": 4096,
            "temporal_overlap": 4,
        },
    }
    wf["52"] = {
        "class_type": "LTXVAudioVAEDecode",
        "inputs": {"samples": ["50", 1], "audio_vae": ["3", 0]},
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
    return wf


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
