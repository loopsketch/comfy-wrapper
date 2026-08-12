#!/usr/bin/env bash
# Colab の 8000(API) / 8188(ComfyUI) を手元へ通す常駐トンネル。tunnel サービスの起点。
#
# **Colab は1ランタイムにつき `colab ssh` を1本しか許さない。** 接続に失敗しても
# サーバ側にはセッションが登録されたまま残ることがあり、その状態で張り直すと
#
#   [colab] Already-active SSH session (HTTP 429): another `colab ssh` is connected
#
# で弾かれる。docker の restart に任せて秒単位で叩き直すと **自分で自分を締め出して
# 復帰できなくなる**。2026-08-12 に実際に踏んだ: 取得直後で負荷の高いランタイムに
# 繋ぎにいって banner exchange でタイムアウトし、以降ずっと 429 で再起動を繰り返した。
#
# なので待ってから張り直す。間隔は失敗のたびに倍にして、上限で頭打ちにする。
#
#   COLAB_SESSION              セッション名(既定 comfy)
#   COLAB_TUNNEL_RETRY_SECONDS 最初の待ち(既定 30秒)
#   COLAB_TUNNEL_MAX_WAIT      待ちの上限(既定 180秒)
set -uo pipefail

SESSION="${COLAB_SESSION:-comfy}"
BASE_WAIT="${COLAB_TUNNEL_RETRY_SECONDS:-30}"
MAX_WAIT="${COLAB_TUNNEL_MAX_WAIT:-180}"
wait_s="$BASE_WAIT"

SSH_DIR=/app/.colab/.ssh
CONFIG="$SSH_DIR/config"
KEY="$SSH_DIR/id_ed25519"

# ssh の Host ブロックはセッション名ごとに要る。無ければ書く。
# **手で書かせない。** ここは README にも載らないまま手元にだけ存在していて、
# 別の環境に持っていくと `Could not resolve hostname colab-xxx` で止まる。
# 宛先の実体は colab ssh --proxy-mode で、鍵は colab_key ではなく SSH 鍵の方。
mkdir -p "$SSH_DIR"
touch "$CONFIG"
chmod 700 "$SSH_DIR"
chmod 600 "$CONFIG"
if ! grep -q "^Host colab-${SESSION}\$" "$CONFIG"; then
  cat >> "$CONFIG" <<EOS
Host colab-${SESSION}
  ProxyCommand /usr/local/bin/colab ssh --proxy-mode -s ${SESSION}
  User root
  StrictHostKeyChecking no
  UserKnownHostsFile /dev/null
  IdentityFile ${KEY}
EOS
  echo "ssh の設定に Host colab-${SESSION} を足した ($CONFIG)" >&2
fi

if [ ! -f "$KEY" ]; then
  echo "SSH 鍵がありません: $KEY" >&2
  echo "  docker compose exec colab ssh-keygen -t ed25519 -N \"\" -C comfy-wrapper -f $KEY" >&2
  exit 1
fi

while true; do
  # -L は 0.0.0.0 にバインドしないとコンテナの外から見えない。
  # ConnectTimeout を長めに取るのは、取得直後のランタイムは I/O が詰まっていて
  # banner exchange に時間がかかるため
  ssh -F /app/.colab/.ssh/config -N \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -o ConnectTimeout=45 \
    -L "0.0.0.0:8000:127.0.0.1:8000" \
    -L "0.0.0.0:8188:127.0.0.1:8188" \
    "colab-${SESSION}"
  code=$?

  if [ "$code" = 0 ]; then
    # -N なので正常終了は普通ここに来ない。来たら素直に張り直す
    wait_s="$BASE_WAIT"
  fi
  echo "ssh が終了した (code=${code})。${wait_s}秒 待って張り直す" >&2
  sleep "$wait_s"
  wait_s=$((wait_s * 2))
  [ "$wait_s" -gt "$MAX_WAIT" ] && wait_s="$MAX_WAIT"
done
