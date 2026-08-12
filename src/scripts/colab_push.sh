#!/usr/bin/env bash
# src/ を固めて Colab のセッションへ送る。
#
#   src/scripts/colab_push.sh [セッション名]
#
# 展開は colab_setup.py が行う。コードだけ差し替えたいときは、送ったあとに
# colab_setup.py の [1/4] 相当(tar の展開)だけを流せばよい。
set -euo pipefail

cd "$(dirname "$0")/../.."

SESSION="${1:-comfy}"
TAR=".colab/.upload.tar.gz"

mkdir -p .colab

tar czf "$TAR" --exclude='__pycache__' --exclude='*.pyc' src
trap 'rm -f "$TAR"' EXIT

src/scripts/colab.sh upload -s "$SESSION" "$TAR" /content/comfy-wrapper.tar.gz

# HF のトークンがあれば一緒に送る。未認証だとウェイトの取得が絞られる
# (実測で 100MB/s -> 8MB/s。42.5GB が7分で終わるところ1時間かかった)
if [ -f .colab/hf-token ]; then
  src/scripts/colab.sh upload -s "$SESSION" .colab/hf-token /content/hf-token
fi
