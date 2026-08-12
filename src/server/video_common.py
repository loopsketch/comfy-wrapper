"""動画モデル共通の寸法・尺の計算と、出力段の拡大。

H3 / Wan2.2 / LTX-2.3 でモデルごとに違うのは「latent のグリッド」だけで、
アスペクトの扱いと出力サイズの揃え方は共通なので、ここにまとめてある。
"""

from __future__ import annotations

import math

ASPECTS = {
    "16x9": (16, 9),
    "9x16": (9, 16),
    "1x1": (1, 1),
    "4x3": (4, 3),
    "3x4": (3, 4),
    "21x9": (21, 9),
}


def canvas_size(aspect: str, megapixels: float, multiple: int = 32) -> tuple[int, int]:
    """アスペクト比と目標画素数から、multiple の倍数に丸めた width/height を返す。

    ComfyUI の ResolutionSelector と同じ計算にしてある。1 megapixel は
    1e6 ではなく 1024x1024 で、幅と高さはそれぞれ独立に丸められる。
    multiple はモデルの latent 圧縮率で決まる(H3/Wan14B は 32、
    Wan2.2-5B は 32、LTX は半解像度で 32 の倍数が要るので 64)。
    """
    if aspect not in ASPECTS:
        raise ValueError(f"未対応のアスペクト: {aspect}")
    w_ratio, h_ratio = ASPECTS[aspect]
    scale = math.sqrt(megapixels * 1024 * 1024 / (w_ratio * h_ratio))
    width = round(w_ratio * scale / multiple) * multiple
    height = round(h_ratio * scale / multiple) * multiple
    return max(multiple, width), max(multiple, height)


def grid_length(
    seconds: float,
    fps: int,
    grid: int,
    offset: int = 1,
    min_length: int = 1,
    max_length: int | None = None,
) -> int:
    """秒数を latent のフレームグリッド (grid*k + offset) に切り上げて収める。

    Wan は 4k+1、LTX は 8k+1、H3 は 17k+5。丸めた結果が学習レンジを外れるときは、
    グリッドに乗ったまま内側へ寄せる。
    """
    length = max(1, round(seconds * fps))
    length += (offset - length) % grid
    if length < min_length:
        length = min_length + (offset - min_length) % grid
    if max_length is not None and length > max_length:
        length = max_length - ((max_length - offset) % grid)
    return length


def attach_upscale(
    wf: dict,
    target_width: int | None,
    target_height: int | None,
    model_name: str | None,
    *,
    images: list,
    video_node_id: str,
    node_prefix: str = "9",
) -> dict:
    """デコードと CreateVideo の間に拡大を挟み、出力を目標サイズちょうどにする。

    生成キャンバスは 16:9 ちょうどとは限らないため、1920x1080 のような厳密な
    サイズが要る場合はアスペクト差を center crop で吸収する。model_name を渡すと
    アップスケールモデル(RealESRGAN 等)を通してから目標サイズへ落とす。

    **拡大しても情報量は増えない。** 増えるのは画素数だけで、精細さが要るカットは
    生成解像度そのものを上げる方が効く。
    """
    if not (target_width and target_height):
        return wf

    # **既存ノードを踏まないこと。** 拡大段は node_prefix から 3つの ID を作るので、
    # 本体側が同じ番号を使っていると黙って上書きし、その出力を参照している
    # ノードが「tuple index out of range」で落ちる(2026-08-12 に IC-LoRA で踏んだ。
    # 60/61/62 が LoRA・パラメータ・AddGuide と衝突していた)
    taken = [f"{node_prefix}{i}" for i in (0, 1, 2) if f"{node_prefix}{i}" in wf]
    if taken:
        raise ValueError(
            f"拡大段のノード ID が本体と衝突しています: {', '.join(taken)}"
        )

    source = images
    if model_name:
        wf[f"{node_prefix}0"] = {
            "class_type": "UpscaleModelLoader",
            "inputs": {"model_name": model_name},
        }
        wf[f"{node_prefix}1"] = {
            "class_type": "ImageUpscaleWithModel",
            "inputs": {"upscale_model": [f"{node_prefix}0", 0], "image": source},
        }
        source = [f"{node_prefix}1", 0]

    wf[f"{node_prefix}2"] = {
        "class_type": "ImageScale",
        "inputs": {
            "image": source,
            "upscale_method": "lanczos",
            "width": target_width,
            "height": target_height,
            "crop": "center",
        },
    }
    wf[video_node_id]["inputs"]["images"] = [f"{node_prefix}2", 0]
    return wf
