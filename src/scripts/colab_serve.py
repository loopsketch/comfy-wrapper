"""Colab 側で ComfyUI と FastAPI を起動する(非同期)。

    src/scripts/colab.sh exec -s comfy -f src/scripts/colab_serve.py

どちらも 127.0.0.1 に閉じたまま起動する。外へは出さない。手元からは
`docker compose up -d tunnel`(ssh -L)で 8000 / 8188 に繋ぐ。

ComfyUI には認証機構が無く、ワークフローを投げられること自体が任意コード実行と
等価になるため、トンネルに直接出してはいけない。

ComfyUI の起動は数分かかる。`colab exec` は同期実行で、待つと WebSocket が
切れる("Connection was lost.")ので、ここでは起動だけして即座に戻る。
準備できたかは colab_serve_status.py で確認する。
"""

import os
import subprocess
from pathlib import Path

WORK = Path("/content/comfy")
COMFY = Path("/content/ComfyUI")
LOGS = Path("/content/logs")
KEYS = Path("/content/comfy-keys.json")
JOBS = Path("/content/jobs")


def _spawn(name: str, args: list[str], cwd: Path, env: dict | None = None) -> int:
    log = open(LOGS / f"{name}.log", "w")
    proc = subprocess.Popen(
        args, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, env=env,
        start_new_session=True,
    )
    print(f"{name}: pid={proc.pid}")
    return proc.pid


def _detect_models() -> dict:
    """ディスクに実在するウェイトを見て、サーバへ渡す名前を決める。

    server/h3_workflows.py の既定は int8 のファイル名だが、`--quant fp8` で構築すると
    fp8 のファイルしか無い。人が環境変数を合わせる作りだと取り違えるので、
    **実物から決める**。両方あれば既定(int8)のままにする。
    """
    out: dict[str, str] = {}
    d = COMFY / "models" / "diffusion_models"
    for task, var in (("fl2va", "H3_FL2VA_MODEL"), ("ref2va", "H3_REF2VA_MODEL")):
        if var in os.environ:  # 明示指定があればそれを尊重する
            continue
        found = sorted(p.name for p in d.glob(f"minimax_h3_{task}_*.safetensors"))
        if not found or any("int8" in n for n in found):
            continue
        out[var] = found[0]
        print(f"{task}: {found[0]} を使う")
    return out


def main() -> None:
    LOGS.mkdir(exist_ok=True)
    JOBS.mkdir(exist_ok=True)

    if not (LOGS / "setup.done").exists():
        raise SystemExit("環境構築が終わっていません(colab_setup_status.py で確認)")

    if not KEYS.exists():
        raise SystemExit(
            f"{KEYS} がありません。手元で src/scripts/colab_key.sh を実行して、"
            "ハッシュだけのキーストアを送ってください(平文は手元に残ります)"
        )

    _spawn(
        "comfyui",
        ["python", "main.py", "--listen", "127.0.0.1", "--port", "8188",
         "--disable-auto-launch"],
        cwd=COMFY,
    )

    env = dict(
        os.environ,
        COMFY_URL="http://127.0.0.1:8188",
        WRAPPER_KEYS_PATH=str(KEYS),
        WRAPPER_JOBS_DIR=str(JOBS),
        **_detect_models(),
    )
    _spawn("api", ["python", "-m", "uvicorn", "app:app", "--host", "127.0.0.1",
                   "--port", "8000"], cwd=WORK / "server", env=env)

    print("起動した。準備できたかは colab_serve_status.py で確認する")


if __name__ == "__main__":
    main()
