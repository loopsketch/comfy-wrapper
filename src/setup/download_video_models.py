#!/usr/bin/env python3
"""Wan2.2 / LTX-2.3 のウェイトを ComfyUI のモデルディレクトリへ落とす。

  python download_video_models.py --comfy /content/ComfyUI --models wan2.2
  python download_video_models.py --comfy /content/ComfyUI --models ltx-2.3

MiniMax H3 は download_models.py のまま(リポジトリ構成が ComfyUI の models/ と
同じで、量子化の選択肢も別軸のため)。こちらは repo ごとにパスがばらばらなので、
download_image_models.py と同じ (repo, repo内パス, 配置先, GB) の表で持つ。

**1セッションに1モデル。** Wan2.2 14B 一式で約 38GB、LTX-2.3 で約 42GB、
LTX-2.5 で約 40GB あり、/content にも VRAM にも同時には載らない。
切り替えるときは構築からやり直す。

Lightricks の LTX 系リポジトリは gated なので、HF トークン側でライセンスに
同意しておくこと(未同意だと 401 で落ちる)。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import resilient_download

# 生成物と作業領域のために空けておく分
DISK_MARGIN_GB = 8.0

# (HFリポジトリ, repo内パス, ComfyUI の models/ 配下の配置先, おおよそのGB)
Asset = tuple[str, str, str, float]

WAN_REPO = "Comfy-Org/Wan_2.2_ComfyUI_Repackaged"
# テキストエンコーダだけは 2.1 のリポジトリのまま(2.2 でも同じものを使う)
UMT5: Asset = (
    "Comfy-Org/Wan_2.1_ComfyUI_repackaged",
    "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    "text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    6.74,
)


def _wan(name: str, kind: str, size: float) -> Asset:
    return (WAN_REPO, f"split_files/{kind}/{name}", f"{kind}/{name}", size)


MODELS: dict[str, list[Asset]] = {
    # 14B MoE。high/low の2本と、4steps 蒸留 LoRA。i2v 用のウェイトを既定にする
    # (静止画から動かす i2v が主な用途で、t2v 用は別ファイル)
    "wan2.2": [
        _wan("wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors", "diffusion_models", 14.29),
        _wan("wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors", "diffusion_models", 14.29),
        _wan("wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors", "loras", 1.23),
        _wan("wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors", "loras", 1.23),
        _wan("wan_2.1_vae.safetensors", "vae", 0.25),
        UMT5,
    ],
    # t2v も回したいとき用。i2v とは別ウェイトなので、両方入れると +31GB
    "wan2.2-t2v": [
        _wan("wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors", "diffusion_models", 14.29),
        _wan("wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors", "diffusion_models", 14.29),
        _wan("wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors", "loras", 1.23),
        _wan("wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors", "loras", 1.23),
        _wan("wan_2.1_vae.safetensors", "vae", 0.25),
        UMT5,
    ],
    # S2V-14B。音声で口と動きを駆動する。wav2vec2 の音声エンコーダが要る。
    # 蒸留 LoRA は t2v 用のものを流用する(公式テンプレートがそうしている)
    "wan2.2-s2v": [
        _wan("wan2.2_s2v_14B_fp8_scaled.safetensors", "diffusion_models", 16.39),
        _wan("wav2vec2_large_english_fp16.safetensors", "audio_encoders", 0.63),
        _wan("wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors", "loras", 1.23),
        _wan("wan_2.1_vae.safetensors", "vae", 0.25),
        UMT5,
    ],
    # TI2V-5B。1本で t2v も i2v も回る軽量枠。VAE は 2.2 系の別ファイル
    "wan2.2-5b": [
        _wan("wan2.2_ti2v_5B_fp16.safetensors", "diffusion_models", 10.00),
        _wan("wan2.2_vae.safetensors", "vae", 1.41),
        UMT5,
    ],
    # LTX-2.3。映像と音声を1パスで出す。チェックポイントに音声 VAE も入っている
    "ltx-2.3": [
        (
            "Lightricks/LTX-2.3-fp8",
            "ltx-2.3-22b-dev-fp8.safetensors",
            "checkpoints/ltx-2.3-22b-dev-fp8.safetensors",
            29.15,
        ),
        (
            "Comfy-Org/ltx-2",
            "split_files/text_encoders/gemma_3_12B_it_fp4_mixed.safetensors",
            "text_encoders/gemma_3_12B_it_fp4_mixed.safetensors",
            9.45,
        ),
        (
            "Comfy-Org/ltx-2.3",
            "split_files/loras/ltx_2.3_22b_distilled_1.1_lora_dynamic_fro09_avg_rank_111_bf16.safetensors",
            "loras/ltx_2.3_22b_distilled_1.1_lora_dynamic_fro09_avg_rank_111_bf16.safetensors",
            2.74,
        ),
        (
            "Lightricks/LTX-2.3",
            "ltx-2.3-spatial-upscaler-x2-1.1.safetensors",
            "latent_upscale_models/ltx-2.3-spatial-upscaler-x2-1.1.safetensors",
            1.00,
        ),
    ],
    # LTX-2.3 の Q4_K_M 量子化版。**L4 (24GB) で回すならこちら。**
    # fp8 は本体だけで 29GB あって載らないが、これは 14.2GB で収まる。
    # 蒸留済みのウェイトなので蒸留 LoRA は要らない。
    #
    # チェックポイントに同梱されていた VAE・音声 VAE・接続子は別ファイルで配られている。
    # **音声 VAE と接続子は checkpoints/ に置く。** ComfyUI 側のローダ
    # (LTXVAudioVAELoader / LTXAVTextEncoderLoader) が ckpt_name 入力から選ぶため。
    "ltx-2.3-gguf": [
        (
            "unsloth/LTX-2.3-GGUF",
            "distilled-1.1/ltx-2.3-22b-distilled-1.1-Q4_K_M.gguf",
            "diffusion_models/ltx-2.3-22b-distilled-1.1-Q4_K_M.gguf",
            14.19,
        ),
        (
            "unsloth/LTX-2.3-GGUF",
            "vae/ltx-2.3-22b-distilled_video_vae.safetensors",
            "vae/ltx-2.3-22b-distilled_video_vae.safetensors",
            1.45,
        ),
        (
            "unsloth/LTX-2.3-GGUF",
            "vae/ltx-2.3-22b-distilled_audio_vae.safetensors",
            "checkpoints/ltx-2.3-22b-distilled_audio_vae.safetensors",
            0.36,
        ),
        (
            "unsloth/LTX-2.3-GGUF",
            "text_encoders/ltx-2.3-22b-distilled_embeddings_connectors.safetensors",
            "checkpoints/ltx-2.3-22b-distilled_embeddings_connectors.safetensors",
            2.31,
        ),
        (
            "Comfy-Org/ltx-2",
            "split_files/text_encoders/gemma_3_12B_it_fp4_mixed.safetensors",
            "text_encoders/gemma_3_12B_it_fp4_mixed.safetensors",
            9.45,
        ),
        (
            "Lightricks/LTX-2.3",
            "ltx-2.3-spatial-upscaler-x2-1.1.safetensors",
            "latent_upscale_models/ltx-2.3-spatial-upscaler-x2-1.1.safetensors",
            1.00,
        ),
        # 参照シートつき生成 (IC-LoRA Ingredients)。1.31GB なので一緒に入れておく
        (
            "Comfy-Org/ltx-2.3",
            "split_files/loras/ltx-2.3-22b-ic-lora-ingredients-0.9.safetensors",
            "loras/ltx-2.3-22b-ic-lora-ingredients-0.9.safetensors",
            1.31,
        ),
    ],
    # LTX-2.5。int8 量子化の公式ウェイト。2.3 と違ってチェックポイント1本ではなく、
    # 本体・テキストエンコーダ・映像 VAE・音声 VAE に分かれている。
    #
    # **音声 VAE も vae/ に置く。** 2.3 では専用ローダが checkpoints/ から読んでいたが、
    # 2.5 は素の VAELoader で通るようになった。
    #
    # bf16 は本体だけで 42GB あって 1セッションに収まらない。nvfp4 (18.7GB) は
    # Blackwell 専用で L4 (Ada) では動かないので、選べるのは int8 だけ。
    "ltx-2.5": [
        (
            "Lightricks/LTX-2.5",
            "diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors",
            "diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors",
            21.50,
        ),
        (
            "Lightricks/LTX-2.5",
            "text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors",
            "text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors",
            15.37,
        ),
        (
            "Lightricks/LTX-2.5",
            "vae/ltx-2.5-video-vae-bf16.safetensors",
            "vae/ltx-2.5-video-vae-bf16.safetensors",
            1.47,
        ),
        (
            "Lightricks/LTX-2.5",
            "vae/ltx-2.5-audio-vae-bf16.safetensors",
            "vae/ltx-2.5-audio-vae-bf16.safetensors",
            0.36,
        ),
        (
            "Lightricks/LTX-2.5",
            "latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
            "latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
            1.00,
        ),
    ],
}

# 仕上げ(フレーム補間 + アップスケール)。**合計 160MB なので毎回入れる。**
# 生成したその場で 4K 24p まで持っていけるようにしておく方が、セッションを
# 立て直すより安い。ノードは ComfyUI 本体に入っている(カスタムノード不要)
POSTPROCESS: list[Asset] = [
    (
        "Comfy-Org/Real-ESRGAN_repackaged",
        "RealESRGAN_x4plus.safetensors",
        "upscale_models/RealESRGAN_x4plus.safetensors",
        0.07,
    ),
    (
        "Comfy-Org/frame_interpolation",
        "frame_interpolation/film_net_fp16.safetensors",
        "frame_interpolation/film_net_fp16.safetensors",
        0.07,
    ),
    (
        "Comfy-Org/frame_interpolation",
        "frame_interpolation/rife_v4.26.safetensors",
        "frame_interpolation/rife_v4.26.safetensors",
        0.02,
    ),
    # **x2 も要る。** 1080p から 4K なら x4 は過剰で、中間が 7680x4352 に膨らむ
    (
        "ai-forever/Real-ESRGAN",
        "RealESRGAN_x2.pth",
        "upscale_models/RealESRGAN_x2.pth",
        0.07,
    ),
]
MODELS["postprocess"] = POSTPROCESS

# GGUF を読むには ComfyUI 本体に無いノードが要る(UnetLoaderGGUF)
GGUF_MODELS = {"ltx-2.3-gguf"}


def fetch(asset: Asset, comfy_root: Path, cache: Path | None = None) -> Path | None:
    """ファイル名が repo ごとに違うので、配置先を明示して落とす。

    取得は resilient_download.py に任せる(Xet が無応答で固まっても例外が
    上がらないので、外から無進捗を見て殺し、続きから再開する)。
    """
    repo, src, dest_rel, size_gb = asset
    dest = comfy_root / "models" / dest_rel
    if dest.exists():
        print(f"[skip] {dest.name} (既にあります)")
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)

    if cache is not None:
        cached = cache / dest_rel
        if cached.exists():
            print(f"[cache] {dest.name} (~{size_gb:.2f} GB)")
            dest.symlink_to(cached)
            return dest
        cached.parent.mkdir(parents=True, exist_ok=True)

    print(f"[get ] {dest_rel} (~{size_gb:.2f} GB) <- {repo}", flush=True)
    try:
        got = resilient_download.download(repo, src)
    except Exception as e:
        print(f"[warn] {repo}/{src} を取得できませんでした: {e}")
        return None

    if cache is not None:
        shutil.copy2(got, cache / dest_rel)
        dest.symlink_to(cache / dest_rel)
    else:
        dest.symlink_to(got)
    return dest


def plan_for(names: list[str]) -> list[Asset]:
    """重複(共用のエンコーダ・VAE)を落として取得計画を作る。"""
    plan: list[Asset] = []
    for name in names:
        for asset in MODELS[name]:
            if asset not in plan:
                plan.append(asset)
    return plan


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--comfy", required=True, type=Path, help="ComfyUI のルート")
    parser.add_argument("--models", nargs="+", default=["wan2.2"], choices=list(MODELS))
    parser.add_argument(
        "--no-postprocess",
        action="store_true",
        help="仕上げ用(補間・拡大、計160MB)を落とさない",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        help="Drive 等の永続ディレクトリ。次のセッションで再取得を省ける",
    )
    args = parser.parse_args()

    names = list(args.models)
    if not args.no_postprocess and "postprocess" not in names:
        names.append("postprocess")
    plan = plan_for(names)

    # **落とす前に、落とせるかを見る。** 途中でディスクが尽きると、そこまでの
    # 取得時間(= GPU の課金)がまるごと無駄になる
    def _have(a: Asset) -> bool:
        if (args.comfy / "models" / a[2]).exists():
            return True
        return args.cache is not None and (args.cache / a[2]).exists()

    todo = [a for a in plan if not _have(a)]
    need_gb = sum(a[3] for a in todo)
    free_gb = shutil.disk_usage(args.comfy).free / 1024**3

    print(f"要求 {sum(a[3] for a in plan):.1f} GB (これから {need_gb:.1f} GB)")
    print(f"空き {free_gb:.1f} GB (余裕として {DISK_MARGIN_GB:.0f} GB 残す)\n")

    if need_gb > free_gb - DISK_MARGIN_GB:
        print(
            f"ディスクが足りません。{need_gb:.1f} GB 要るのに使えるのは "
            f"{max(free_gb - DISK_MARGIN_GB, 0):.1f} GB です。\n"
            "1セッションに1モデルにしてください "
            "(wan2.2 約38GB / ltx-2.3 約42GB / ltx-2.5 約40GB / H3 42.5GB は同居できません)。"
        )
        return 1

    if not todo:
        print("すべて取得済みです")
        return 0

    for asset in todo:
        fetch(asset, args.comfy, args.cache)

    print("\n完了しました")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
