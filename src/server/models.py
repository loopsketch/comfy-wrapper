"""リクエスト/レスポンスのスキーマ。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Task = Literal["t2v", "i2v", "r2v"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "canceled"]

# 動画モデル。r2v(参照つき)は minimax-h3 だけ、音声つき生成は h3 と LTX 系。
# ltx-2.3-gguf は Q4_K_M 量子化版(L4 の VRAM に載るのはこちら)。
# ltx-2.5 は int8 量子化の公式ウェイトで、first_frame と last_frame の両方を取れる。
VideoModel = Literal[
    "minimax-h3", "wan2.2", "wan2.2-5b", "wan2.2-s2v",
    "ltx-2.3", "ltx-2.3-gguf", "ltx-2.3-ic", "ltx-2.5",
]


class GenerateRequest(BaseModel):
    model: VideoModel = "minimax-h3"
    task: Task = "i2v"
    prompt: str = Field(..., min_length=1)
    negative: str | None = Field(None, description="Wan / LTX 用。省略時はモデルの既定")

    # 画像は data URI か素の base64。i2v は first_frame 必須。
    first_frame: str | None = None
    last_frame: str | None = None
    ref_images: list[str] = Field(default_factory=list, max_length=9)
    ref_videos: list[str] = Field(default_factory=list, max_length=3)
    ref_audios: list[str] = Field(default_factory=list, max_length=3)
    # 口と動きを駆動する音声(wan2.2-s2v 専用)。H3 の ref_audios とは役割が違う
    audio: str | None = Field(None, description="base64 か data URI。wan2.2-s2v のみ")

    duration: float = Field(5.0, ge=0.2, le=20.0, description="秒。モデルのフレームグリッドに切り上げ")
    aspect: str | None = Field(None, description="16x9 / 9x16 / 1x1 など。省略時は first_frame から判定")
    megapixels: float = Field(0.4, ge=0.1, le=2.5)
    fps: int | None = Field(None, ge=8, le=50, description="LTX のみ可変。省略時はモデルの既定")
    steps: int | None = Field(None, ge=1, le=60, description="省略時はモデルの既定")
    seed: int = Field(-1, description="-1 でランダム")
    lightning: bool = Field(
        True, description="Wan2.2 14B で 4steps の蒸留 LoRA を使う(速いが動きは大人しい)"
    )
    ref_image_size: Literal["match", "max"] = "match"

    # 生成後の拡大。両方指定するとそのサイズちょうどで出力する(center crop)
    output_width: int | None = Field(None, ge=64, le=3840)
    output_height: int | None = Field(None, ge=64, le=2160)
    upscale_model: str | None = Field(
        None, description="ComfyUI の upscale_models/ のファイル名。省略時は lanczos"
    )


class GenerateResponse(BaseModel):
    job_id: str
    status: JobStatus
    model: str = "minimax-h3"
    fps: int = 24
    width: int
    height: int
    output_width: int
    output_height: int
    length: int
    seconds: float
    seed: int


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    # 動画は t2v/i2v/r2v、静止画はモデル名(z-image 等)が入る
    task: str
    model: str = "minimax-h3"
    fps: int = 24
    kind: str = "video"
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    queue_position: int | None = None
    width: int
    height: int
    output_width: int
    output_height: int
    length: int
    seconds: float
    seed: int
    error: str | None = None
    video_available: bool = False
    video_bytes: int | None = None


class PostprocessRequest(BaseModel):
    """生成済みの動画を仕上げる(フレーム補間 + アップスケール)。

    **尺は変えない。** 補間で増えたぶん fps を上げるので、16fps を multiplier=3 に
    すると 48fps になる。24p が要るなら手元の ffmpeg で 2枚に1枚落とす。
    """

    video: str = Field(..., description="mp4 の base64 か data URI")
    source_fps: float = Field(..., gt=0, description="入力の fps。出力 fps の計算に使う")
    multiplier: int = Field(1, ge=1, le=16, description="1 なら補間しない")
    target_width: int | None = Field(None, ge=64, le=7680)
    target_height: int | None = Field(None, ge=64, le=4320)
    upscale_model: str | None = None
    interp_model: str | None = None
    keep_audio: bool = True


class PostprocessResponse(BaseModel):
    job_id: str
    status: JobStatus
    width: int
    height: int
    fps: float
    length: int
    seconds: float
    ram_estimate_gb: float


class ImageGenerateRequest(BaseModel):
    """静止画の生成。参照画像を渡すと Qwen-Image-Edit で編集・合成になる。"""

    model: str = Field("z-image", description="z-image / qwen-image / qwen-image-edit")
    prompt: str = Field(..., min_length=1)
    negative: str | None = None
    aspect: str = Field("1x1", description="16x9 / 9x16 / 1x1")
    seed: int = Field(-1, description="-1 でランダム")
    steps: int | None = Field(None, ge=1, le=60, description="省略時はモデルの既定")
    # 参照画像。base64 か data URI。渡すと model の指定によらず edit 経路になる
    ref_images: list[str] = Field(default_factory=list, max_length=3)
    # (LoRA のファイル名, 強さ)
    loras: list[tuple[str, float]] = Field(default_factory=list, max_length=4)


class ImageGenerateResponse(BaseModel):
    job_id: str
    status: JobStatus
    model: str
    width: int
    height: int
    seed: int


class HealthResponse(BaseModel):
    status: str
    comfy_ready: bool
    # モデル名 -> ウェイトが載っているか。以前あった h3_ready はこの minimax-h3 に一本化した
    video_ready: dict[str, bool] = Field(default_factory=dict)


class InfoResponse(BaseModel):
    gpu: str | None
    vram_total_mb: int | None
    vram_free_mb: int | None
    models: dict[str, str]
    jobs: int
