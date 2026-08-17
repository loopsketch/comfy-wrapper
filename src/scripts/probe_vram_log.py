#!/usr/bin/env python3
"""ComfyUI のログから、VRAM のやりくりに関わる行だけを拾う。

    src/scripts/colab.sh exec -s comfy -f src/scripts/probe_vram_log.py

**「動いた」と「余裕があった」は別。** 部分オフロードが起きていれば動きはするが、
そのぶん遅くなる。L4 に載せて回すかどうかの判断は、時間だけでなくここを見て決める。

このスクリプトは colab exec で単体送信されるので、他のスクリプトを import しない。
"""

import re
import subprocess
from pathlib import Path

LOG = Path("/content/logs/comfyui.log")

# オフロード・確保・不足に関わる行。ComfyUI の model_management が出す文言
PATTERNS = re.compile(
    r"loaded partially|loaded completely|unloading|lowvram|OOM|out of memory|"
    r"Requested to load|free memory|CUDA error|allocat",
    re.IGNORECASE,
)


def main() -> int:
    if not LOG.exists():
        print(f"{LOG} がありません")
        return 1

    lines = LOG.read_text(errors="replace").splitlines()
    hits = [line for line in lines if PATTERNS.search(line)]
    print(f"-- comfyui.log {len(lines)}行 のうち VRAM 関連 {len(hits)}行")
    for line in hits[-40:]:
        print(line)

    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.total,memory.used,memory.free",
         "--format=csv,noheader"],
        capture_output=True, text=True,
    )
    print(f"\n-- nvidia-smi: {out.stdout.strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
