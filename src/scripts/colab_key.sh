#!/usr/bin/env bash
# H3 API のアクセスキーを手元で発行し、ハッシュだけを Colab へ送る。
#
#   src/scripts/colab_key.sh [セッション名]
#
# 平文は .colab/colab-api-key (600) に置き、手元のスクリプトはそこから読む。Colab 側へ渡るのは
# SHA-256 ハッシュだけのキーストアなので、平文がリモートに存在しない。
# キーは初回だけ発行し、以降は同じものを送り直す。作り直したいときは
# .colab/comfy-keys.json と .colab/colab-api-key を消してから実行する。
set -euo pipefail

cd "$(dirname "$0")/../.."

SESSION="${1:-comfy}"
STORE=".colab/comfy-keys.json"
PLAIN=".colab/colab-api-key"

mkdir -p .colab

if [ ! -f "$STORE" ] || [ ! -f "$PLAIN" ]; then
  rm -f "$STORE" "$PLAIN"
  docker compose exec -T colab \
    python src/scripts/genkey.py --keys "/app/$STORE" issue --name comfy --env \
    | sed 's/^COLAB_API_KEY=//' > "$PLAIN"
  chmod 600 "$PLAIN"
  echo "キーを発行した: $PLAIN (平文はここだけ。Colab へは渡らない)"
fi

src/scripts/colab.sh upload -s "$SESSION" "$STORE" /content/comfy-keys.json
