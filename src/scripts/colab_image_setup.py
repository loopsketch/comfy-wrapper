"""静止画モデル用の環境構築を非同期で走らせる。

    src/scripts/colab.sh exec -s comfy -f src/scripts/colab_image_setup.py

H3(動画)は落とさない。`/content` の空きは確保直後で約 65.9GB あり、H3 一式(42.5GB)を
入れると残り 25.0GB になって Qwen 系(28GB)が入らないため、**モデル選定は H3 抜きの
セッションで行う**。

進捗は /content/logs/setup.log。完了で /content/logs/setup.done ができる。
確認は colab_setup_status.py を流す(H3 版と共通)。

**取得のあと、そのままサーバまで起動する。** 人が colab_serve.py を打つまでの間は
取得も生成も起動も走っておらず、見張りがアイドルと見て自動停止させてしまう
。人が見ていない前提なので、待ちを作らない。
"""

import subprocess
from pathlib import Path

WORK = Path("/content/comfy")
COMFY = Path("/content/ComfyUI")
LOGS = Path("/content/logs")
TARBALL = Path("/content/comfy-wrapper.tar.gz")
KEYS = Path("/content/comfy-keys.json")  # colab_key.sh が送るハッシュだけのキーストア

# 既定は Qwen-Image-Edit だけ。Z-Image を足すなら "z-image"(+11.3GB)。
# colab_run.sh --models で上書きできる(/content/setup-models.txt 経由)
MODELS = "qwen-image-edit"
LORAS = ""

_override = Path("/content/setup-models.txt")
if _override.exists():
    MODELS = _override.read_text().strip() or MODELS

SCRIPT = f"""
set -e
# HF のトークンがあれば使う。未認証だと取得速度が大きく落ちる
if [ -f /content/hf-token ]; then
  export HF_TOKEN="$(cat /content/hf-token)"
  echo "HF_TOKEN を使う"
fi

# 19GB 級の1ファイルが途中で無応答になる事象への対処。
#
# **取得は resilient_download.py が担う。** Xet は速いが無応答で固まることがあり、
# `HF_HUB_DOWNLOAD_TIMEOUT` が効かない(Python の HTTP 層を通らないため)。
# 一方 `HF_HUB_DISABLE_XET=1` にすると 469MB/s が 4〜29MB/s まで落ち、42.5GB では
# 成立しない。**Xet のまま取り、外から無進捗を見て殺す**。
# 最後の1回だけ Xet を切って挑む。

# **hf_transfer は既定で使わない。** Rust 実装で速い(実測 566MB/s)が、Python の
# HTTP 層を通らないため HF_HUB_DOWNLOAD_TIMEOUT が効かない。止まっても例外が
# 上がらず、download_*.py のリトライに入れないまま無応答になる。速さより、
# 止まったら気づいて再開できることを取る。
# 使いたいときは CW_HF_TRANSFER=1。
if [ "${{CW_HF_TRANSFER:-0}}" = "1" ]; then
  pip install -q hf_transfer 2>/dev/null && export HF_HUB_ENABLE_HF_TRANSFER=1 \
    && echo "hf_transfer を使う (タイムアウトは効かない)"
fi
export HF_HUB_DOWNLOAD_TIMEOUT=30
# **バッファリングを切る。** リダイレクト先がファイルだと Python の標準出力は
# ブロックバッファになり、setup.log に何も落ちない。実際、取得が止まった回の
# ログは「[4/5] ウェイトを取得」で終わっていて、どのファイルで止まったのかも
# 分からなかった。見えないログは無いのと同じ
export PYTHONUNBUFFERED=1

# 起動に要るものは取得の前に確かめる。最後に気づくと構築の20〜25分がまるごと課金の無駄になる
if [ ! -f {KEYS} ]; then
  echo "{KEYS} がありません。手元で src/scripts/colab_key.sh を実行してください"
  exit 1
fi

echo "[1/5] コードを展開"
rm -rf {WORK}
mkdir -p {WORK}
tar xzf {TARBALL} -C {WORK} --strip-components=1

echo "[2/5] ComfyUI を取得"
# **clone も粘る。** ウェイト取得は resilient_download.py が再開できるのに、ここだけ
# 一発勝負だった。GitHub に繋がらず 132秒でタイムアウトし、確保したランタイムを
# まるごと捨てた。Colab の回線は当たり外れがある
if [ ! -d {COMFY} ]; then
  for attempt in 1 2 3; do
    git clone --depth 1 https://github.com/comfyanonymous/ComfyUI {COMFY} && break
    echo "clone に失敗した (${{attempt}}回目)。20秒 待って試し直す"
    rm -rf {COMFY}
    sleep 20
  done
fi
if [ ! -d {COMFY} ]; then
  echo "ComfyUI を取得できなかった。回線を引き直す (セッションを取り直す)"
  exit 1
fi

echo "[3/5] 依存をインストール"
pip install -q -r {COMFY}/requirements.txt
pip install -q -r {WORK}/server/requirements.txt

echo "[4/5] 画像モデルを取得 (qwen-image-edit 一式で約30GB)"
python {WORK}/setup/download_image_models.py --comfy {COMFY} \
  --models {MODELS} --loras {LORAS}

# **Xet のチャンクキャッシュを捨てる。** 取得はウェイトの実体とは別に、Xet の
# チャンクをもう一組ぶん抱える。ComfyUI/models 側はキャッシュへのシンボリック
# リンクなので実体は一組だが、チャンクが残るぶんだけ丸々二重に効く。
# qwen-image と qwen-image-edit を両方入れた回で /content 112.6GB を使い切り、
# 参照画像のアップロードが `No space left on device` で 500 になった。
# 実体は消さない(消すとシンボリックリンクが切れる)。
rm -rf /root/.cache/huggingface/xet
df -h /content | tail -1
touch {LOGS}/setup.done

# 続けてサーバを起こす。ここで人待ちにすると見張りがアイドルと見て止めてしまう
echo "[5/5] ComfyUI と API を起動"
python {WORK}/scripts/colab_serve.py
echo "完了"
"""


def main() -> None:
    LOGS.mkdir(exist_ok=True)
    done = LOGS / "setup.done"
    if done.exists():
        done.unlink()

    sh = LOGS / "setup.sh"
    sh.write_text(SCRIPT)

    log = LOGS / "setup.log"
    with open(log, "w") as f:
        subprocess.Popen(
            ["nohup", "bash", str(sh)],
            stdout=f,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    print(f"画像モデルの構築を開始した。進捗は {log}")


if __name__ == "__main__":
    main()
