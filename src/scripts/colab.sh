#!/usr/bin/env bash
# colab CLI を常駐コンテナの中で実行する。
#
#   src/scripts/colab.sh sessions
#   src/scripts/colab.sh new -s comfy --gpu A100
#
# 常駐コンテナ越しに叩くのは keep-alive デーモンを生かすため。`colab new` は
# keep-alive を detached な子プロセスとして起こすので、`docker compose run --rm`
# で叩くとコマンド終了でコンテナごと消え、ランタイムがアイドル刈り取りされる。
set -euo pipefail

cd "$(dirname "$0")/../.."

if ! docker compose ps --status running --services 2>/dev/null | grep -qx colab; then
  echo "colab コンテナが動いていません。先に: docker compose up -d colab" >&2
  exit 1
fi

exec docker compose exec -T colab colab "$@"
