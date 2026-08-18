"""どのモデルで何ができるかを、1か所にまとめた表。

**呼ぶ側にこの表を写させない。** 以前はモデルごとの解像度・音声の有無・スループットを
利用側 (music-video-creator2) が自前の表として持っていて、こちらに `ltx-2.5` を足しても
向こうは気づけなかった。結果、生成自体は通るのにコスト台帳へ0円で記録される、という
静かな食い違いが残った。表はこのサーバが持ち、`GET /v1/models` で配る。

ここが持つのは**ランタイムが無くても分かること**だけにする。ウェイトが実際に載って
いるかどうか (`ready`) はランタイムの状態なので、`/health` の `video_ready` から
API 層で合成する。見積もりは GPU を確保する前に出せる必要があり、そこを混ぜると
「見積もるにはまず確保してください」という本末転倒になる。

スループット (`seconds_per_output_second`) は「出力1秒あたり、GPU を何秒握るか」。
480p・ロード済みの実測で、解像度を上げるとほぼ画素数に比例して伸びる。**枚数でも
本数でもなく GPU の占有時間で課金される**ので、コストはここから引く。
"""

from __future__ import annotations

from .video_sizes import AUDIO_MODELS, MODEL_RESOLUTIONS

# 480p・ロード済み・L4 での実測 (README のコスト表)。
MEASURED_L4 = {
    "minimax-h3": 77.4,
    "wan2.2": 32.6,
    "ltx-2.3-gguf": 14.0,
    "ltx-2.5": 20.8,
}

# 実測していない構成の概算。**測ったものと混ぜない**ので別に置く。
# 見積もりには使うが、README のコスト表には載せない。
ESTIMATED_L4 = {
    "wan2.2-5b": 32.0,
    "wan2.2-s2v": 29.0,
    "ltx-2.3": 30.0,
    "ltx-2.3-ic": 20.0,
}


def _rate(model: str) -> dict:
    if model in MEASURED_L4:
        return {"L4": MEASURED_L4[model], "measured": True}
    if model in ESTIMATED_L4:
        return {"L4": ESTIMATED_L4[model], "measured": False}
    return {}


# ウェイトの合計 (GB)。setup/download_*.py の実データから出した概数。
_WEIGHTS_GB = {
    "minimax-h3": 42.5,
    "wan2.2": 38.0,
    "wan2.2-5b": 18.1,
    "wan2.2-s2v": 25.2,
    "ltx-2.3": 42.3,
    "ltx-2.3-gguf": 30.1,
    "ltx-2.3-ic": 29.0,
    "ltx-2.5": 39.7,
    "z-image": 11.3,
    "qwen-image": 28.0,
    "qwen-image-edit": 29.7,
}

