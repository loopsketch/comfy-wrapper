"""ComfyUI の生成をキー認証つき REST で公開する薄いラッパ。

動画 (MiniMax H3 / Wan2.2 / LTX-2.3)、静止画 (Z-Image / Qwen 系)、仕上げ(補間・拡大)を
1本の API で扱う。モデルごとの差はワークフロー生成側 (*_workflows.py) に閉じている。

ComfyUI 自体には認証が無く、ワークフロー投入=任意コード実行なので、
ComfyUI は 127.0.0.1 に閉じたままこのサーバだけをトンネルに出すこと。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import os
import random
import secrets
import shutil
import subprocess
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from PIL import Image

import h3_workflows
import ltx_workflows
import post_workflows
import wan_workflows
from auth import KeyStore
from comfy import ComfyClient, ComfyError, extract_output, failure_reason
import image_workflows
from models import (
    GenerateRequest,
    GenerateResponse,
    ImageGenerateRequest,
    ImageGenerateResponse,
    HealthResponse,
    InfoResponse,
    JobResponse,
    PostprocessRequest,
    PostprocessResponse,
)


def _env(name: str, default: str) -> str:
    """WRAPPER_<name> を読む。旧名 H3_<name> も見る。

    H3 専用だった頃の名前で起動しているスクリプトが残っていても動くようにする。
    モデル指定の H3_FL2VA_MODEL / H3_REF2VA_MODEL / H3_TEXT_ENCODER は H3 固有なので
    そのままの名前で使う。
    """
    return os.environ.get(f"WRAPPER_{name}", os.environ.get(f"H3_{name}", default))


COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188")
KEYS_PATH = Path(_env("KEYS_PATH", "keys.json"))
JOBS_DIR = Path(_env("JOBS_DIR", "jobs"))
POLL_INTERVAL = float(_env("POLL_INTERVAL", "3"))
# クライアント (手元の scripts/ 側) はこれより長く待つ。
# **先に諦めるのはサーバ側**にして、結末をクライアントが受け取れるようにしてある
JOB_TIMEOUT = float(_env("JOB_TIMEOUT", "3600"))
MAX_KEPT_JOBS = int(_env("MAX_KEPT_JOBS", "50"))


@dataclass
class Job:
    id: str
    task: str
    status: str
    created_at: str
    width: int
    height: int
    output_width: int
    output_height: int
    length: int
    seed: int
    slug: str  # ファイル名用。20260806-134512_i2v_a1b2c3d4
    model: str = "minimax-h3"
    fps: int = h3_workflows.FPS
    kind: str = "video"  # video | image
    started_at: str | None = None
    finished_at: str | None = None
    prompt_id: str | None = None
    error: str | None = None
    output_path: Path | None = None
    handle: asyncio.Task | None = field(default=None, repr=False)

    @property
    def seconds(self) -> float:
        """画像には尺の概念が無いので 0 を返す。"""
        return round(self.length / self.fps, 3) if self.kind == "video" else 0.0


jobs: dict[str, Job] = {}
comfy: ComfyClient | None = None
keystore: KeyStore | None = None
bearer = HTTPBearer(auto_error=False)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def require_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> str:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Authorization: Bearer <key> が必要です",
            headers={"WWW-Authenticate": "Bearer"},
        )
    record = keystore.verify(credentials.credentials) if keystore else None
    if record is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "キーが無効です")
    return record.name


def _decode_media(payload: str, label: str) -> bytes:
    """data URI か素の base64 を bytes にする。"""
    raw = payload.split(",", 1)[1] if payload.startswith("data:") else payload
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(400, f"{label} の base64 が不正です: {exc}") from exc


def _aspect_from_image(data: bytes) -> str:
    """画像の縦横比を、H3 が扱うアスペクト候補のうち最も近いものに寄せる。"""
    with Image.open(io.BytesIO(data)) as img:
        ratio = img.width / img.height

    def distance(name: str) -> float:
        w_ratio, h_ratio = h3_workflows.ASPECTS[name]
        return abs(w_ratio / h_ratio - ratio)

    return min(h3_workflows.ASPECTS, key=distance)


# ローダのノードごとに、ウェイトのファイル名が入る入力。投入前の点検に使う
LOADER_INPUTS = {
    "UNETLoader": ("unet_name",),
    "CLIPLoader": ("clip_name",),
    "VAELoader": ("vae_name",),
    "CheckpointLoaderSimple": ("ckpt_name",),
    "LoraLoaderModelOnly": ("lora_name",),
    "LTXAVTextEncoderLoader": ("text_encoder", "ckpt_name"),
    "LTXVAudioVAELoader": ("ckpt_name",),
    "AudioEncoderLoader": ("audio_encoder_name",),
    "FrameInterpolationModelLoader": ("model_name",),
    "LatentUpscaleModelLoader": ("model_name",),
    "UpscaleModelLoader": ("model_name",),
    # ComfyUI 本体には無いノード(custom_nodes の ComfyUI-GGUF)。
    # 入っていなければ object_info が 404 になり、点検は見送られる
    "UnetLoaderGGUF": ("unet_name",),
}

# health で「そのモデルが回せるか」を代表させる (ノード, 入力, ファイル名の一部)。
#
# **ノードの有無では判定できない。** MiniMaxH3ImageToVideo も WanImageToVideo も
# ComfyUI 本体に入っているので、ウェイトを1つも落としていないセッションでも
# ノードは見つかる。
# 量子化違いでファイル名が変わるので、前方一致ではなく部分一致で見る。
READY_ASSETS = {
    "minimax-h3": (
        "UNETLoader",
        "unet_name",
        ["minimax_h3_fl2va", "minimax_h3_ref2va"],
    ),
    "wan2.2": (
        "UNETLoader",
        "unet_name",
        ["wan2.2_i2v_high_noise_14B", "wan2.2_t2v_high_noise_14B"],
    ),
    "wan2.2-5b": ("UNETLoader", "unet_name", ["wan2.2_ti2v_5B"]),
    "wan2.2-s2v": ("UNETLoader", "unet_name", ["wan2.2_s2v_14B"]),
    "ltx-2.3": ("CheckpointLoaderSimple", "ckpt_name", ["ltx-2.3-22b-dev-fp8"]),
    # GGUF はノードごと custom_nodes 側なので、これが立てば導入も済んでいる
    "ltx-2.3-gguf": ("UnetLoaderGGUF", "unet_name", ["ltx-2.3-22b"]),
    # 参照シートつき。IC-LoRA が載っていれば使える(本体は GGUF と共用)
    "ltx-2.3-ic": ("LoraLoaderModelOnly", "lora_name", ["ic-lora-ingredients"]),
    # 仕上げ(補間 + 拡大)。生成モデルとは独立に載る
    "postprocess": (
        "FrameInterpolationModelLoader",
        "model_name",
        ["film_net", "rife"],
    ),
}


async def _missing_weights(workflow: dict) -> list[str]:
    """ワークフローが指しているウェイトのうち、ComfyUI が見つけられないものを返す。

    ノードの有無では分からない。Wan も LTX もノードは ComfyUI 本体に入っているので、
    ウェイトが無いまま投入すると実行時まで気づけず、待ち時間ぶんの課金だけが増える。
    """
    seen: dict[tuple[str, str], list[str]] = {}
    missing = []
    for node in workflow.values():
        for name in LOADER_INPUTS.get(node["class_type"], ()):
            value = node["inputs"].get(name)
            if not isinstance(value, str):
                continue
            key = (node["class_type"], name)
            if key not in seen:
                seen[key] = await comfy.options(*key)
            if seen[key] and value not in seen[key] and value not in missing:
                missing.append(value)
    return missing


async def _video_ready() -> dict[str, bool]:
    """モデルごとにウェイトが載っているか。"""
    ready = {}
    cache: dict[tuple[str, str], list[str]] = {}
    for model, (node, field, fragments) in READY_ASSETS.items():
        key = (node, field)
        if key not in cache:
            cache[key] = await comfy.options(*key)
        ready[model] = any(f in name for f in fragments for name in cache[key])
    return ready


def _prune_jobs() -> None:
    """完了済みジョブの台帳を新しい方から MAX_KEPT_JOBS 件だけ残す。

    生成された mp4 は消さない。出力先を Drive に向けている場合、
    ここで消すと成果物を失うため。古い動画の整理は運用側に任せる。
    """
    finished = sorted(
        (j for j in jobs.values() if j.status in ("succeeded", "failed", "canceled")),
        key=lambda j: j.created_at,
    )
    excess = len(finished) - MAX_KEPT_JOBS
    for job in finished[:excess] if excess > 0 else []:
        jobs.pop(job.id, None)


async def _run_job(
    job: Job, workflow: dict, save_node_id: str | None = None, suffix: str = ".mp4"
) -> None:
    """ComfyUI に投入し、完了まで /history をポーリングして成果物を回収する。"""
    save_node_id = save_node_id or h3_workflows.SAVE_NODE_ID
    try:
        job.prompt_id = await comfy.queue(workflow)
        job.status = "running"
        job.started_at = _now()

        deadline = asyncio.get_running_loop().time() + JOB_TIMEOUT
        while True:
            history = await comfy.history(job.prompt_id)
            if history:
                reason = failure_reason(history)
                if reason:
                    raise ComfyError(reason)
                output = extract_output(history, save_node_id)
                if output:
                    data = await comfy.view(
                        output["filename"],
                        output.get("subfolder", ""),
                        output.get("type", "output"),
                    )
                    JOBS_DIR.mkdir(parents=True, exist_ok=True)
                    job.output_path = JOBS_DIR / f"{job.slug}{suffix}"
                    job.output_path.write_bytes(data)
                    job.status = "succeeded"
                    job.finished_at = _now()
                    return
                raise ComfyError("実行は終わったが出力が見つかりません")
            if asyncio.get_running_loop().time() > deadline:
                raise ComfyError(f"{JOB_TIMEOUT:.0f} 秒を超えたため打ち切りました")
            await asyncio.sleep(POLL_INTERVAL)
    except asyncio.CancelledError:
        job.status = "canceled"
        job.finished_at = _now()
        raise
    except Exception as exc:  # ComfyError, httpx のエラーなど
        job.status = "failed"
        job.error = str(exc)
        job.finished_at = _now()
    finally:
        _prune_jobs()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global comfy, keystore
    comfy = ComfyClient(COMFY_URL)
    keystore = KeyStore(KEYS_PATH)
    if not keystore.records:
        raise RuntimeError(
            f"{KEYS_PATH} に有効なキーがありません。genkey.py で発行してください"
        )
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    yield
    for job in jobs.values():
        if job.handle and not job.handle.done():
            job.handle.cancel()
    await comfy.aclose()


app = FastAPI(title="ComfyUI wrapper on Colab", version="1.0.0", lifespan=lifespan)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """認証不要。トンネルの疎通と ComfyUI の準備状況だけを返す。

    comfy_ready は ComfyUI の生死。どのモデルが回せるかは video_ready で分ける。
    """
    if not await comfy.ready():
        return HealthResponse(status="ok", comfy_ready=False)
    ready = await _video_ready()
    return HealthResponse(
        status="ok",
        comfy_ready=True,
        video_ready=ready,
    )


@app.get("/v1/info", response_model=InfoResponse)
async def info(_: str = Depends(require_key)) -> InfoResponse:
    gpu, total, free = None, None, None
    if shutil.which("nvidia-smi"):
        query = "--query-gpu=name,memory.total,memory.free"
        out = subprocess.run(
            ["nvidia-smi", query, "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
        )
        if out.returncode == 0 and out.stdout.strip():
            name, total_s, free_s = (v.strip() for v in out.stdout.splitlines()[0].split(","))
            gpu, total, free = name, int(total_s), int(free_s)
    return InfoResponse(
        gpu=gpu,
        vram_total_mb=total,
        vram_free_mb=free,
        models=h3_workflows.DEFAULT_MODELS,
        jobs=len(jobs),
    )


def _plan(req: GenerateRequest, aspect: str) -> tuple[int, int, int, int]:
    """モデルごとの寸法・フレーム数・fps を決める。

    latent のグリッドがモデルで違う(H3 は 17k+5、Wan は 4k+1、LTX は 8k+1)ので、
    尺は必ずここで丸めてから組み立てへ渡す。
    """
    if req.model == "minimax-h3":
        width, height = h3_workflows.canvas_size(aspect, req.megapixels)
        length = h3_workflows.clamp_length(h3_workflows.frame_length(req.duration))
        return width, height, length, h3_workflows.FPS
    if req.model in wan_workflows.MODELS:
        width, height = wan_workflows.canvas(req.model, aspect, req.megapixels)
        return width, height, wan_workflows.frame_length(req.model, req.duration), \
            wan_workflows.fps(req.model)
    fps = req.fps or ltx_workflows.DEFAULT_FPS
    width, height = ltx_workflows.canvas(aspect, req.megapixels)
    return width, height, ltx_workflows.frame_length(req.duration, fps), fps


def _check_task(req: GenerateRequest) -> None:
    """モデルが受けられない組み合わせを、投入の前に断る。"""
    if req.task == "i2v" and not req.first_frame:
        raise HTTPException(400, "task=i2v には first_frame が必要です")
    if req.task == "r2v":
        if req.model != "minimax-h3":
            raise HTTPException(400, f"{req.model} は参照つき生成 (r2v) に未対応です")
        if not (req.ref_images or req.ref_videos or req.ref_audios):
            raise HTTPException(
                400, "task=r2v には ref_images / ref_videos / ref_audios のいずれかが必要です"
            )
    if req.model == "ltx-2.3-ic" and not req.ref_images:
        raise HTTPException(
            400, "ltx-2.3-ic には参照シート (ref_images[0]) が必要です"
        )
    if req.ref_images and req.task != "r2v" and req.model != "ltx-2.3-ic":
        raise HTTPException(
            400, f"{req.model} は ref_images を取りません (H3 の r2v か ltx-2.3-ic のみ)"
        )
    if req.model == "wan2.2-s2v" and not req.audio:
        raise HTTPException(400, "wan2.2-s2v には駆動する audio が必要です")
    if req.audio and req.model != "wan2.2-s2v":
        raise HTTPException(
            400,
            f"{req.model} は audio を取りません "
            "(H3 の参照音声は ref_audios、H3 と LTX は音声を自分で生成します)",
        )
    if req.last_frame and (req.model == "wan2.2-5b" or req.model.startswith("ltx-")):
        raise HTTPException(400, f"{req.model} は last_frame に未対応です")
    if req.fps and not req.model.startswith("ltx-"):
        raise HTTPException(400, "fps を指定できるのは ltx-2.3 系だけです")


@app.post("/v1/generate", response_model=GenerateResponse, status_code=202)
async def generate(req: GenerateRequest, _: str = Depends(require_key)) -> GenerateResponse:
    _check_task(req)
    if not await comfy.ready():
        raise HTTPException(503, "ComfyUI がまだ準備できていません")

    first = _decode_media(req.first_frame, "first_frame") if req.first_frame else None
    last = _decode_media(req.last_frame, "last_frame") if req.last_frame else None
    drive_audio = _decode_media(req.audio, "audio") if req.audio else None
    refs = [_decode_media(v, f"ref_images[{i}]") for i, v in enumerate(req.ref_images)]
    videos = [_decode_media(v, f"ref_videos[{i}]") for i, v in enumerate(req.ref_videos)]
    audios = [_decode_media(v, f"ref_audios[{i}]") for i, v in enumerate(req.ref_audios)]

    aspect = req.aspect
    if aspect is None:
        source = first or (refs[0] if refs else None)
        aspect = _aspect_from_image(source) if source else "16x9"
    if aspect not in h3_workflows.ASPECTS:
        raise HTTPException(400, f"未対応のアスペクト: {aspect}")

    width, height, length, fps = _plan(req, aspect)
    seed = req.seed if req.seed >= 0 else random.randrange(2**31)

    job_id = secrets.token_hex(8)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    job = Job(
        id=job_id,
        slug=f"{stamp}_{req.task}_{job_id}",
        task=req.task,
        model=req.model,
        fps=fps,
        status="queued",
        created_at=_now(),
        width=width,
        height=height,
        output_width=req.output_width or width,
        output_height=req.output_height or height,
        length=length,
        seed=seed,
    )

    try:
        first_name = (
            await comfy.upload(first, f"{job.id}_first.png", "image/png") if first else None
        )
        last_name = (
            await comfy.upload(last, f"{job.id}_last.png", "image/png") if last else None
        )
        if req.model == "minimax-h3" and req.task == "r2v":
            names = [
                await comfy.upload(d, f"{job.id}_ref{i}.png", "image/png")
                for i, d in enumerate(refs)
            ]
            video_names = [
                await comfy.upload(d, f"{job.id}_video{i}.mp4", "video/mp4")
                for i, d in enumerate(videos)
            ]
            audio_names = [
                await comfy.upload(d, f"{job.id}_audio{i}.wav", "audio/wav")
                for i, d in enumerate(audios)
            ]
            workflow = h3_workflows.build_ref2va(
                prompt=req.prompt,
                width=width,
                height=height,
                length=length,
                seed=seed,
                steps=req.steps or 20,
                ref_images=names,
                ref_videos=video_names,
                ref_audios=audio_names,
                ref_image_size=req.ref_image_size,
                filename_prefix=f"video/{job.id}",
            )
            upscale = h3_workflows.attach_upscale
            save_node = h3_workflows.SAVE_NODE_ID
        elif req.model == "minimax-h3":
            workflow = h3_workflows.build_fl2va(
                prompt=req.prompt,
                width=width,
                height=height,
                length=length,
                seed=seed,
                steps=req.steps or 20,
                first_frame=first_name,
                last_frame=last_name,
                filename_prefix=f"video/{job.id}",
            )
            upscale = h3_workflows.attach_upscale
            save_node = h3_workflows.SAVE_NODE_ID
        elif req.model in wan_workflows.MODELS:
            audio_name = (
                await comfy.upload(drive_audio, f"{job.id}_drive.wav", "audio/wav")
                if drive_audio
                else None
            )
            workflow = wan_workflows.build(
                model=req.model,
                task=req.task,
                prompt=req.prompt,
                width=width,
                height=height,
                length=length,
                seed=seed,
                negative=req.negative,
                steps=req.steps,
                first_frame=first_name,
                last_frame=last_name,
                audio=audio_name,
                lightning=req.lightning,
                filename_prefix=f"video/{job.id}",
            )
            upscale = wan_workflows.attach_upscale
            save_node = wan_workflows.SAVE_NODE_ID
        else:
            # 参照シートつき (ltx-2.3-ic) も 2段構えのまま回す。単パスの公式構成は
            # カートの形は直った代わりにカメラが破綻した
            sheet = (
                await comfy.upload(refs[0], f"{job.id}_sheet.png", "image/png")
                if req.model == "ltx-2.3-ic"
                else None
            )
            workflow = ltx_workflows.build(
                prompt=req.prompt,
                width=width,
                height=height,
                length=length,
                seed=seed,
                fps=fps,
                negative=req.negative,
                first_frame=first_name,
                ref_sheet=sheet,
                filename_prefix=f"video/{job.id}",
                # ic も GGUF の上に載せる (L4 に収まるのは GGUF だけ)
                gguf=req.model.endswith("-gguf") or req.model == "ltx-2.3-ic",
            )
            upscale = ltx_workflows.attach_upscale
            save_node = ltx_workflows.SAVE_NODE_ID
    except ComfyError as exc:
        raise HTTPException(502, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    upscale(workflow, req.output_width, req.output_height, req.upscale_model)

    # **投入の前にウェイトを点検する。** 無いまま投げると ComfyUI の実行時エラーに
    # なり、そこまでの待ち時間ぶんだけ GPU の課金が乗る
    missing = await _missing_weights(workflow)
    if missing:
        raise HTTPException(
            503,
            f"{req.model} のウェイトが載っていません: {', '.join(missing)}。"
            "そのモデルを含む構築で立て直してください",
        )

    jobs[job.id] = job
    job.handle = asyncio.create_task(_run_job(job, workflow, save_node))
    return GenerateResponse(
        job_id=job.id,
        status=job.status,
        model=job.model,
        fps=fps,
        width=width,
        height=height,
        output_width=job.output_width,
        output_height=job.output_height,
        length=length,
        seconds=job.seconds,
        seed=seed,
    )


@app.post("/v1/postprocess", response_model=PostprocessResponse, status_code=202)
async def postprocess(
    req: PostprocessRequest, _: str = Depends(require_key)
) -> PostprocessResponse:
    """生成済みの動画をフレーム補間・アップスケールして返す。

    生成そのものより **RAM** が効く。4K は 24p・5秒(120フレーム)あたりが
    Colab (RAM 53GB) の現実的な線なので、投入前に見積もりを返す。
    """
    if not await comfy.ready():
        raise HTTPException(503, "ComfyUI がまだ準備できていません")
    if req.multiplier < 2 and not (req.target_width and req.target_height):
        raise HTTPException(400, "補間 (multiplier) か拡大 (target_*) のどちらかは要ります")

    data = _decode_media(req.video, "video")
    job_id = secrets.token_hex(8)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    try:
        name = await comfy.upload(data, f"{job_id}_src.mp4", "video/mp4")
        workflow = post_workflows.build(
            video=name,
            out_fps=req.source_fps * req.multiplier,
            multiplier=req.multiplier,
            target_width=req.target_width,
            target_height=req.target_height,
            upscale_model=req.upscale_model,
            interp_model=req.interp_model,
            keep_audio=req.keep_audio,
            filename_prefix=f"video/{job_id}",
        )
    except ComfyError as exc:
        raise HTTPException(502, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    missing = await _missing_weights(workflow)
    if missing:
        raise HTTPException(
            503,
            f"仕上げ用のモデルが載っていません: {', '.join(missing)}。"
            "postprocess を含む構築で立て直してください",
        )

    # 尺は入力のまま。フレーム数と fps が同じ倍率で増える
    fps = req.source_fps * req.multiplier
    width = req.target_width or 0
    height = req.target_height or 0
    job = Job(
        id=job_id,
        slug=f"{stamp}_post_{job_id}",
        task="postprocess",
        model="postprocess",
        fps=int(round(fps)) or 1,
        status="queued",
        created_at=_now(),
        width=width,
        height=height,
        output_width=width,
        output_height=height,
        length=0,  # 入力のフレーム数はサーバ側では数えない
        seed=0,
    )
    jobs[job.id] = job
    job.handle = asyncio.create_task(
        _run_job(job, workflow, post_workflows.SAVE_NODE_ID)
    )
    return PostprocessResponse(
        job_id=job.id,
        status=job.status,
        width=width,
        height=height,
        fps=fps,
        length=0,
        seconds=0.0,
        ram_estimate_gb=round(
            post_workflows.ram_estimate_gb(width or 1920, height or 1080, 120), 1
        ),
    )


@app.post("/v1/images/generate", response_model=ImageGenerateResponse, status_code=202)
async def generate_image(
    req: ImageGenerateRequest, _: str = Depends(require_key)
) -> ImageGenerateResponse:
    """静止画を1枚生成する。参照画像を渡すと Qwen-Image-Edit の編集経路になる。"""
    if not await comfy.ready():
        raise HTTPException(503, "ComfyUI がまだ準備できていません")

    seed = req.seed if req.seed >= 0 else secrets.randbelow(2**31)
    loras = [(name, float(strength)) for name, strength in req.loras]
    job_id = secrets.token_hex(8)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    try:
        width, height = image_workflows.canvas_size(req.aspect)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if req.ref_images:
        # 参照画像は ComfyUI の input/ に置いてからファイル名で指す
        names = []
        for i, payload in enumerate(req.ref_images):
            data = _decode_media(payload, f"ref_images[{i}]")
            names.append(await comfy.upload(data, f"{job_id}_ref{i}.png", "image/png"))
        # 出力の寸法は aspect で決まる。参照の寸法は引き継がない
        workflow = image_workflows.build_edit(
            req.prompt, names, aspect=req.aspect, seed=seed, negative=req.negative,
            loras=loras, filename_prefix=f"cw_{job_id}", steps=req.steps,
        )
        model = "qwen-image-edit"
    else:
        if req.model not in image_workflows.MODELS:
            raise HTTPException(400, f"未知のモデル: {req.model}")
        workflow = image_workflows.build_t2i(
            req.model, req.prompt, aspect=req.aspect, seed=seed,
            negative=req.negative, loras=loras,
            filename_prefix=f"cw_{job_id}", steps=req.steps,
        )
        model = req.model

    job = Job(
        id=job_id,
        task=model,
        kind="image",
        status="queued",
        created_at=_now(),
        width=width,
        height=height,
        output_width=width,
        output_height=height,
        length=1,
        seed=seed,
        slug=f"{stamp}_{model}_{job_id}",
    )
    jobs[job.id] = job
    job.handle = asyncio.create_task(
        _run_job(job, workflow, image_workflows.SAVE_NODE_ID, ".png")
    )
    return ImageGenerateResponse(
        job_id=job.id, status=job.status, model=model,
        width=width, height=height, seed=seed,
    )


def _get_job(job_id: str) -> Job:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "そのジョブはありません")
    return job


def _job_response(job: Job, position: int | None = None) -> JobResponse:
    available = bool(job.output_path and job.output_path.exists())
    return JobResponse(
        job_id=job.id,
        status=job.status,
        task=job.task,
        model=job.model,
        fps=job.fps,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        queue_position=position,
        width=job.width,
        height=job.height,
        output_width=job.output_width,
        output_height=job.output_height,
        length=job.length,
        seconds=job.seconds,
        seed=job.seed,
        error=job.error,
        kind=job.kind,
        video_available=available,
        video_bytes=job.output_path.stat().st_size if available else None,
    )


@app.get("/v1/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, _: str = Depends(require_key)) -> JobResponse:
    job = _get_job(job_id)
    position = None
    if job.status in ("queued", "running") and job.prompt_id:
        position = await comfy.queue_position(job.prompt_id)
    return _job_response(job, position)


def _finished_output(job_id: str, kind: str) -> Job:
    job = _get_job(job_id)
    if job.kind != kind:
        raise HTTPException(409, f"このジョブは {job.kind} です")
    if job.status != "succeeded" or not job.output_path or not job.output_path.exists():
        raise HTTPException(409, f"まだ取得できません (status={job.status})")
    return job


@app.get("/v1/jobs/{job_id}/video")
async def get_video(job_id: str, _: str = Depends(require_key)) -> FileResponse:
    job = _finished_output(job_id, "video")
    return FileResponse(job.output_path, media_type="video/mp4", filename=f"{job.slug}.mp4")


@app.get("/v1/jobs/{job_id}/image")
async def get_image(job_id: str, _: str = Depends(require_key)) -> FileResponse:
    job = _finished_output(job_id, "image")
    return FileResponse(job.output_path, media_type="image/png", filename=f"{job.slug}.png")


@app.delete("/v1/jobs/{job_id}", status_code=204)
async def cancel_job(job_id: str, _: str = Depends(require_key)) -> None:
    job = _get_job(job_id)
    if job.handle and not job.handle.done():
        job.handle.cancel()
    if job.prompt_id and job.status == "running":
        await comfy.interrupt()


@app.get("/v1/jobs", response_model=list[JobResponse])
async def list_jobs(_: str = Depends(require_key)) -> list[JobResponse]:
    return [await get_job(j.id) for j in sorted(jobs.values(), key=lambda j: j.created_at)]
