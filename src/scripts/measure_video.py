"""Wan2.2 / LTX-2.3 / H3 の生成時間を、ロード込みと込みでないので測る。

    # 1本目(ウェイトを VRAM へ積むところから)
    docker compose run --rm client src/scripts/measure_video.py \
      submit works/monochrome-buddy-3/produce/stills/e2c02.png --model wan2.2 --aspect 9x16
    # 進捗を見る(succeeded なら動画も回収する)
    docker compose run --rm client src/scripts/measure_video.py \
      status --model wan2.2
    # 1本目が終わってから2本目を投げ、最後に表を出す
    docker compose run --rm client src/scripts/measure_video.py \
      report --model wan2.2

**1本目が終わってから2本目を投げる。** 続けて投入するとキュー待ちの時間が
2本目の計測に混ざり、「ロードにいくらかかったか」が出せなくなる。

**投げっぱなしにして待たない。** 生成は数分〜数十分かかるので、投入と確認を分けて
短い呼び出しの繰り返しにしてある(長いコマンドを打ち切るとコンテナが孤児になる)。

比較の基準は README のコスト表にある実測。

    A100 / H3 int8 / 480p(0.4MP) / 5.167秒 / 20steps -> 335秒(初回ロード込み)
    L4   / H3 fp8  / 同上                            -> 483秒(初回ロード込み)

条件はモデルをまたいで揃える(同じ静止画・480p 相当・約5秒・同じ seed)。
step 数だけは既定が違う(H3 20 / Wan は蒸留 4 / LTX は 8+3)ので、
「それぞれの既定でどれくらいか」を測っていることになる。
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# scripts/ の隣にある lib/ を通す (実行は python src/scripts/xxx.py の形なので
# パッケージとしては解決されない)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import colab_link
from lib.video_sizes import MODEL_RESOLUTIONS, output_size

# 宛先とキーの解決は colab_link に集約してある(旧名の環境変数も読む)
ENDPOINT = colab_link.read_endpoint()
STATE_DIR = Path("/app/works/.verify")

# Wan / LTX は H3 のような形式プロンプトを取らないので、素のシネマ的な描写で書く。
# 中身は measure_h3.py と同じ場面にしてある(絵も見比べられるように)。
PROMPT = (
    "A young woman in a light denim jacket and striped top pushes a red shopping cart "
    "straight toward the camera down a bright supermarket aisle. She leans over the "
    "handle, grinning, and drives the cart forward at a brisk walk while snack packages "
    "tumble from the basket and scatter across the tiled floor behind her. The camera "
    "tracks backward ahead of her, keeping the cart centered as she advances. "
    "Live-action, cinematic lighting, shallow depth of field."
)

SEED = 12345
# これ以上は動かない状態。lost はセッションが消えて結末が分からなくなったもの
TERMINAL = ("succeeded", "failed", "canceled", "lost")
# 単価は colab_link が正本。ここで表を持たない(同じ数字を2か所に置くと必ずずれる)
RATES = colab_link.GPU_CU_PER_HOUR

# README の実測(初回ロード込み、480p / 約5秒)
BASELINES = {"A100 / H3 int8": 335.0, "L4 / H3 fp8": 483.0}


def _req(method: str, path: str, payload: dict | None = None, timeout: int = 300):
    key = colab_link.require_api_key()
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(f"{ENDPOINT}{path}", data=data, method=method)
    r.add_header("Authorization", f"Bearer {key}")
    if data is not None:
        r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=timeout) as res:
        return res.read()


def _state_path(model: str) -> Path:
    return STATE_DIR / f"measure_{model}.json"


def _load_state(model: str) -> dict:
    path = _state_path(model)
    if path.exists():
        return json.loads(path.read_text())
    return {"model": model, "runs": []}


def _save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _state_path(state["model"]).write_text(
        json.dumps(state, ensure_ascii=False, indent=2)
    )


def _elapsed(job: dict) -> float | None:
    """サーバが記録した実行時間(ComfyUI へ投入してから回収まで)。"""
    if not (job.get("started_at") and job.get("finished_at")):
        return None
    start = datetime.fromisoformat(job["started_at"])
    end = datetime.fromisoformat(job["finished_at"])
    return (end - start).total_seconds()


def cmd_submit(args) -> int:
    if not args.still.exists():
        print(f"画像がありません: {args.still}")
        return 2

    state = _load_state(args.model)
    pending = [r for r in state["runs"] if r.get("status") not in TERMINAL]
    if pending and not args.force:
        print(f"まだ終わっていない run があります: {[r['job_id'] for r in pending]}\n"
              "status で終わるのを待ってから投げてください (--force で無視)")
        return 1

    megapixels, short_edge = MODEL_RESOLUTIONS[args.model][args.resolution]
    width, height = output_size(args.aspect, short_edge)
    payload = {
        "model": args.model,
        "task": "i2v",
        "prompt": args.prompt or PROMPT,
        "first_frame": base64.b64encode(args.still.read_bytes()).decode(),
        "duration": args.duration,
        "aspect": args.aspect,
        "megapixels": megapixels,
        "output_width": width,
        "output_height": height,
        "seed": SEED,
    }
    if args.ref_sheet:
        # ltx-2.3-ic の参照シート。キャラ・小物・場所を1枚に並べたもの
        payload["ref_images"] = [base64.b64encode(args.ref_sheet.read_bytes()).decode()]
    if args.audio:
        # S2V は音声で口と動きを駆動する。画像は先頭フレームではなく参照になる
        payload["audio"] = base64.b64encode(args.audio.read_bytes()).decode()
    if args.no_lightning:
        payload["lightning"] = False

    health = json.loads(urllib.request.urlopen(f"{ENDPOINT}/health", timeout=30).read())
    info = json.loads(_req("GET", "/v1/info"))
    job = json.loads(_req("POST", "/v1/generate", payload))

    run = {
        "run": len(state["runs"]) + 1,
        "job_id": job["job_id"],
        "status": job["status"],
        "gpu": info.get("gpu"),
        "canvas": f"{job['width']}x{job['height']}",
        "output": f"{job['output_width']}x{job['output_height']}",
        "length": job["length"],
        "seconds": job["seconds"],
        "fps": job["fps"],
    }
    state["runs"].append(run)
    state["conditions"] = {
        "still": str(args.still),
        "resolution": args.resolution,
        "megapixels": megapixels,
        "duration": args.duration,
        "aspect": args.aspect,
        "seed": SEED,
    }
    _save_state(state)

    print(f"GPU     : {info.get('gpu')} "
          f"(VRAM 空き {info.get('vram_free_mb')}/{info.get('vram_total_mb')}MB)")
    print(f"載って   : {[k for k, v in health.get('video_ready', {}).items() if v]}")
    print(f"{run['run']}本目を投入: {run['job_id']} "
          f"{run['canvas']} -> {run['output']} / {run['length']}F "
          f"{run['seconds']}秒 @{run['fps']}fps")
    print("進捗は status で見る")
    return 0


def cmd_status(args) -> int:
    state = _load_state(args.model)
    if not state["runs"]:
        print("まだ投入していません")
        return 1

    for run in state["runs"]:
        # **ジョブ台帳はサーバのメモリにしかない。** セッションを立て直すと消えるので、
        # 前回の run が残っていても 404 で全体を落とさない
        try:
            job = json.loads(_req("GET", f"/v1/jobs/{run['job_id']}"))
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            # 終わったものの記録は残す(report が使う)。終わっていなかったものは
            # **lost にして片づける。** running のまま残すと submit のガードが
            # 永久に「まだ終わっていない run がある」と言い続け、そのモデルは
            # --force なしでは二度と投げられなくなる(2026-08-12 に踏んだ)
            if run.get("status") not in TERMINAL:
                run["status"] = "lost"
            print(f"{run['run']}本目 {run['job_id']}: 前のセッションのジョブ (台帳から消えている)")
            continue
        run["status"] = job["status"]
        run["elapsed"] = _elapsed(job)
        line = f"{run['run']}本目 {run['job_id']}: {job['status']}"
        if job.get("queue_position") is not None:
            line += f" (待ち {job['queue_position']})"
        if run["elapsed"]:
            line += f" / {run['elapsed']:.0f}秒"
        if job.get("error"):
            line += f" / {job['error']}"
        print(line)

        if job["status"] == "succeeded" and not run.get("video"):
            out = STATE_DIR / f"measure_{args.model}_{run['run']}.mp4"
            out.write_bytes(_req("GET", f"/v1/jobs/{run['job_id']}/video", timeout=600))
            run["video"] = str(out)
            print(f"  回収した: {out} ({out.stat().st_size / 1024:.0f}KB)")

    _save_state(state)
    done = [r for r in state["runs"] if r["status"] == "succeeded"]
    if len(done) == len(state["runs"]):
        print(f"\n{len(done)}本とも完了。次は report、または 2本目を submit")
    return 0


def cmd_report(args) -> int:
    state = _load_state(args.model)
    runs = [r for r in state["runs"] if r.get("elapsed")]
    if not runs:
        print("完了した run がありません(先に status)")
        return 1

    yen_h = colab_link.yen_per_hour(args.gpu)
    cond = state.get("conditions", {})
    print(f"モデル : {state['model']} / GPU: {runs[0].get('gpu')}")
    print(f"条件   : {cond.get('resolution')} ({cond.get('megapixels')}MP) / "
          f"{cond.get('duration')}秒 / {cond.get('aspect')} / seed={cond.get('seed')}")
    print(f"生成   : {runs[0]['canvas']} -> {runs[0]['output']} / "
          f"{runs[0]['length']}F {runs[0]['seconds']}秒 @{runs[0]['fps']}fps\n")

    for run in runs:
        tag = "ロード込み" if run["run"] == 1 else "ロード済み"
        print(f"{run['run']}本目 ({tag}) : {run['elapsed']:6.0f}秒 "
              f"({run['elapsed'] / 60:4.1f}分) = {yen_h * run['elapsed'] / 3600:5.2f}円")

    if len(runs) >= 2:
        load = runs[0]["elapsed"] - runs[1]["elapsed"]
        steady = runs[1]["elapsed"]
        print(f"\nロードにかかった分 : 約 {load:.0f}秒 ({load / 60:.1f}分)")
        print(f"2本目以降の1本     : {steady:.0f}秒 = {yen_h * steady / 3600:.2f}円")
        print(f"映像1秒あたり      : {steady / runs[1]['seconds']:.1f}秒")
        total = load + steady * 12
        print(f"12カット回すなら   : {total / 60:.0f}分 = {yen_h * total / 3600:.0f}円")

    print("\n-- 基準 (README の実測、初回ロード込み・480p・約5秒)")
    for name, sec in BASELINES.items():
        print(f"  {name}: {sec:.0f}秒")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("submit", help="1本投入する(終わってから次を投げる)")
    p.add_argument("still", type=Path, help="先頭フレームにする静止画")
    p.add_argument("--model", default="wan2.2", choices=list(MODEL_RESOLUTIONS))
    p.add_argument("--resolution", default="480p")
    p.add_argument("--duration", type=float, default=5.0)
    p.add_argument("--aspect", default="9x16")
    p.add_argument("--audio", type=Path, help="wan2.2-s2v で口を駆動する音声")
    p.add_argument("--ref-sheet", type=Path, help="ltx-2.3-ic に渡す参照シート")
    p.add_argument("--prompt", help="既定の測定用プロンプトを差し替える")
    p.add_argument(
        "--no-lightning", action="store_true", help="Wan の 4steps 蒸留 LoRA を使わない"
    )
    p.add_argument("--force", action="store_true", help="前の run が未完でも投げる")
    p.set_defaults(func=cmd_submit)

    p = sub.add_parser("status", help="状態を見て、終わっていれば動画を回収する")
    p.add_argument("--model", default="wan2.2", choices=list(MODEL_RESOLUTIONS))
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("report", help="表を出す")
    p.add_argument("--model", default="wan2.2", choices=list(MODEL_RESOLUTIONS))
    p.add_argument("--gpu", default="L4", choices=list(RATES))
    p.set_defaults(func=cmd_report)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
