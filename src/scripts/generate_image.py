"""静止画を1枚生成して手元へ回収する。cw image / cw jobs の実体。

    # テキストから
    cw image "a cat on a neon-lit rooftop" --model z-image --aspect 9x16

    # 参照画像つき(渡すと model によらず Qwen-Image-Edit の編集経路になる)
    cw image "image 1 の人物が公園のベンチに座っている" --ref ./ref.png

    # 待たずに投げて、あとでまとめて回収する
    cw image "..." --no-wait
    cw jobs

既定では完成まで待って png を書き出す(Z-Image で数十秒)。まとめて投げたいときは
--no-wait で投入だけして、status で回収する。**ジョブ台帳はサーバのメモリにしかない**
ので、ランタイムを止めると回収できなくなる。止める前に status を通すこと。

**出力は CWD 相対、台帳はリポジトリ。** --out は呼んだ場所からの相対で受け、台帳へは
絶対パスで書く。別のディレクトリから cw jobs を叩いても同じ場所へ回収できる。
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from datetime import datetime
from pathlib import Path

# scripts/ の隣にある lib/ を通す (実行は python src/scripts/xxx.py の形なので
# パッケージとしては解決されない)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import colab_link

# 台帳は呼ぶ側のプロジェクトを汚さないようリポジトリ側に集約する
STATE = colab_link.JOBS_DIR / "images.json"
TERMINAL = ("succeeded", "failed", "canceled")

ENDPOINT = colab_link.read_endpoint()


def _req(method: str, path: str, payload: dict | None = None, timeout: float = 120.0):
    key = colab_link.require_api_key()
    return colab_link.request(ENDPOINT, key, method, path, payload, timeout=timeout)[1]


def _load() -> list[dict]:
    try:
        return json.loads(STATE.read_text())
    except (OSError, json.JSONDecodeError):
        return []


def _save(jobs: list[dict]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(jobs, ensure_ascii=False, indent=2))


def _parse_lora(value: str) -> tuple[str, float]:
    """`ファイル名:強さ` を (名前, 強さ) にする。強さは省略で 1.0。"""
    name, _, strength = value.partition(":")
    return name, float(strength) if strength else 1.0


def _b64(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"参照画像がありません: {path}")
    return base64.b64encode(path.read_bytes()).decode()


def _hint(error: str | None) -> str:
    """ComfyUI の生のエラーに、次の一手を足す。"""
    if error and "value_not_in_list" in error:
        return ("\n  ヒント: そのウェイトはこのセッションに入っていません。"
                "構築したモデル (--models) と --model を揃えてください")
    return ""


def _collect(job_id: str, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(_req("GET", f"/v1/jobs/{job_id}/image", timeout=300))
    return out


def cmd_submit(args) -> int:
    health = colab_link.health(ENDPOINT)
    if not health.get("comfy_ready"):
        print("ComfyUI がまだ準備できていません。構築の進捗を見てください:")
        print("  cw status")
        return 1

    payload = {
        "model": args.model,
        "prompt": args.prompt,
        "aspect": args.aspect,
        "seed": args.seed,
    }
    if args.negative:
        payload["negative"] = args.negative
    if args.steps:
        payload["steps"] = args.steps
    if args.ref:
        payload["ref_images"] = [_b64(p) for p in args.ref]
    if args.lora:
        payload["loras"] = [_parse_lora(v) for v in args.lora]

    job = json.loads(_req("POST", "/v1/images/generate", payload))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    # 台帳に相対パスを書くと、別の CWD から回収したときに違う場所へ落ちる
    out = (args.out or Path(f"{stamp}_{job['model']}_{job['job_id']}.png")).resolve()

    print(f"投入した: {job['job_id']} / {job['model']} / "
          f"{job['width']}x{job['height']} / seed={job['seed']}")

    if args.no_wait:
        jobs = _load()
        jobs.append({
            "job_id": job["job_id"],
            "model": job["model"],
            "prompt": args.prompt,
            "out": str(out),
            "submitted_at": stamp,
        })
        _save(jobs)
        print("回収は status で。**ランタイムを止める前に回収すること**")
        return 0

    started = time.time()
    deadline = started + args.timeout
    while time.time() < deadline:
        time.sleep(args.interval)
        state = json.loads(_req("GET", f"/v1/jobs/{job['job_id']}"))
        if state["status"] not in TERMINAL:
            continue
        elapsed = time.time() - started
        if state["status"] != "succeeded":
            print(f"失敗: {state['status']} / {state.get('error')}{_hint(state.get('error'))}")
            return 1
        path = _collect(job["job_id"], out)
        print(f"書き出した: {path} ({path.stat().st_size / 1024:.0f}KB, {elapsed:.0f}秒)")
        return 0

    # **待ちを打ち切っても生成は裏で走っている。** 台帳に残して status で拾えるようにする
    jobs = _load()
    jobs.append({
        "job_id": job["job_id"], "model": job["model"], "prompt": args.prompt,
        "out": str(out), "submitted_at": stamp,
    })
    _save(jobs)
    print(f"{args.timeout}秒で待つのをやめました。生成は続いています。status で回収してください")
    return 1


def cmd_status(args) -> int:
    jobs = _load()
    if not jobs:
        print("回収待ちのジョブはありません")
        return 0

    remaining = []
    for entry in jobs:
        try:
            state = json.loads(_req("GET", f"/v1/jobs/{entry['job_id']}"))
        except colab_link.LinkError as exc:
            # セッションを立て直すと台帳ごと消える。1件で全体を落とさない
            print(f"{entry['job_id']}: 取得できません ({exc})")
            remaining.append(entry)
            continue

        line = f"{entry['job_id']}: {state['status']}"
        if state.get("queue_position") is not None:
            line += f" (待ち {state['queue_position']})"
        if state.get("error"):
            line += f" / {state['error']}"
        print(line)

        if state["status"] == "succeeded":
            path = _collect(entry["job_id"], Path(entry["out"]))
            print(f"  書き出した: {path} ({path.stat().st_size / 1024:.0f}KB)")
        elif state["status"] not in TERMINAL:
            remaining.append(entry)

    _save(remaining)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("submit", help="1枚生成する(既定では完成まで待つ)")
    p.add_argument("prompt")
    p.add_argument("--model", default="z-image",
                   choices=["z-image", "qwen-image", "qwen-image-edit"])
    p.add_argument("--aspect", default="1x1", choices=["16x9", "9x16", "1x1"])
    p.add_argument("--ref", type=Path, action="append",
                   help="参照画像。最大3枚。渡すと model によらず edit 経路になる")
    p.add_argument("--lora", action="append", help="ファイル名[:強さ]。最大4本")
    p.add_argument("--negative")
    p.add_argument("--seed", type=int, default=-1)
    p.add_argument("--steps", type=int, help="省略時はモデルの既定 (Z-Image 8 / Qwen 4)")
    p.add_argument("--out", type=Path, help="既定は CWD に日時つきで書く")
    p.add_argument("--no-wait", action="store_true", help="投入だけして status で回収する")
    p.add_argument("--timeout", type=float, default=300.0, help="待つ上限(秒)")
    p.add_argument("--interval", type=float, default=5.0)
    p.set_defaults(func=cmd_submit)

    p = sub.add_parser("status", help="投入済みのジョブを見て、終わっていれば回収する")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args()
    try:
        return args.func(args)
    except colab_link.LinkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
