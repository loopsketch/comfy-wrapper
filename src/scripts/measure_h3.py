"""H3 の生成時間を測る。GPU を変えたときの比較用。

    src/scripts/colab_run.sh --gpu L4 --setup h3 --quant fp8 --max 60 --python -- \
      src/scripts/measure_h3.py works/monochrome-buddy-3/produce/stills/e2c02.png

手元(client コンテナ)から tunnel 越しに1本だけ生成し、経過秒を出す。
比較の基準は README のコスト表にある A100 実測。

    A100-SXM4-40GB / int8 / 480p(0.4MP) / 5.167秒 / 20steps -> 335秒(初回ロード込み)

**同じ条件で測らないと比較にならない**ので、megapixels / steps / duration は
既定でその値に固定してある。

損益分岐: L4 は 1.54CU/時、A100 は 5.3CU/時 (100CU = 1179円) なので **3.44倍**。
L4 がこれより遅ければ A100 の方が安く、速ければ L4 の方が安い。
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

# scripts/ の隣にある lib/ を通す (実行は python src/scripts/xxx.py の形なので
# パッケージとしては解決されない)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import colab_link

# 宛先とキーの解決は colab_link に集約してある(旧名の環境変数も読む)
ENDPOINT = colab_link.read_endpoint()

# **H3 の記法で書く。** 生成時間はステップ数・解像度・尺で決まるのでプロンプトの
# 中身は秒数に響かないが、平文で書くと出てきた絵が演出として当てにならず、
# 「測定は有効だが絵は代表的でない」と毎回断ることになる。
# 最初に測ったときは "The subject stays still, gentle natural motion" と書いてしまい、
# **指示どおり動かない映像**が出てスローモーションに見えた(2026-08-09)。
#
# 記法は公式の VIDEO_PROMPT_WRITING_GUIDE (README の「出典」)。要点は3つ。
#   - [Shot 1] で全体のスタイルと初期構図を宣言する(参照画像があるタスクでは
#     スタイルは参照画像から導き、別のスタイル語を足さない)
#   - カメラは専用語彙(Static Shot / Push In / Tracking Shot ...)で、
#     必要なときだけ振幅と速度を添える
#   - 音は overall_soundscape と non_diegetic_music に分ける。台詞・歌唱・劇中音は
#     本文側の担当なので、ここで繰り返さない
PROMPT = """\
[Shot 1] Live-action, cinematic, a medium-wide shot frames a young woman in a light \
denim jacket and striped top pushing a red shopping cart straight toward the camera \
down a bright supermarket aisle. She leans over the handle, grinning, and drives the \
cart forward at a brisk walk while snack packages tumble from the basket and scatter \
across the tiled floor behind her. The camera holds a Tracking Shot, retreating ahead \
of her to keep the cart centered as she advances.

overall_soundscape: Cart wheels rattle over the tiled floor while plastic snack bags \
rustle and slap down behind her. Quick sneaker steps and a short bright laugh cut \
through the low hum of the refrigerator units.

non_diegetic_music: N/A"""

# A100 実測と同じ条件。変えると比較にならない
MEGAPIXELS = 0.4
STEPS = 20
DURATION = 5.167
SEED = 12345

# 損益分岐(A100 円/時 ÷ L4 円/時)
A100_BASELINE_SECONDS = 335.0
CU_YEN = 1179.0 / 100
RATES = {"A100": 5.3 * CU_YEN, "L4": 1.54 * CU_YEN}


def _req(method: str, path: str, payload: dict | None = None, timeout: int = 120):
    key = colab_link.require_api_key()
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(f"{ENDPOINT}{path}", data=data, method=method)
    r.add_header("Authorization", f"Bearer {key}")
    if data is not None:
        r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=timeout) as res:
        return res.read()


def main() -> int:
    if len(sys.argv) < 2:
        print("使い方: measure_h3.py <first_frame の画像パス>")
        return 2
    first = Path(sys.argv[1])
    if not first.exists():
        print(f"画像がありません: {first}")
        return 2

    info = json.loads(_req("GET", "/v1/info"))
    gpu = info.get("gpu") or "(不明)"
    print(f"GPU        : {gpu}")
    print(f"VRAM       : 全体 {info.get('vram_total_mb')}MB / 空き {info.get('vram_free_mb')}MB")
    print(f"モデル     : {json.dumps(info.get('models'), ensure_ascii=False)}")
    print(f"条件       : {MEGAPIXELS}MP / {STEPS}steps / {DURATION}秒 / seed={SEED}")
    print(f"プロンプト : H3 記法 ({len(PROMPT)}文字)")
    print(f"基準(A100) : {A100_BASELINE_SECONDS:.0f}秒\n")

    payload = {
        "task": "i2v",
        "prompt": PROMPT,
        "first_frame": base64.b64encode(first.read_bytes()).decode(),
        "duration": DURATION,
        "megapixels": MEGAPIXELS,
        "steps": STEPS,
        "seed": SEED,
    }

    t0 = time.time()
    job = json.loads(_req("POST", "/v1/generate", payload, timeout=300))
    job_id = job["job_id"]
    print(f"投入した job_id: {job_id}")

    last = ""
    while time.time() - t0 < 5400:
        state = json.loads(_req("GET", f"/v1/jobs/{job_id}"))
        status = state["status"]
        if status != last:
            print(f"  [{time.time() - t0:6.0f}秒] {status}")
            last = status
        if status == "succeeded":
            elapsed = time.time() - t0
            video = _req("GET", f"/v1/jobs/{job_id}/video", timeout=600)
            out = Path(os.environ.get("CW_MEASURE_OUT",
                                      "/app/works/.verify/h3_measure.mp4"))
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(video)

            print(f"\n=== 結果 ===")
            print(f"生成時間   : {elapsed:.0f}秒 ({elapsed / 60:.1f}分)")
            print(f"A100比     : {elapsed / A100_BASELINE_SECONDS:.2f}倍")
            print(f"出力       : {out} ({len(video) / 1024:.0f}KB)")
            for name, yen_h in RATES.items():
                sec = A100_BASELINE_SECONDS if name == "A100" else elapsed
                print(f"  {name:5s} {yen_h:5.2f}円/時 x {sec:6.0f}秒 = {yen_h * sec / 3600:5.2f}円")
            break
        if status in ("failed", "canceled"):
            print(f"\n失敗: {state.get('error') or status}")
            return 1
        time.sleep(10)
    else:
        print("\n90分待っても終わりませんでした")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
