"""colab_setup.py の進捗を見る。

    src/scripts/colab.sh exec -s comfy -f src/scripts/colab_setup_status.py
"""

import shutil
from pathlib import Path

LOGS = Path("/content/logs")


def main() -> None:
    log = LOGS / "setup.log"
    if not log.exists():
        print("まだ始まっていない")
        return

    text = log.read_text()
    print(text[-1500:])

    total, used, free = shutil.disk_usage("/content")
    gb = 1024**3
    print(f"\n-- /content 空き {free/gb:.1f}GB / 全体 {total/gb:.1f}GB")
    print("-- 状態:", "完了" if (LOGS / "setup.done").exists() else "実行中")


if __name__ == "__main__":
    main()