# 動画モデル。`tasks` は API の task フィールドに渡せる値。
# `last_frame` は first_frame と併せて末尾フレームを置けるか(単独では置けない)。
_VIDEO = {
    "minimax-h3": {
        "tasks": ["t2v", "i2v", "r2v"],
        "last_frame": True,
        "audio_in": False,
        "ref_images": 9,
        "ref_videos": 3,
        "ref_audios": 3,
        "fps": {"default": 24, "min": 24, "max": 24},
        "notes": "参照つき生成 (r2v) はこのモデルだけ。参照はプロンプト側から "
                 "<Picture 1> <Video 1> <Audio 1> で指す",
    },
    "wan2.2": {
        "tasks": ["t2v", "i2v"],
        "last_frame": True,
        "audio_in": False,
        "ref_images": 0, "ref_videos": 0, "ref_audios": 0,
        "fps": {"default": 16, "min": 16, "max": 16},
        "notes": "14B の MoE。4step 蒸留 LoRA (lightning) が既定で入る",
    },
    "wan2.2-5b": {
        "tasks": ["t2v", "i2v"],
        "last_frame": False,
        "audio_in": False,
        "ref_images": 0, "ref_videos": 0, "ref_audios": 0,
        "fps": {"default": 24, "min": 24, "max": 24},
        "notes": "TI2V-5B。480p 生成は学習範囲を外れるので 720p 相当で回す",
    },
    "wan2.2-s2v": {
        "tasks": ["i2v"],
        "last_frame": False,
        "audio_in": True,
        "ref_images": 0, "ref_videos": 0, "ref_audios": 0,
        "fps": {"default": 16, "min": 16, "max": 16},
        "notes": "音声で駆動する。1チャンク 77フレーム (16fps で 4.81秒) が上限",
    },
    "ltx-2.3": {
        "tasks": ["t2v", "i2v"],
        "last_frame": False,
        "audio_in": False,
        "ref_images": 0, "ref_videos": 0, "ref_audios": 0,
        "fps": {"default": 25, "min": 8, "max": 50},
        "notes": "22B fp8。本体 29GB で L4 には載らない (A100 向け)",
    },
    "ltx-2.3-gguf": {
        "tasks": ["t2v", "i2v"],
        "last_frame": False,
        "audio_in": False,
        "ref_images": 0, "ref_videos": 0, "ref_audios": 0,
        "fps": {"default": 25, "min": 8, "max": 50},
        "notes": "Q4_K_M 量子化。L4 で一番速い",
    },
    "ltx-2.3-ic": {
        "tasks": ["i2v"],
        "last_frame": False,
        "audio_in": False,
        "ref_images": 3, "ref_videos": 0, "ref_audios": 0,
        "fps": {"default": 25, "min": 8, "max": 50},
        "notes": "IC-LoRA Ingredients の参照シート。2.5 版はまだ無い",
    },
    "ltx-2.5": {
        "tasks": ["t2v", "i2v"],
        "last_frame": True,
        "audio_in": False,
        "ref_images": 0, "ref_videos": 0, "ref_audios": 0,
        "fps": {"default": 24, "min": 8, "max": 50},
        "notes": "int8 のまま L4 に載り、720p まで素で回る。先頭+末尾を置ける唯一の LTX "
                 "(そのときだけ単パス・フル解像度で、i2v より重い)",
    },
}

# 静止画モデル。尺も fps も無いので、動画とは持ち物が違う。
# `seconds_per_image` は L4 で1枚を出すのにかかる秒数。**枚数単価は無い**ので、
# 動画側と同じく GPU の占有時間からコストを引く。
_IMAGE = {
    "z-image": {
        "ref_images": 0,
        "seconds_per_image": {"L4": 15.0},
        "notes": "8step で速い。参照を渡すと model によらず編集経路になる",
    },
    "qwen-image": {
        "ref_images": 0,
        "seconds_per_image": {"L4": 10.0},
        "notes": "プロンプト追従が強い",
    },
    "qwen-image-edit": {
        "ref_images": 3,
        "seconds_per_image": {"L4": 30.0},
        "notes": "参照画像を渡せる。キャラクタの一貫性はこちら",
    },
}


def _video_entry(model: str) -> dict:
    spec = _VIDEO[model]
    resolutions = {
        name: {"megapixels": mp, "short_edge": edge}
        for name, (mp, edge) in MODEL_RESOLUTIONS[model].items()
    }
    return {
        "id": model,
        "kind": "video",
        # 音声の有無は video_sizes が持っている。ここで書き写すと同じ過ちを繰り返す
        "audio_out": model in AUDIO_MODELS,
        "resolutions": resolutions,
        "weights_gb": _WEIGHTS_GB[model],
        "seconds_per_output_second": _rate(model),
        **spec,
    }


def _image_entry(model: str) -> dict:
    spec = _IMAGE[model]
    return {
        "id": model,
        "kind": "image",
        "weights_gb": _WEIGHTS_GB[model],
        **spec,
    }


def catalog() -> list[dict]:
    """ランタイムが無くても分かるぶんだけを返す。`ready` は API 層で足す。"""
    return [_video_entry(m) for m in _VIDEO] + [_image_entry(m) for m in _IMAGE]


def ids(kind: str | None = None) -> list[str]:
    """モデル ID の一覧。`--model` の選択肢や検証に使う。"""
    return [e["id"] for e in catalog() if kind is None or e["kind"] == kind]
