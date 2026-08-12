"""ルック比較の進捗を見る。

    src/scripts/colab.sh exec -s comfy -f src/scripts/colab_image_bench_status.py
"""

import shutil
from pathlib import Path

LOGS = Path("/content/logs")
OUT = Path("/content/bench")


def main() -> None:
    log = LOGS / "bench.log"
    if log.exists():
        print(log.read_text(errors="replace")[-1200:])
    else:
        print("まだ始まっていない")

    pngs = sorted(OUT.glob("*.png")) if OUT.exists() else []
    print(f"\n-- 出力 {len(pngs)} 枚")
    for p in pngs:
        print(f"   {p.stat().st_size/1024:6.0f}KB  {p.name}")

    _, _, free = shutil.disk_usage("/content")
    print(f"-- /content 空き {free/1024**3:.1f}GB")
    print("-- 状態:", "完了" if (OUT / "report.json").exists() else "実行中")


if __name__ == "__main__":
    main()
