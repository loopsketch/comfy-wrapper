"""ComfyUI だけを起動する(非同期)。

    src/scripts/colab.sh exec -s comfy -f src/scripts/colab_comfy.py

モデル選定のように ComfyUI へ直接ワークフローを投げたいときに使う。FastAPI は
起動しないのでアクセスキーも要らない。127.0.0.1 に閉じたままなので、手元から見るなら
`docker compose up -d tunnel` で 8188 を通す。

準備できたかは colab_serve_status.py で確認する(comfyui だけ 200 になっていればよい)。
"""

import subprocess
from pathlib import Path

COMFY = Path("/content/ComfyUI")
LOGS = Path("/content/logs")


def main() -> None:
    LOGS.mkdir(exist_ok=True)
    if not (LOGS / "setup.done").exists():
        raise SystemExit("環境構築が終わっていません(colab_setup_status.py で確認)")

    log = open(LOGS / "comfyui.log", "w")
    proc = subprocess.Popen(
        ["python", "main.py", "--listen", "127.0.0.1", "--port", "8188",
         "--disable-auto-launch"],
        cwd=COMFY, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
    )
    print(f"comfyui: pid={proc.pid}")
    print("準備できたかは colab_serve_status.py で確認する")


if __name__ == "__main__":
    main()
