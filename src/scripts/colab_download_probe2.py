"""取得が止まる原因が「ComfyUI の依存インストール」かを、GPU を使わずに切り分ける。

    src/scripts/colab.sh exec -s dl -f src/scripts/colab_download_probe2.py
    src/scripts/colab.sh exec -s dl -f src/scripts/colab_download_probe_status.py

colab_download_probe.py は素の環境で 124秒 完走した。一方 A100 の実運用は5回とも
14〜17GB で止まっている。両者の差は、setup スクリプトが取得の前に

    pip install -r ComfyUI/requirements.txt
    pip install -r server/requirements.txt

を走らせている点。ここで huggingface_hub / hf_xet の版が変わると、取得の経路ごと
変わってしまう。**同じ CPU セッションで依存を入れてから同じ取得をやり直す**ことで、
GPU を使わずに切り分ける。

前後で版を必ず記録する。何が変わったのかを推測で語らないため。
"""

import importlib
import subprocess
import sys
from pathlib import Path

# colab exec は本文だけを送ってくるので、tar で展開済みの scripts を明示的に通す。
# ディレクトリはカーネルの起動後に作られるので、import キャッシュを捨てないと
# 「そんなモジュールは無い」と言われ続ける
sys.path.insert(0, "/content/comfy/scripts")
importlib.invalidate_caches()

import colab_download_probe as base  # noqa: E402  同じ取得スクリプトを使い回す

LOGS = Path("/content/logs")
COMFY = Path("/content/ComfyUI")
WORK = Path("/content/comfy")

PRE = f'''
import json, subprocess, sys
from pathlib import Path

log = open("{LOGS}/dlprobe.log", "w", buffering=1)
def p(*a):
    print(*a, file=log)
    print(*a)

def versions(tag):
    out = {{}}
    for mod in ("huggingface_hub", "hf_xet", "hf_transfer"):
        try:
            m = __import__(mod)
            out[mod] = getattr(m, "__version__", "?")
        except ImportError:
            out[mod] = None
    p(f"[{{tag}}] {{json.dumps(out)}}")
    return out

before = versions("依存を入れる前")

if not Path("{COMFY}").exists():
    p("ComfyUI を取得")
    subprocess.run(["git", "clone", "--depth", "1",
                    "https://github.com/comfyanonymous/ComfyUI", "{COMFY}"],
                   check=True, capture_output=True)

for req in ("{COMFY}/requirements.txt", "{WORK}/server/requirements.txt"):
    if not Path(req).exists():
        p(f"{{req}} が無いので飛ばす")
        continue
    p(f"pip install -r {{req}}")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", req],
                       capture_output=True, text=True)
    p(f"  終了コード {{r.returncode}}")
    if r.returncode != 0:
        p("  " + (r.stderr or "")[-800:])

after = versions("依存を入れた後")
for k in before:
    if before[k] != after[k]:
        p(f"**版が変わった** {{k}}: {{before[k]}} -> {{after[k]}}")

# 前回落とした本体を消して、同じ取得をやり直す
import shutil
from huggingface_hub import constants as C
d = Path(C.HF_HUB_CACHE) / "models--Comfy-Org--Qwen-Image-Edit_ComfyUI"
if d.exists():
    p(f"キャッシュを消す {{d}}")
    shutil.rmtree(d, ignore_errors=True)

log.close()
'''

# base.SCRIPT は自分でログを開き直す(追記ではなく上書き)ので、
# 前段の記録が消えないように追記モードへ差し替える
BODY = base.SCRIPT.replace(
    f'log = open("{LOGS}/dlprobe.log", "w", buffering=1)',
    f'log = open("{LOGS}/dlprobe.log", "a", buffering=1)',
)


def main() -> None:
    LOGS.mkdir(exist_ok=True)
    sh = LOGS / "dlprobe2.py"
    sh.write_text(PRE + "\n" + BODY)
    out = LOGS / "dlprobe2.stdout"
    with open(out, "w") as f:
        subprocess.Popen(
            ["nohup", "python", str(sh)],
            stdout=f, stderr=subprocess.STDOUT, start_new_session=True,
        )
    print(f"依存インストール込みの診断を開始した。進捗は {LOGS / 'dlprobe.log'}")


if __name__ == "__main__":
    main()
