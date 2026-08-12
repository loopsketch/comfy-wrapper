"""静止画生成の ComfyUI ワークフロー(API フォーマット)を組み立てる。

ComfyUI 公式テンプレート (`image_z_image_turbo` / `image_qwen_Image_2512` /
`image_qwen_image_edit_2511`) のサブグラフを展開して API フォーマットに落としたもの。
ノード構成・入力名は公式テンプレートに合わせてある。

A100 (sm80) は FP8/FP4 をネイティブに実行できないので int8 を既定にしている。
qwen-image だけは例外で fp8_e4m3fn_scaled(int8 版は ComfyUI の UNETLoader で読めない)。
"""

from __future__ import annotations

MODELS = {
    "z-image": {
        "unet": "z_image_turbo_int8_convrot.safetensors",
        "clip": "qwen_3_4b_fp8_mixed.safetensors",
        "clip_type": "lumina2",
        "vae": "ae.safetensors",
        "steps": 8,
        "cfg": 1.0,
        "sampler": "res_multistep",
        "shift": 3.0,
    },
    "qwen-image": {
        # lightx2v の 4steps 統合版(ComfyUI 用)。Lightning LoRA を別に読む必要がない
        "unet": "qwen_image_2512_comfyui_4steps.safetensors",
        "clip": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
        "clip_type": "qwen_image",
        "vae": "qwen_image_vae.safetensors",
        "steps": 4,
        "cfg": 1.0,
        "sampler": "euler",
        "shift": 3.1,
    },
    "qwen-image-edit": {
        "unet": "qwen_image_edit_2511_int8_convrot.safetensors",
        "clip": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
        "clip_type": "qwen_image",
        "vae": "qwen_image_vae.safetensors",
        "lora": "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-fp32.safetensors",
        "steps": 4,
        "cfg": 1.0,
        "sampler": "euler",
        "shift": 3.1,
    },
}

# 公式テンプレートが Qwen-Image に置いている既定のネガティブ(中国語)。
# 低画質・肢体の破綻・蝋人形感・過度な平滑化を避ける指示。
#
# **cfg=1.0 の間は効かない。** cfg=1.0 ではネガティブ側の conditioning が数式上まったく
# 寄与しないため、4step Lightning の既定では飾りになる。cfg を上げたときのために残して
# あるだけなので、避けたいものはプロンプト本文に頼らないこと(本文末尾の "Avoid: ..." は
# 肯定文として読まれる。呼び出し側で落としておくこと)。
QWEN_NEGATIVE = (
    "低分辨率，低画质，肢体畸形，手指畸形，画面过饱和，蜡像感，"
    "人脸无细节，过度光滑，画面具有AI感。构图混乱。文字模糊，扭曲"
)

# 出力ノードの ID。app.py はここから画像を回収する
SAVE_NODE_ID = "99"

ASPECT_SIZES = {
    # 短辺 1024 前後。Qwen 系の公式テンプレートは 1328x1328 を既定にしている
    "1x1": (1328, 1328),
    "16x9": (1664, 928),
    "9x16": (928, 1664),
}


def canvas_size(aspect: str) -> tuple[int, int]:
    if aspect not in ASPECT_SIZES:
        raise ValueError(f"未対応のアスペクト: {aspect}")
    return ASPECT_SIZES[aspect]


def _loaders(cfg: dict, loras: list[tuple[str, float]] | None) -> tuple[dict, str]:
    """ローダ群を作り、(ノード辞書, モデル出力のノードID) を返す。

    LoRA は積み重ねられるので、最後に繋がったノードの ID を返す。
    """
    nodes = {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": cfg["unet"], "weight_dtype": "default"},
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": cfg["clip"],
                "type": cfg["clip_type"],
                "device": "default",
            },
        },
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": cfg["vae"]}},
    }

    model_id = "1"
    stack = list(loras or [])
    if cfg.get("lora"):  # Edit 系は Lightning LoRA を先に積む
        stack.insert(0, (cfg["lora"], 1.0))

    for i, (name, strength) in enumerate(stack):
        node_id = f"1{i}0"
        nodes[node_id] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "model": [model_id, 0],
                "lora_name": name,
                "strength_model": strength,
            },
        }
        model_id = node_id

    # ModelSamplingAuraFlow はテンプレート通り、サンプラの直前に置く
    nodes["20"] = {
        "class_type": "ModelSamplingAuraFlow",
        "inputs": {"model": [model_id, 0], "shift": cfg["shift"]},
    }
    return nodes, "20"


