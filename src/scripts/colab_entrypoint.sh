#!/usr/bin/env bash
# colab 常駐コンテナの起点。認証の延長ループを回しながらコンテナを生かし続ける。
#
#   docker compose up -d colab          常駐(引数なし)。延長ループが回る
#   docker compose run --rm colab ARGS  ARGS をそのまま実行する(ループは回さない)
#
# **延長ループは常駐でしか回さない。** 監視 (colab_keepalive_watch.py) も認証を
# 見るが、監視はセッションがある間しか生きていない。認証が切れるのは半日で、
# その半日はたいてい何も走っていない。そこを埋めるのがこのループ。
#
#   COLAB_AUTH_LOOP_MINUTES  確認の間隔(既定 30分)。0 でループを止める
set -euo pipefail

AUTH=/app/src/scripts/colab_auth.py
LOG=/app/.colab/auth-loop.log
INTERVAL_MIN="${COLAB_AUTH_LOOP_MINUTES:-30}"

# 一発実行として呼ばれたときは、そのまま渡されたコマンドになる
if [ $# -gt 0 ]; then
  exec "$@"
fi

if [ "$INTERVAL_MIN" != "0" ]; then
  mkdir -p "$(dirname "$LOG")"
  (
    while true; do
      # 失敗してもループを止めない。**止まると切れたことにも気づけなくなる**
      python "$AUTH" 2>&1 | while IFS= read -r line; do
        printf '%s  %s\n' "$(date '+%m/%d %H:%M:%S %Z')" "$line"
      done >> "$LOG" || true
      sleep "$((INTERVAL_MIN * 60))"
    done
  ) &
fi

exec sleep infinity
