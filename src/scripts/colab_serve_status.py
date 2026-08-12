"""ComfyUI と FastAPI が応答するかを見る。

    src/scripts/colab.sh exec -s comfy -f src/scripts/colab_serve_status.py
"""

import urllib.request
from pathlib import Path

LOGS = Path("/content/logs")

# ComfyUI は /system_stats で生死を見る。特定ノードの有無に依存させると、
# 画像モデルだけを載せたセッション(H3 なし)で誤って「未起動」と出てしまう。
CHECKS = {
    "comfyui": "http://127.0.0.1:8188/system_stats",
    "api": "http://127.0.0.1:8000/health",
}


def main() -> None:
    ready = True
    for name, url in CHECKS.items():
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                print(f"{name}: HTTP {r.status}")
        except Exception as e:
            ready = False
            print(f"{name}: まだ応答しない ({type(e).__name__}: {e})")
            log = LOGS / f"{name}.log"
            if log.exists():
                print("  --- ログ末尾 ---")
                for line in log.read_text(errors="replace").splitlines()[-8:]:
                    print("  " + line)

    print("\n-- 状態:", "準備完了" if ready else "起動中")


if __name__ == "__main__":
    main()
