"""ルック比較を非同期で走らせる。

    src/scripts/colab.sh exec -s comfy -f src/scripts/colab_image_bench_run.py

`colab exec` は同期実行なので、生成をそのまま流すと WebSocket が切れる。
モデルの切り替えでウェイトのロードが挟まるぶん、10枚で数十分かかりうる。

進捗は /content/logs/bench.log。完了で /content/bench/report.json ができる。
確認は colab_image_bench_status.py を流す。
"""

import subprocess
from pathlib import Path

LOGS = Path("/content/logs")
BENCH = Path("/content/comfy/scripts/colab_image_bench.py")


def main() -> None:
    LOGS.mkdir(exist_ok=True)
    if not BENCH.exists():
        raise SystemExit(f"{BENCH} がありません(colab_push.sh でコードを送る)")

    log = open(LOGS / "bench.log", "w")
    proc = subprocess.Popen(
        ["nohup", "python", str(BENCH)],
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
    )
    print(f"比較を開始した: pid={proc.pid} / 進捗は {LOGS}/bench.log")


if __name__ == "__main__":
    main()
