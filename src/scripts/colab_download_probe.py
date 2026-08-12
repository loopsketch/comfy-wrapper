"""ウェイト取得が止まる理由を、GPU を使わずに突き止める。

    src/scripts/colab.sh new -s dl          # CPU ランタイム(GPU 課金なし)
    src/scripts/colab_push.sh dl
    src/scripts/colab.sh exec -s dl -f src/scripts/colab_download_probe.py
    src/scripts/colab.sh exec -s dl -f src/scripts/colab_download_probe_status.py

19GB の1ファイルが 14〜17GB 付近で5回とも止まった。落としているのは HF の
ファイルで A100 は一切関係ないため、CPU ランタイムで同じことを再現する。

取りたいのは次の4つ。

1. **キャッシュがどのファイルシステムに載っているか。** /content と別なら、
   download_image_models.py の空き容量チェック(/content を見ている)は的外れになる
2. **空きが尽きていないか。** 19GB を置けるかは、そのファイルシステム次第
3. **どの転送経路か。** hf_xet / hf_transfer / 素の HTTP で挙動が違う
4. **止まった瞬間に、どこで止まっているか。** faulthandler でスタックを吐かせる

進捗と結果は /content/logs/dlprobe.log。
"""

import subprocess
from pathlib import Path

LOGS = Path("/content/logs")

# 実際に止まったファイル(qwen-image-edit の本体、19.09GB)
REPO = "Comfy-Org/Qwen-Image-Edit_ComfyUI"
FILE = "split_files/diffusion_models/qwen_image_edit_2511_int8_convrot.safetensors"

# 何秒動きが無ければ「止まった」と見なしてスタックを吐くか
STALL_SECONDS = 180

SCRIPT = f'''
import faulthandler, json, os, shutil, sys, threading, time
from pathlib import Path

log = open("{LOGS}/dlprobe.log", "w", buffering=1)
def p(*a):
    print(*a, file=log)
    print(*a)

if Path("/content/hf-token").exists():
    os.environ["HF_TOKEN"] = Path("/content/hf-token").read_text().strip()
    p("HF_TOKEN あり")
os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "30"

import huggingface_hub
from huggingface_hub import hf_hub_download
from huggingface_hub import constants as C

p("huggingface_hub", huggingface_hub.__version__)
cache = Path(C.HF_HUB_CACHE)
p("キャッシュ", cache)

# 1. どのファイルシステムか。同じ st_dev なら同居、違えば別
def dev(path):
    q = Path(path)
    while not q.exists():
        q = q.parent
    return os.stat(q).st_dev
p("st_dev  /content =", dev("/content"), " キャッシュ =", dev(cache))

# 2. 空き容量。19GB を置けるのはどちらか
for name in ("/content", str(cache), "/"):
    try:
        t, u, f = shutil.disk_usage(Path(name) if Path(name).exists() else "/")
        p(f"df {{name:24s}} 全体 {{t/1024**3:8.1f}}GB  使用 {{u/1024**3:8.1f}}GB  空き {{f/1024**3:8.1f}}GB")
    except Exception as e:
        p(f"df {{name}} 取得できず: {{e!r}}")

# 3. どの転送経路か
for mod in ("hf_xet", "hf_transfer"):
    try:
        __import__(mod)
        p(f"{{mod}}: 入っている")
    except ImportError:
        p(f"{{mod}}: 無い")
p("HF_HUB_ENABLE_HF_TRANSFER =", os.environ.get("HF_HUB_ENABLE_HF_TRANSFER"))
p("HF_HUB_DISABLE_XET        =", os.environ.get("HF_HUB_DISABLE_XET"))

# 4. 止まったらスタックを吐く見張り
state = {{"n": 0, "last": time.time(), "size": 0, "done": False}}

def sizes():
    inc = xet = 0
    try:
        inc = sum(x.stat().st_size for x in cache.rglob("*.incomplete") if x.is_file())
    except OSError:
        pass
    try:
        xd = cache.parent / "xet"
        if xd.exists():
            xet = sum(x.stat().st_size for x in xd.rglob("*") if x.is_file())
    except OSError:
        pass
    return inc, xet

def watch():
    while not state["done"]:
        inc, xet = sizes()
        t, u, f = shutil.disk_usage(cache if cache.exists() else "/")
        cur = inc + xet + u
        if cur > state["size"] + 5 * 1024 ** 2:
            state["size"], state["last"] = cur, time.time()
            p(f"[{{time.strftime('%H:%M:%S')}}] 書きかけ {{inc/1024**3:6.2f}}GB "
              f"xet {{xet/1024**3:6.2f}}GB  空き {{f/1024**3:7.1f}}GB")
        elif time.time() - state["last"] > {STALL_SECONDS}:
            p(f"\\n===== {STALL_SECONDS}秒 動きが無い。スタックを吐く =====")
            p(f"書きかけ {{inc/1024**3:.2f}}GB / xet {{xet/1024**3:.2f}}GB / 空き {{f/1024**3:.1f}}GB")
            faulthandler.dump_traceback(file=log)
            log.flush()
            state["last"] = time.time()   # 次の周期でまた吐く
        time.sleep(10)

threading.Thread(target=watch, daemon=True).start()

p("\\n取得を始める:", "{REPO}", "{FILE}")
t0 = time.time()
try:
    got = hf_hub_download(repo_id="{REPO}", filename="{FILE}")
    p(f"\\n成功 {{time.time()-t0:.0f}}秒  ->  {{got}}")
    p(f"サイズ {{Path(got).stat().st_size/1024**3:.2f}}GB")
except Exception as e:
    p(f"\\n失敗 {{time.time()-t0:.0f}}秒  {{type(e).__name__}}: {{e}}")
    import traceback; traceback.print_exc(file=log)
finally:
    state["done"] = True
    inc, xet = sizes()
    t, u, f = shutil.disk_usage(cache if cache.exists() else "/")
    p(f"最終 書きかけ {{inc/1024**3:.2f}}GB / xet {{xet/1024**3:.2f}}GB / 空き {{f/1024**3:.1f}}GB")
    p("DLPROBE_DONE")
'''


def main() -> None:
    LOGS.mkdir(exist_ok=True)
    sh = LOGS / "dlprobe.py"
    sh.write_text(SCRIPT)
    out = LOGS / "dlprobe.stdout"
    with open(out, "w") as f:
        subprocess.Popen(
            ["nohup", "python", str(sh)],
            stdout=f, stderr=subprocess.STDOUT, start_new_session=True,
        )
    print(f"取得の診断を開始した。進捗は {LOGS / 'dlprobe.log'}")


if __name__ == "__main__":
    main()
