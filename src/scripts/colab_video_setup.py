"""Wan2.2 / LTX 用の環境構築を非同期で走らせる。

    src/scripts/colab.sh exec -s comfy -f src/scripts/colab_video_setup.py

H3 は落とさない。**1セッションに1モデル**で、Wan2.2 14B 一式で約 38GB、
LTX-2.3 で約 42GB、LTX-2.5 で約 40GB ある。どれを落とすかは colab_run.sh --models で渡す
(/content/setup-models.txt 経由)。

進捗は /content/logs/setup.log。完了で /content/logs/setup.done ができる。
確認は colab_setup_status.py を流す(H3 版と共通)。

**取得のあと、そのままサーバまで起動する。** 人が colab_serve.py を打つまでの間は
取得も生成も起動も走っておらず、監視がアイドルと見て自動停止させてしまう。

このスクリプトは `colab exec -f` で単体送信されるので、他のスクリプトを import
してはいけない(送られるのはこのファイルだけ)。前置きが colab_setup.py と
重複しているのはそのため。
"""

import subprocess
from pathlib import Path

WORK = Path("/content/comfy")
COMFY = Path("/content/ComfyUI")
LOGS = Path("/content/logs")
TARBALL = Path("/content/comfy-wrapper.tar.gz")
KEYS = Path("/content/comfy-keys.json")  # colab_key.sh が送るハッシュだけのキーストア

# wan2.2 (i2v 14B) / wan2.2-t2v / wan2.2-5b / ltx-2.3 / ltx-2.3-gguf / ltx-2.5。
# 空白区切りで複数可
MODELS = "wan2.2"

_override = Path("/content/setup-models.txt")
if _override.exists():
    MODELS = _override.read_text().strip() or MODELS

# GGUF のウェイトは ComfyUI 本体では読めない。custom_nodes に ComfyUI-GGUF を足す。
# **要るときだけ入れる。** 常に入れると、GGUF を使わない回まで起動時の読み込みと
# 依存が増える
GGUF_SETUP = """
echo "[3.5/5] ComfyUI-GGUF を導入 (GGUF のウェイトを読むため)"
if [ ! -d {comfy}/custom_nodes/ComfyUI-GGUF ]; then
  git clone --depth 1 https://github.com/city96/ComfyUI-GGUF \
    {comfy}/custom_nodes/ComfyUI-GGUF
fi
pip install -q --upgrade gguf
""" if "gguf" in MODELS else ""

SCRIPT = f"""
set -e
# HF のトークンがあれば使う。未認証だと取得速度が大きく落ちる
if [ -f /content/hf-token ]; then
  export HF_TOKEN="$(cat /content/hf-token)"
  echo "HF_TOKEN を使う"
fi

# 取得は resilient_download.py が担う。Xet は速いが無応答で固まることがあり、
# HF_HUB_DOWNLOAD_TIMEOUT が効かない(Python の HTTP 層を通らないため)。
# Xet のまま取り、外から無進捗を見て殺す。最後の1回だけ Xet を切って挑む。

# **Xet を高性能モードで回す。** 帯域を使い切り、CPU コアを並列に使う設定に切り替わる。
# 旧 HF_HUB_ENABLE_HF_TRANSFER=1 の後継で、hf_transfer 自体は huggingface_hub 1.x で
# 使われなくなった(env を渡しても FutureWarning が出るだけで、経路は変わらない)。
#
# **hf_transfer に落とす道はもう無い。** Xet 対応リポジトリでは file_download.py が
# `xet_file_data is not None and is_xet_available()` で Xet を先に選ぶ。Xet を切れば
# 素の HTTP になるが、実測 4〜29MB/s では 19GB 級が成立しない。
# 切りたいときは CW_XET_HIGH_PERFORMANCE=0。
if [ "${{CW_XET_HIGH_PERFORMANCE:-1}}" != "0" ]; then
  export HF_XET_HIGH_PERFORMANCE=1
  echo "Xet を高性能モードで使う"
fi
if [ "${{CW_HF_TRANSFER:-0}}" = "1" ]; then
  echo "CW_HF_TRANSFER は効かない (huggingface_hub 1.x で hf_transfer は使われない)。Xet の高性能モードを使う"
fi
export HF_HUB_DOWNLOAD_TIMEOUT=30
# バッファリングを切る。切らないと setup.log に何も落ちず、どこで止まったのかが分からない
export PYTHONUNBUFFERED=1

# 起動に要るものは取得の前に確かめる。最後に気づくと構築の時間がまるごと課金の無駄になる
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
{GGUF_SETUP.format(comfy=COMFY)}
echo "[4/5] 動画モデルを取得 ({MODELS})"
python {WORK}/setup/download_video_models.py --comfy {COMFY} --models {MODELS}

# Xet のチャンクキャッシュを捨てる。ウェイトの実体とは別に、取得はチャンクを
# もう一組ぶん抱えるので、そのぶん丸々二重にディスクへ効く。実体は消さない
rm -rf /root/.cache/huggingface/xet {COMFY}/models/.cache
df -h /content | tail -1
touch {LOGS}/setup.done

# 続けてサーバを起こす。ここで人待ちにすると監視がアイドルと見て止めてしまう
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
    print(f"動画モデル ({MODELS}) の構築を開始した。進捗は {log}")


if __name__ == "__main__":
    main()
