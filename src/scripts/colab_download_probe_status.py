"""colab_download_probe.py の進捗を見る。

    src/scripts/colab.sh exec -s dl -f src/scripts/colab_download_probe_status.py
"""

from pathlib import Path

LOG = Path("/content/logs/dlprobe.log")


def main() -> None:
    if not LOG.exists():
        print("まだ始まっていない")
        return
    text = LOG.read_text(errors="replace")
    print(text[-3000:])
    print("\n-- 状態:", "完了" if "DLPROBE_DONE" in text else "実行中")


if __name__ == "__main__":
    main()
