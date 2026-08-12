"""生成済みのクリップを仕上げる(フレーム補間 + アップスケール)。

    # 1080p24 のクリップを 4K のまま出す(補間なし)
    docker compose run --rm client src/scripts/postprocess.py \
      submit works/.verify/measure_ltx-2.3-gguf_2.mp4 --size 4k

    # 16fps のクリップを 48fps に増やして 4K にする
    docker compose run --rm client src/scripts/postprocess.py \
      submit works/.verify/measure_wan2.2_2.mp4 --size 4k --multiplier 3

    docker compose run --rm client src/scripts/postprocess.py status

**尺は変えない。** 補間で増えたフレーム数に合わせて fps が上がる(8fps x3 = 24fps)。

**倍率は整数だけ。** 24p が要るなら、生成の時点で 24 を割り切れるフレーム数にしておく
(41F を 8fps とみなして x3、61F を 12fps とみなして x2)。48fps に増やしてから
半分捨てるのは、その補間がまるごと無駄になる。

**RAM が効く。** 4K は「解像度 x フレーム数」でシステム RAM を食う。
Colab の L4 ランタイムは 53GB なので、4K なら 120フレーム(24p で5秒)あたりが目安。
投入前に見積もりを出して、超えそうなら止める。
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

# scripts/ の隣にある lib/ を通す (実行は python src/scripts/xxx.py の形なので
# パッケージとしては解決されない)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import colab_link

# 宛先とキーの解決は colab_link に集約してある(旧名の環境変数も読む)
ENDPOINT = colab_link.read_endpoint()
STATE = Path("/app/works/.verify/postprocess.json")

SIZES = {
    "4k": (3840, 2160),
    "4k-portrait": (2160, 3840),
    "1080p": (1920, 1080),
    "1080p-portrait": (1080, 1920),
}

# 倍率ごとのアップスケールモデル。入力と目標の比で選ぶ(server/post_workflows.py と同じ)
UPSCALE_MODELS = {2: "RealESRGAN_x2.pth", 4: "RealESRGAN_x4plus.safetensors"}

# これを超える見積もりは投入前に止める(Colab の L4 は RAM 53GB)
RAM_LIMIT_GB = 30.0


def _req(method: str, path: str, payload: dict | None = None, timeout: int = 600):
    key = colab_link.require_api_key()
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(f"{ENDPOINT}{path}", data=data, method=method)
    r.add_header("Authorization", f"Bearer {key}")
    if data is not None:
        r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=timeout) as res:
        return res.read()


def _probe(path: Path) -> dict:
    """入力の fps・フレーム数・寸法を ffprobe で読む。"""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate,nb_frames",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    s = json.loads(out.stdout)["streams"][0]
    num, den = s["r_frame_rate"].split("/")
    return {
        "width": int(s["width"]),
        "height": int(s["height"]),
        "fps": float(num) / float(den),
        "frames": int(s.get("nb_frames") or 0),
    }


def _state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {"jobs": []}


def _save(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def cmd_submit(args) -> int:
    if not args.video.exists():
        print(f"動画がありません: {args.video}")
        return 2

    src = _probe(args.video)
    if args.source_fps:
        # **Wan には fps の条件付けが無い。** 41フレームを 8fps とみなせば
        # 5秒のスロー寄りのカットになり、生成の計算量は 81フレームの半分で済む。
        # ここはその「みなし」を入れる口(中身のフレームは触らない)
        print(f"入力の fps を {src['fps']:.3g} -> {args.source_fps:.3g} とみなす")
        src["fps"] = args.source_fps
    width, height = SIZES[args.size] if args.size else (None, None)
    frames = (src["frames"] - 1) * args.multiplier + 1 if args.multiplier > 1 else src["frames"]
    out_fps = src["fps"] * args.multiplier

    # **中間と最終が同時に載る。** 拡大モデルは「入力 x 倍率」を吐き、そのあと
    # 目標サイズへ縮めるので、ピークは2本の合計。1080p に x4 を当てると中間だけで
    # 7680x4352 になり、120フレームで約 45GB に達する
    scale, model, px = 1, args.upscale_model, 0
    if width:
        ratio = max(width / src["width"], height / src["height"])
        scale = 2 if ratio <= 2.2 else 4
        model = model or UPSCALE_MODELS[scale]
        px = width * height
    ram = ((src["width"] * scale) * (src["height"] * scale) + px) * 3 * 4 * frames / 1024**3

    print(f"入力  : {args.video.name} {src['width']}x{src['height']} "
          f"{src['frames']}F @{src['fps']:.3g}fps")
    print(f"出力  : {width or src['width']}x{height or src['height']} "
          f"{frames}F @{out_fps:.3g}fps (尺は据え置き)")
    if width:
        print(f"拡大  : x{scale} ({model}) -> 中間 "
              f"{src['width'] * scale}x{src['height'] * scale} から目標へ縮める")
    print(f"RAM   : 約 {ram:.1f}GB (上限 {RAM_LIMIT_GB:.0f}GB)")
    if ram > RAM_LIMIT_GB and not args.force:
        print("\nRAM の見積もりが上限を超えます。フレーム数か解像度を下げてください "
              "(--force で無視)")
        return 1

    # **縦横が食い違うと中央でクロップされる。** 歪みはしないが被写体が切り落とされる。
    # 縦のクリップに --size 1080p を当てて、人物の頭と足元が消えたのに気づかず
    # 仕上げを1本まるごと捨てた。RAM は止めるのにここは素通しだった
    if width:
        src_portrait = src["height"] > src["width"]
        if src_portrait != (height > width):
            kept = min(width / src["width"], height / src["height"]) / max(
                width / src["width"], height / src["height"]
            )
            suggest = next(
                (n for n, (w, h) in SIZES.items() if (h > w) == src_portrait
                 and max(w, h) == max(width, height)),
                None,
            )
            print(
                f"\n入力は{'たて' if src_portrait else 'よこ'}なのに目標は"
                f"{'たて' if height > width else 'よこ'}です。中央で切り取られ、"
                f"元の約 {kept * 100:.0f}% しか残りません"
                + (f"。--size {suggest} を使ってください" if suggest else "")
            )
            if not args.force:
                print("(意図してクロップするなら --force)")
                return 1

    payload = {
        "video": base64.b64encode(args.video.read_bytes()).decode(),
        "source_fps": src["fps"],
        "multiplier": args.multiplier,
        "target_width": width,
        "target_height": height,
    }
    if args.interp_model:
        payload["interp_model"] = args.interp_model
    if model:
        payload["upscale_model"] = model

    job = json.loads(_req("POST", "/v1/postprocess", payload))
    state = _state()
    state["jobs"].append({
        "job_id": job["job_id"],
        "source": str(args.video),
        "out": str(args.video.with_name(f"{args.video.stem}_post.mp4")),
        "size": f"{width or src['width']}x{height or src['height']}",
        "fps": out_fps,
        "multiplier": args.multiplier,
    })
    _save(state)
    print(f"\n投入した: {job['job_id']} / status で回収する")
    return 0


def cmd_status(args) -> int:
    state = _state()
    if not state["jobs"]:
        print("まだ投入していません")
        return 1
    for entry in state["jobs"]:
        # 回収済みのものは問い合わせない。**ジョブ台帳はサーバのメモリにしかない**ので、
        # セッションを立て直すと 404 になる。それで全体を落とすと、いま走っている
        # ジョブの結果まで回収できなくなる(measure_video.py と同じ扱いにする)
        if entry.get("done"):
            continue
        try:
            job = json.loads(_req("GET", f"/v1/jobs/{entry['job_id']}"))
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            print(f"{entry['job_id']}: 前のセッションのジョブ (台帳から消えている)")
            entry["done"] = True
            continue
        line = f"{entry['job_id']} ({Path(entry['source']).name} -> {entry['size']}): {job['status']}"
        if job.get("error"):
            line += f" / {job['error']}"
        print(line)
        if job["status"] == "succeeded" and not entry.get("done"):
            out = Path(entry["out"])
            out.write_bytes(_req("GET", f"/v1/jobs/{entry['job_id']}/video"))
            entry["done"] = True
            got = _probe(out)
            print(f"  回収した: {out} {got['width']}x{got['height']} "
                  f"{got['frames']}F @{got['fps']:.3g}fps "
                  f"({out.stat().st_size / 1024**2:.1f}MB)")
    _save(state)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("submit", help="1本投入する")
    p.add_argument("video", type=Path)
    p.add_argument("--size", choices=list(SIZES), help="目標の解像度。省略すると拡大しない")
    p.add_argument("--multiplier", type=int, default=1, help="フレーム補間の倍率(1で補間なし)")
    p.add_argument(
        "--source-fps",
        type=float,
        help="入力の fps をこの値とみなす(41F を 8fps 扱いにして x3 で 24p にする等)",
    )
    p.add_argument("--interp-model", help="film_net_fp16 / rife_v4.26 など")
    p.add_argument("--upscale-model")
    p.add_argument("--force", action="store_true", help="RAM の見積もりを無視する")
    p.set_defaults(func=cmd_submit)

    p = sub.add_parser("status", help="状態を見て、終わっていれば回収する")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
