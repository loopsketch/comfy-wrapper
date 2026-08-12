"""生成済みの動画を仕上げる後処理(フレーム補間 + アップスケール)。

ComfyUI 公式ブループリント (`blueprints/frame_interpolation.json` /
`video_upscale_gan_x4.json`) と同じ構成で、どちらも **ComfyUI 本体のノードだけ**で
組める(カスタムノードは要らない)。

    LoadVideo -> GetVideoComponents -> FrameInterpolate -> ImageUpscaleWithModel
              -> ImageScale(目標ぴったり) -> CreateVideo -> SaveVideo

**補間を先に、拡大を後に置く。** 逆にすると 4K のフレーム同士で補間することになり、
メモリも時間も跳ね上がる。補間は元の解像度で回して、増えたフレームを拡大する。

**尺は変えない。** 補間で増えたフレーム数に合わせて出力 fps も上げる
(16fps を multiplier=3 にしたら 48fps)。24p が要るなら、48fps から
ffmpeg で 2枚に1枚落とす(等間隔なので破綻しない)。

メモリの目安。大きいテンソルは CPU 側(intermediate_device)に載るので、
効くのは VRAM ではなく **システム RAM**。

    3840x2160 x 120フレーム x fp32 = 約 11GB
    3840x2160 x 240フレーム x fp32 = 約 22GB

Colab の L4 ランタイムは RAM 53GB なので、4K は 24p・5秒(120フレーム)が現実的な線。
"""

from __future__ import annotations

import os

# 公式ブループリントの既定。x4 の GAN と FILM の補間モデル
UPSCALE_MODEL = os.environ.get("CW_UPSCALE_MODEL", "RealESRGAN_x4plus.safetensors")
INTERP_MODEL = os.environ.get("CW_INTERP_MODEL", "film_net_fp16.safetensors")

SAVE_NODE_ID = "9"
VIDEO_NODE_ID = "8"

# ウェイトが載っているかの判定に使う (ノード, 入力, ファイル名の一部)
READY_ASSETS = {
    "upscale": ("UpscaleModelLoader", "model_name", ["RealESRGAN", "ESRGAN"]),
    "interp": ("FrameInterpolationModelLoader", "model_name", ["film_net", "rife"]),
}


def build(
    video: str,
    out_fps: float,
    multiplier: int = 1,
    target_width: int | None = None,
    target_height: int | None = None,
    upscale_model: str | None = None,
    interp_model: str | None = None,
    keep_audio: bool = True,
    filename_prefix: str = "video/cw_post",
) -> dict:
    """後処理のワークフローを返す。

    multiplier=1 なら補間しない。target_width/height を省くと拡大しない。
    どちらも省くと何も起きないので、呼び出し側で弾くこと。
    """
    if multiplier < 1:
        raise ValueError("multiplier は 1 以上にしてください")
    if bool(target_width) != bool(target_height):
        raise ValueError("target_width と target_height は両方を指定してください")

    wf: dict = {
        "1": {"class_type": "LoadVideo", "inputs": {"file": video}},
        "2": {"class_type": "GetVideoComponents", "inputs": {"video": ["1", 0]}},
    }
    images = ["2", 0]

    if multiplier > 1:
        wf["3"] = {
            "class_type": "FrameInterpolationModelLoader",
            "inputs": {"model_name": interp_model or INTERP_MODEL},
        }
        wf["4"] = {
            "class_type": "FrameInterpolate",
            "inputs": {
                "interp_model": ["3", 0],
                "images": images,
                "multiplier": multiplier,
            },
        }
        images = ["4", 0]

    if target_width and target_height:
        wf["5"] = {
            "class_type": "UpscaleModelLoader",
            "inputs": {"model_name": upscale_model or UPSCALE_MODEL},
        }
        wf["6"] = {
            "class_type": "ImageUpscaleWithModel",
            "inputs": {"upscale_model": ["5", 0], "image": images},
        }
        # GAN の倍率は固定(x4 など)なので、目標サイズちょうどに落とす。
        # アスペクトのずれは center crop で吸収する
        wf["7"] = {
            "class_type": "ImageScale",
            "inputs": {
                "image": ["6", 0],
                "upscale_method": "lanczos",
                "width": target_width,
                "height": target_height,
                "crop": "center",
            },
        }
        images = ["7", 0]

    video_inputs = {"images": images, "fps": out_fps, "bit_depth": 8}
    if keep_audio:
        # 元動画の音声をそのまま持ち越す(H3 / LTX / S2V は音がある)
        video_inputs["audio"] = ["2", 1]
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
    return wf


def output_frames(source_frames: int, multiplier: int) -> int:
    """補間後のフレーム数。両端は据え置きなので (N-1)*m + 1 になる。"""
    if multiplier < 2 or source_frames < 2:
        return source_frames
    return (source_frames - 1) * multiplier + 1


# 倍率ごとのアップスケールモデル。**ソースと目標の比に合わせて選ぶ。**
# 1080p に x4 を当てると中間が 7680x4352 になり、120フレームで約 45GB に達する
# (目標が 4K でも、モデルの出力は目標ではなく「入力 x 倍率」で決まる)
UPSCALE_MODELS = {
    2: os.environ.get("CW_UPSCALE_X2", "RealESRGAN_x2.pth"),
    4: UPSCALE_MODEL,
}


def pick_upscale(src_width: int, src_height: int, dst_width: int, dst_height: int) -> int:
    """必要な倍率に近い方のモデルを選ぶ。返すのは 2 か 4。"""
    ratio = max(dst_width / src_width, dst_height / src_height)
    return 2 if ratio <= 2.2 else 4


def ram_estimate_gb(
    width: int,
    height: int,
    frames: int,
    scale: int = 1,
    target: tuple[int, int] | None = None,
) -> float:
    """仕上げのピークで要るシステム RAM のおおよそ(fp32 の連続テンソル)。

    **中間と最終が同時に載る。** 拡大モデルは「入力 x 倍率」を吐き、そのあと
    ImageScale が目標サイズの別テンソルを作るので、ピークは2本の合計になる。
    倍率は目標ではなくモデル側で決まるので、両方を数えないと過小評価する。
    """
    px = (width * scale) * (height * scale)
    if target:
        px += target[0] * target[1]
    return px * 3 * 4 * frames / 1024**3