def build_t2i(
    model: str,
    prompt: str,
    aspect: str = "1x1",
    seed: int = 0,
    negative: str | None = None,
    loras: list[tuple[str, float]] | None = None,
    filename_prefix: str = "comfy",
    steps: int | None = None,
) -> dict:
    """テキストから静止画を1枚生成するワークフロー。"""
    if model not in MODELS:
        raise ValueError(f"未知のモデル: {model}")
    cfg = MODELS[model]
    width, height = canvas_size(aspect)

    nodes, model_id = _loaders(cfg, loras)

    nodes["4"] = {
        "class_type": "CLIPTextEncode",
        "inputs": {"clip": ["2", 0], "text": prompt},
    }
    if model == "z-image":
        # Z-Image はネガティブを使わず ConditioningZeroOut で潰す(テンプレート通り)
        nodes["5"] = {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["4", 0]},
        }
    else:
        nodes["5"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "clip": ["2", 0],
                "text": QWEN_NEGATIVE if negative is None else negative,
            },
        }

    nodes["6"] = {
        "class_type": "EmptySD3LatentImage",
        "inputs": {"width": width, "height": height, "batch_size": 1},
    }
    nodes["7"] = {
        "class_type": "KSampler",
        "inputs": {
            "model": [model_id, 0],
            "positive": ["4", 0],
            "negative": ["5", 0],
            "latent_image": ["6", 0],
            "seed": seed,
            "steps": steps or cfg["steps"],
            "cfg": cfg["cfg"],
            "sampler_name": cfg["sampler"],
            "scheduler": "simple",
            "denoise": 1.0,
        },
    }
    nodes["8"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["7", 0], "vae": ["3", 0]},
    }
    nodes[SAVE_NODE_ID] = {
        "class_type": "SaveImage",
        "inputs": {"images": ["8", 0], "filename_prefix": filename_prefix},
    }
    return nodes


def build_edit(
    prompt: str,
    images: list[str],
    aspect: str = "1x1",
    seed: int = 0,
    negative: str | None = None,
    loras: list[tuple[str, float]] | None = None,
    filename_prefix: str = "comfy",
    steps: int | None = None,
) -> dict:
    """参照画像つきの編集・生成(Qwen-Image-Edit-2511)。

    images は ComfyUI の input/ に置いたファイル名。最大3枚まで。
    プロンプトからは "image 1" "image 2" のように指せる。

    出力の寸法は **aspect で決める**。参照画像の寸法は引き継がない。
    呼び出し側はキャラのシート(横長)を毎回「見た目の手本」として渡すので、参照に合わせると
    9x16 のカットが横長で出てくる(2026-08-08 に踏んだ)。

    参照の中身は TextEncodeQwenImageEditPlus が conditioning に載せる。KSampler の
    latent は denoise=1.0 で全面ノイズに置き換わるため、形(= 出力の寸法)だけが効く。
    """
    if not images:
        raise ValueError("参照画像が要ります")
    if len(images) > 3:
        raise ValueError("参照画像は3枚まで")

    cfg = MODELS["qwen-image-edit"]
    width, height = canvas_size(aspect)
    nodes, model_id = _loaders(cfg, loras)

    # LoadImage を並べ、TextEncodeQwenImageEditPlus に image1..3 として渡す
    encode_inputs: dict = {"clip": ["2", 0], "vae": ["3", 0], "prompt": prompt}
    for i, name in enumerate(images, start=1):
        node_id = f"3{i}0"
        nodes[node_id] = {"class_type": "LoadImage", "inputs": {"image": name}}
        encode_inputs[f"image{i}"] = [node_id, 0]

    nodes["4"] = {"class_type": "TextEncodeQwenImageEditPlus", "inputs": encode_inputs}
    nodes["5"] = {
        "class_type": "TextEncodeQwenImageEditPlus",
        "inputs": {
            **{k: v for k, v in encode_inputs.items() if k != "prompt"},
            "prompt": QWEN_NEGATIVE if negative is None else negative,
        },
    }
    nodes["7"] = {
        "class_type": "EmptySD3LatentImage",
        "inputs": {"width": width, "height": height, "batch_size": 1},
    }
    nodes["8"] = {
        "class_type": "KSampler",
        "inputs": {
            "model": [model_id, 0],
            "positive": ["4", 0],
            "negative": ["5", 0],
            "latent_image": ["7", 0],
            "seed": seed,
            "steps": steps or cfg["steps"],
            "cfg": cfg["cfg"],
            "sampler_name": cfg["sampler"],
            "scheduler": "simple",
            "denoise": 1.0,
        },
    }
    nodes["9"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["8", 0], "vae": ["3", 0]},
    }
    nodes[SAVE_NODE_ID] = {
        "class_type": "SaveImage",
        "inputs": {"images": ["9", 0], "filename_prefix": filename_prefix},
    }
    return nodes
