"""確保した Colab ランタイムの素性を 1 回で調べる。

    src/scripts/colab.sh exec -s comfy -f src/scripts/probe_runtime.py

GPU の型番と VRAM、ディスクの空き(モデルを何本置けるか)、Python とドライバの版、
外へ出られるかを見る。ランタイムを確保した直後に流して、記録を残す用途。
"""

import shutil
import socket
import subprocess
import sys
from pathlib import Path


def _run(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return (r.stdout or r.stderr).strip()
    except Exception as e:  # コマンド自体が無い環境もある
        return f"(取得できず: {e})"


def main() -> None:
    print("== GPU ==")
    print(
        _run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version,compute_cap",
                "--format=csv,noheader",
            ]
        )
    )

    print("\n== ディスク ==")
    for path in ("/content", "/"):
        p = Path(path)
        if not p.exists():
            continue
        total, used, free = shutil.disk_usage(p)
        gb = 1024**3
        print(f"{path}: 全体 {total/gb:.1f}GB / 使用 {used/gb:.1f}GB / 空き {free/gb:.1f}GB")

    print("\n== メモリ ==")
    print(_run(["free", "-h"]))

    print("\n== Python ==")
    print(sys.version.replace("\n", " "))

    print("\n== ネットワーク ==")
    print(f"hostname: {socket.gethostname()}")
    print(_run(["curl", "-s", "-o", "/dev/null", "-w", "huggingface.co %{http_code} %{time_total}s", "https://huggingface.co"]))

    print("\n== 既存のモデル ==")
    comfy_models = Path("/content/ComfyUI/models")
    if comfy_models.exists():
        for f in sorted(comfy_models.rglob("*.safetensors")):
            print(f"{f.stat().st_size/1024**3:6.1f}GB  {f.relative_to(comfy_models)}")
    else:
        print("(ComfyUI 未導入)")


if __name__ == "__main__":
    main()
