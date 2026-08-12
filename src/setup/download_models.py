#!/usr/bin/env python3
"""Comfy-Org/MiniMax-H3 から必要なウェイトを ComfyUI のモデルディレクトリへ落とす。

  python download_models.py --comfy /content/ComfyUI --tasks fl2va
  python download_models.py --comfy /content/ComfyUI --tasks fl2va ref2va --quant fp8
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import resilient_download

REPO = "Comfy-Org/MiniMax-H3"

# 生成物と作業領域のために空けておく分。書きかけの取り直しにも余裕が要る
DISK_MARGIN_GB = 8.0

# (repo内パス, ComfyUI配下の配置先, おおよそのGB)
DIFFUSION = {
    "int8": {
        "fl2va": ("diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors", 21.0),
        "ref2va": ("diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors", 21.0),
    },
    "fp8": {
        "fl2va": ("diffusion_models/minimax_h3_fl2va_pruned_fp8_scaled.safetensors", 21.0),
        "ref2va": ("diffusion_models/minimax_h3_ref2va_pruned_fp8_scaled.safetensors", 21.0),
    },
    "bf16": {
        "fl2va": ("diffusion_models/minimax_h3_fl2va_pruned_bf16.safetensors", 40.2),
        "ref2va": ("diffusion_models/minimax_h3_ref2va_pruned_bf16.safetensors", 40.2),
    },
}

TEXT_ENCODERS = {
    "nvfp4": ("text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", 15.7),
    "int8": ("text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors", 27.1),
    "bf16": ("text_encoders/qwen3vl_32b_minimax_h3_bf16.safetensors", 51.5),
}

VAES = [
    ("vae/minimax_h3_video_vae_fp16.safetensors", 5.2),
    ("vae/minimax_h3_audio_vae_fp32.safetensors", 0.6),
]

# 出力の拡大に使う任意のモデル。指定しなければサーバ側は lanczos で拡大する。
UPSCALERS = {
    "realesrgan-x2": ("ai-forever/Real-ESRGAN", "RealESRGAN_x2.pth", 0.07),
    "realesrgan-x4": ("ai-forever/Real-ESRGAN", "RealESRGAN_x4.pth", 0.07),
}


def _download(repo: str, path: str, local_dir: Path) -> None:
    """止まったら殺して再開する取得。詳細は resilient_download.py。"""
    resilient_download.download(repo, path, local_dir=local_dir)


def fetch(path: str, comfy_root: Path, size_gb: float, cache: Path | None = None) -> Path:
    """repo 側のディレクトリ構成が ComfyUI の models/ と同じなのでそのまま展開する。

    cache を渡すと、そこへ落として ComfyUI 側からは symlink で参照する。
    Drive のような永続ディレクトリを指定すればセッションをまたいで再利用できる。
    """
    dest = comfy_root / "models" / path
    if dest.exists():
        print(f"[skip] {dest.name} (既にあります)")
        return dest

    if cache is None:
        print(f"[get ] {path} (~{size_gb:.1f} GB)")
        _download(REPO, path, comfy_root / "models")
        return dest

    cached = cache / path
    if cached.exists():
        print(f"[cache] {cached.name} (~{size_gb:.1f} GB)")
    else:
        print(f"[get ] {path} (~{size_gb:.1f} GB) -> {cache}")
        _download(REPO, path, cache)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.symlink_to(cached)
    return dest


def fetch_upscaler(name: str, comfy_root: Path) -> Path:
    repo, filename, size_gb = UPSCALERS[name]
    dest_dir = comfy_root / "models" / "upscale_models"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    if dest.exists():
        print(f"[skip] {filename} (既にあります)")
        return dest
    print(f"[get ] {filename} (~{size_gb * 1000:.0f} MB)")
    _download(repo, filename, dest_dir)
    return dest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--comfy", required=True, type=Path, help="ComfyUI のルート")
    parser.add_argument(
        "--tasks",
        nargs="+",
        default=["fl2va"],
        choices=["fl2va", "ref2va"],
        help="fl2va=t2v/i2v, ref2va=参照つき生成",
    )
    parser.add_argument("--quant", default="int8", choices=["int8", "fp8", "bf16"])
    parser.add_argument("--text-encoder", default="nvfp4", choices=list(TEXT_ENCODERS))
    parser.add_argument(
        "--upscaler",
        nargs="*",
        default=[],
        choices=list(UPSCALERS),
        help="出力拡大用の任意モデル。省略すると lanczos で拡大する",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        help="Drive 等の永続ディレクトリ。次のセッションで再ダウンロードを省ける",
    )
    args = parser.parse_args()

    plan = [DIFFUSION[args.quant][t] for t in args.tasks]
    plan.append(TEXT_ENCODERS[args.text_encoder])
    plan.extend(VAES)
    total = sum(size for _, size in plan)
    where = f" (キャッシュ: {args.cache})" if args.cache else ""
    print(f"合計 {total:.1f} GB を用意します{where}")

    # **落とす前に、落とせるかを見る。** 途中でディスクが尽きると、そこまでの
    # 取得時間(= GPU の課金)がまるごと無駄になる。実際、21GB を取り終えたあとに
    # 次の 15.7GB が `No space left on device` で落ちた(2026-08-09)。
    todo = [(p, s) for p, s in plan if not (args.comfy / "models" / p).exists()]
    need = sum(s for _, s in todo)
    free = shutil.disk_usage(args.comfy).free / 1024 ** 3
    print(f"これから {need:.1f} GB / 空き {free:.1f} GB "
          f"(余裕として {DISK_MARGIN_GB:.0f} GB 残す)\n")
    if need > free - DISK_MARGIN_GB:
        print(f"ディスクが足りません。{need:.1f} GB 要るのに使えるのは "
              f"{max(free - DISK_MARGIN_GB, 0):.1f} GB です。\n"
              "取得するタスクを減らすか、量子化を下げてください。")
        return 1

    for path, size in plan:
        fetch(path, args.comfy, size, args.cache)
    for name in args.upscaler:
        fetch_upscaler(name, args.comfy)

    print("\n完了しました")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
