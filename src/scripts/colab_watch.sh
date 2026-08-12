#!/usr/bin/env bash
# ランタイムの見張り。中身は colab_keepalive_watch.py。
#
#   src/scripts/colab_watch.sh [セッション名] [上限分]   見張りを始める
#   src/scripts/colab_watch.sh --status [セッション名]   状態を見る
#   src/scripts/colab_watch.sh --stop                    見張りを止める
#
# keep-alive の生存と構築の進捗を見て、**上限時間を超えたら自分でランタイムを止める**。
# GPU の稼働時間で課金されるのに、待ちに入ると誰も止めない。人が見ていない前提で
# 打ち切る。
#
#   COLAB_MAX_MINUTES   確保からの上限(既定 30分)。第2引数でも指定できる
#   COLAB_IDLE_MINUTES  何も進んでいない状態の許容(既定 8分)
#
# 長い構築や大量生成をまとめて回すときは上限を伸ばす:
#   src/scripts/colab_watch.sh comfy 90
set -euo pipefail

cd "$(dirname "$0")/../.."

WATCH=/app/src/scripts/colab_keepalive_watch.py

case "${1:-}" in
  --stop)
    docker compose exec -T colab pkill -f colab_keepalive_watch.py 2>/dev/null || true
    echo "見張りを止めた"
    ;;
  --status)
    SESSION="${2:-comfy}"
    # パターンは [] で割る。pgrep -f は自分を起こした sh のコマンドラインも見るため、
    # そのまま書くと**必ずヒットする**。死んでいる見張りを「動作中」と報告していた
    docker compose exec -T colab sh -c "
      pgrep -f '[c]olab_keepalive_watch.py' >/dev/null \
        && echo '見張り    : 動作中' || echo '見張り    : 止まっている'
      pgrep -f '[k]eep-alive .* ${SESSION}\$' >/dev/null \
        && echo 'keep-alive: 生存' || echo 'keep-alive: 落ちている'
    "
    tail -6 .colab/keepalive-watch.log 2>/dev/null || true
    ;;
  *)
    SESSION="${1:-comfy}"
    MAX="${2:-${COLAB_MAX_MINUTES:-30}}"
    docker compose exec -T colab pkill -f colab_keepalive_watch.py 2>/dev/null || true
    docker compose exec -d \
      -e "COLAB_MAX_MINUTES=$MAX" \
      -e "COLAB_IDLE_MINUTES=${COLAB_IDLE_MINUTES:-8}" \
      -e "COLAB_READY_IDLE_MINUTES=${COLAB_READY_IDLE_MINUTES:-15}" \
      -e "COLAB_BOOT_MINUTES=${COLAB_BOOT_MINUTES:-10}" \
      colab python "$WATCH" "$SESSION"
    echo "見張りを始めた (上限 ${MAX}分で自動停止 / ログ: .colab/keepalive-watch.log)"
    ;;
esac
