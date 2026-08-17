#!/usr/bin/env python3
"""公開された API に 1 本だけ投げて疎通を確かめる。

  docker compose run --rm client src/scripts/smoke_test.py

宛先は tunnel サービスで固定されるので、通常は --endpoint も --key も要らない
(cloudflared の quick tunnel を使っていた頃は URL が毎回変わるので必須だった)。
Colab 以外に立てたときだけ --endpoint で上書きする。
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.request

from pathlib import Path

# scripts/ の隣にある lib/ を通す (実行は python src/scripts/xxx.py の形なので
# パッケージとしては解決されない)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import colab_link

DEFAULT_PROMPT = (
    "A neon-lit Tokyo alley at night after rain, slow dolly-in past steaming ramen stall, "
    "reflections on wet asphalt, distant city hum and light rain on metal awnings."
)


def call(endpoint: str, key: str, method: str, path: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{endpoint.rstrip('/')}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            return res.read()
    except urllib.error.HTTPError as exc:
        print(f"{method} {path} -> {exc.code}: {exc.read().decode(errors='replace')[:500]}", file=sys.stderr)
        raise SystemExit(1) from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default=colab_link.read_endpoint())
    parser.add_argument("--key", default=colab_link.read_api_key())
    parser.add_argument("--out", default="smoke.mp4", type=Path)
    parser.add_argument("--model", help="省略時はサーバの既定 (minimax-h3)")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--aspect", default="16x9")
    parser.add_argument("--megapixels", type=float, default=0.4)
    parser.add_argument("--first-frame", type=Path, help="指定すると i2v で生成する")
    parser.add_argument(
        "--last-frame",
        type=Path,
        help="末尾フレーム。--first-frame と一緒に渡す (ltx-2.5 / wan2.2 / H3)",
    )
    parser.add_argument("--output-size", help="出力サイズ。例 1920x1080")
    parser.add_argument("--upscale-model", help="upscale_models/ のファイル名")
    args = parser.parse_args()

    info = json.loads(call(args.endpoint, args.key, "GET", "/v1/info"))
    print(f"GPU: {info['gpu']} / VRAM {info['vram_free_mb']}MB free")

    body = {
        "task": "i2v" if args.first_frame else "t2v",
        "prompt": args.prompt,
        "duration": args.duration,
        "aspect": args.aspect,
        "megapixels": args.megapixels,
    }
    if args.model:
        body["model"] = args.model
    if args.first_frame:
        body["first_frame"] = base64.b64encode(args.first_frame.read_bytes()).decode()
    if args.last_frame:
        body["last_frame"] = base64.b64encode(args.last_frame.read_bytes()).decode()
    if args.output_size:
        width, height = (int(v) for v in args.output_size.lower().split("x"))
        body["output_width"], body["output_height"] = width, height
    if args.upscale_model:
        body["upscale_model"] = args.upscale_model

    job = json.loads(call(args.endpoint, args.key, "POST", "/v1/generate", body))
    canvas = f"{job['width']}x{job['height']}"
    output = f"{job['output_width']}x{job['output_height']}"
    size = canvas if canvas == output else f"{canvas} -> {output}"
    print(f"job {job['job_id']}: {size} {job['seconds']}s seed={job['seed']}")

    started = time.time()
    while True:
        state = json.loads(call(args.endpoint, args.key, "GET", f"/v1/jobs/{job['job_id']}"))
        elapsed = int(time.time() - started)
        position = state.get("queue_position")
        suffix = f" (待ち {position})" if position else ""
        print(f"\r[{elapsed:4d}s] {state['status']}{suffix}   ", end="", flush=True)
        if state["status"] == "succeeded":
            print()
            break
        if state["status"] in ("failed", "canceled"):
            print(f"\n失敗: {state.get('error')}", file=sys.stderr)
            return 1
        time.sleep(10)

    video = call(args.endpoint, args.key, "GET", f"/v1/jobs/{job['job_id']}/video")
    out = args.out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(video)
    # 書いたつもりの値ではなく、実際にディスク上にあるサイズを報告する
    print(f"保存: {out} ({out.stat().st_size / 1e6:.1f} MB, {elapsed}秒)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
