#!/usr/bin/env python3
"""静止画生成のウェイトを ComfyUI のモデルディレクトリへ落とす。

  python download_image_models.py --comfy /content/ComfyUI --models z-image qwen-image
  python download_image_models.py --comfy /content/ComfyUI --models all --loras anime

いずれも Apache 2.0。A100 (sm80) は FP8/FP4 をネイティブ実行できないので、
既定は int8 のウェイトを選んでいる(H3 で int8 を既定にしたのと同じ理由)。

Qwen-Image / Qwen-Image-Edit はテキストエンコーダと VAE を共用する。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 生成物と作業領域のために空けておく分。これを割り込む取得は最初から始めない
DISK_MARGIN_GB = 5.0

import resilient_download

# (HFリポジトリ, repo内パス, ComfyUI の models/ 配下の配置先, おおよそのGB)
Asset = tuple[str, str, str, float]

QWEN_SHARED: list[Asset] = [
    (
        "Comfy-Org/Qwen-Image_ComfyUI",
        "split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors",
        "text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors",
        8.74,
    ),
    (
        "Comfy-Org/Qwen-Image_ComfyUI",
        "split_files/vae/qwen_image_vae.safetensors",
        "vae/qwen_image_vae.safetensors",
        0.24,
    ),
]

MODELS: dict[str, list[Asset]] = {
    # 軽量・高速枠。8 step で回る
    "z-image": [
        (
            "Comfy-Org/z_image_turbo",
            "split_files/diffusion_models/z_image_turbo_int8_convrot.safetensors",
            "diffusion_models/z_image_turbo_int8_convrot.safetensors",
            5.78,
        ),
        (
            "Comfy-Org/z_image_turbo",
            "split_files/text_encoders/qwen_3_4b_fp8_mixed.safetensors",
            "text_encoders/qwen_3_4b_fp8_mixed.safetensors",
            5.25,
        ),
        (
            "Comfy-Org/z_image_turbo",
            "split_files/vae/ae.safetensors",
            "vae/ae.safetensors",
            0.31,
        ),
    ],
    # 本命。lightx2v の 4steps 統合版を使うので Lightning LoRA を別に読まなくてよい。
    # **ファイル名に comfyui が入っているものを選ぶこと。** 同じ repo の
    # `qwen_image_2512_int8_4steps_v1.0.safetensors` は ComfyUI の UNETLoader では
    # 読めず、生成結果が全面ノイズになる(2026-08-07 に実際に踏んだ)。
    "qwen-image": [
        (
            "lightx2v/Qwen-Image-2512-Lightning",
            "qwen_image_2512_fp8_e4m3fn_scaled_comfyui_4steps_v1.0.safetensors",
            "diffusion_models/qwen_image_2512_comfyui_4steps.safetensors",
            19.03,
        ),
        *QWEN_SHARED,
    ],
    # キャラ一貫性・多参照。エンコーダと VAE は qwen-image と共用
    "qwen-image-edit": [
        (
            "Comfy-Org/Qwen-Image-Edit_ComfyUI",
            "split_files/diffusion_models/qwen_image_edit_2511_int8_convrot.safetensors",
            "diffusion_models/qwen_image_edit_2511_int8_convrot.safetensors",
            19.09,
        ),
        (
            "lightx2v/Qwen-Image-Edit-2511-Lightning",
            "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-fp32.safetensors",
            "loras/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-fp32.safetensors",
            1.58,
        ),
        *QWEN_SHARED,
    ],
}

# アニメ調の LoRA。静止画がアニメ調なら i2v でアニメ MV が作れる
LORAS: dict[str, list[Asset]] = {
    "anime": [
        (
            "alfredplpl/qwen-image-modern-anime-lora",
            "lora.safetensors",
            "loras/qwen_image_modern_anime.safetensors",
            0.55,
        ),
        (
            "prithivMLmods/Qwen-Image-Anime-LoRA",
            "qwen-anime.safetensors",
            "loras/qwen_image_anime.safetensors",
            1.10,
        ),
        (
            "flymy-ai/qwen-image-anime-irl-lora",
            "flymy_anime_irl.safetensors",
            "loras/qwen_image_anime_irl.safetensors",
            0.04,
        ),
    ],
    "anime-edit": [
        (
            "prithivMLmods/Qwen-Image-Edit-2511-Anime",
            "Qwen-Image-Edit-2511-Anime-2000.safetensors",
            "loras/qwen_image_edit_2511_anime.safetensors",
            0.22,
        ),
    ],
}


def fetch(asset: Asset, comfy_root: Path, cache: Path | None = None) -> Path | None:
    """ファイル名が repo ごとに違うので、配置先を明示して落とす。

    19GB 級の1ファイルは途中で無応答になる。原因は Xet で、例外を上げないため
    try/except では捕まらない。取得は resilient_download.py に任せる
    (無進捗を外から見て殺し、続きから再開する)。
    """
    repo, src, dest_rel, size_gb = asset
    dest = comfy_root / "models" / dest_rel
    if dest.exists():
        print(f"[skip] {dest.name} (既にあります)")
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)

    # Drive 等の永続ディレクトリに置いてあれば、そこから symlink するだけで済む。
    # 一度落としたものは持っておく方が速くて安い。
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
        shutil.copy2(got, cache / dest_rel)   # 次回のために残す
        dest.symlink_to(cache / dest_rel)
    else:
        dest.symlink_to(got)
    return dest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--comfy", required=True, type=Path, help="ComfyUI のルート")
    parser.add_argument(
        "--models",
        nargs="+",
        default=["z-image"],
        choices=[*MODELS, "all"],
    )
    parser.add_argument("--loras", nargs="*", default=[], choices=list(LORAS))
    parser.add_argument(
        "--cache",
        type=Path,
        help="Drive 等の永続ディレクトリ。次のセッションで再取得を省ける",
    )
    args = parser.parse_args()

    names = list(MODELS) if "all" in args.models else args.models

    plan: list[Asset] = []
    for name in names:
        for a in MODELS[name]:
            if a not in plan:  # 共用のエンコーダ・VAE を二重に数えない
                plan.append(a)
    for name in args.loras:
        plan.extend(LORAS[name])

    # 落とす前に、落とせるかを判断する。途中でディスクが尽きると、
    # そこまでの取得時間(= 課金)がまるごと無駄になる
    def _have(a: Asset) -> bool:
        if (args.comfy / "models" / a[2]).exists():
            return True
        return args.cache is not None and (args.cache / a[2]).exists()

    todo = [a for a in plan if not _have(a)]
    have_gb = sum(a[3] for a in plan) - sum(a[3] for a in todo)
    need_gb = sum(a[3] for a in todo)
    free_gb = shutil.disk_usage(args.comfy).free / 1024**3

    print(f"要求 {sum(a[3] for a in plan):.1f} GB "
          f"(取得済み {have_gb:.1f} GB / これから {need_gb:.1f} GB)")
    print(f"空き {free_gb:.1f} GB (余裕として {DISK_MARGIN_GB:.0f} GB 残す)\n")

    if need_gb > free_gb - DISK_MARGIN_GB:
        print(
            f"ディスクが足りません。{need_gb:.1f} GB 要るのに使えるのは "
            f"{max(free_gb - DISK_MARGIN_GB, 0):.1f} GB です。\n"
            "落とすモデルを減らすか、用途ごとにセッションを分けてください "
            "(H3(動画) 42.5GB と Qwen 系(画像) 28GB は同居できません)。"
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
