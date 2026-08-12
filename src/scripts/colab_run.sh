#!/usr/bin/env bash
# 承認済みの作業を、確保から停止まで無人で1本流す。
#
#   src/scripts/colab_run.sh [オプション] -- <python スクリプトと引数...>
#
# -- のあとは client コンテナの python にそのまま渡る。作業の中身をここに書く。
#
# 例:
#   src/scripts/colab_run.sh --setup video --models ltx-2.3-gguf --gpu L4 --max 60 -- \
#     src/scripts/measure_video.py submit works/still.png --model ltx-2.3-gguf
#
# **承認はこのコマンドを打つ時点で済んでいる。実行中に承認点を作らない。**
# 途中に人待ちがあると、待っている間ずっと GPU が課金される。実際に、構築後の
# 起動待ちで人を待つ作りだったために 43分(45円)を空転させた(2026-08-08)。
#
# 何が起きても最後に必ず stop する(trap)。GPU は枚数ではなく稼働時間で課金される。
#
#   -s SESSION   セッション名(既定 comfy)
#   --gpu GPU    既定 L4。A100 の 3.44分の1 の単価で 1.44倍しか遅くない
#   --max MIN    見張りの上限分(既定 60)。これを超えたら見張りが強制停止する
#   --idle MIN   何も進んでいないと見なすまでの分(既定 8)
#   --setup KIND image(静止画) | h3(MiniMax H3) | video(Wan2.2 / LTX-2.3)。既定 image
#   --models "…" 取得するモデル。既定は setup 側の既定
#                video なら wan2.2 | wan2.2-t2v | wan2.2-5b | ltx-2.3 | ltx-2.3-gguf
#                h3 なら fl2va(t2v/i2v) | ref2va(参照つき) | "fl2va ref2va"
#   --quant Q    H3 の量子化(h3 のときだけ)。int8 | fp8 | bf16
#   --keep       終わってもセッションを止めない(続けて手で叩きたいとき)
set -euo pipefail

cd "$(dirname "$0")/../.."

SESSION=comfy
GPU=L4
MAX=60
IDLE=8
KIND=image
MODELS=""
QUANT=""
KEEP=0

while [ $# -gt 0 ]; do
  case "$1" in
    -s) SESSION="$2"; shift 2 ;;
    --gpu) GPU="$2"; shift 2 ;;
    --max) MAX="$2"; shift 2 ;;
    --idle) IDLE="$2"; shift 2 ;;
    --setup) KIND="$2"; shift 2 ;;
    --models) MODELS="$2"; shift 2 ;;
    --quant) QUANT="$2"; shift 2 ;;
    --keep) KEEP=1; shift ;;
    --) shift; break ;;
    *) echo "不明なオプション: $1" >&2; exit 2 ;;
  esac
done

if [ $# -eq 0 ]; then
  echo "実行する python スクリプトを -- のあとに書いてください" >&2
  exit 2
fi
CW_ARGS=("$@")

case "$KIND" in
  image) SETUP=src/scripts/colab_image_setup.py ;;
  h3)    SETUP=src/scripts/colab_setup.py ;;
  video) SETUP=src/scripts/colab_video_setup.py ;;
  *)     echo "--setup は image / h3 / video です: $KIND" >&2; exit 2 ;;
esac

LOG=.colab/keepalive-watch.log
# 見張りが書く機械可読の状態(building / ready / stopped)。
# **ログの日本語を grep しない。** 起動行の「アイドル 8分で自動停止」を停止と
# 読み違えて、構築開始15秒で自分を止めた(2026-08-09)
STATE=.colab/watch-state.json

say() { printf '\n=== %s\n' "$1"; }

# 止める前にリモートのログを吐かせる。**回収は見張りの自動停止時にしか走らない**ので、
# colab_run.sh が自力で終わるとログごと消える。API が上がらなかった回の原因を
# 追えなかった(2026-08-09)。落ちた理由は、落ちたセッションの中にしか無い
dump_remote_logs() {
  src/scripts/colab.sh exec -s "$SESSION" 2>/dev/null <<'PY' || true
from pathlib import Path
for name in ("api", "comfyui", "setup"):
    p = Path("/content/logs") / f"{name}.log"
    if not p.exists():
        continue
    tail = p.read_text(errors="replace").splitlines()[-25:]
    print(f"\n--- {name}.log (末尾{len(tail)}行) ---")
    print("\n".join(tail))
PY
}

# 見張りの状態を1語で返す。まだ書かれていなければ空
read_state() {
  [ -f "$STATE" ] || return 0
  python3 -c "
import json, sys
try:
    print(json.load(open('$STATE'))['state'])
except Exception:
    pass
" 2>/dev/null
}

# 確保したら、何があっても止める。--keep のときだけ残す
cleanup() {
  local code=$?
  if [ "$KEEP" = 1 ]; then
    say "セッションは残しました (--keep)。止めるには: src/scripts/colab.sh stop -s $SESSION"
    return
  fi
  if [ "$code" != 0 ]; then
    say "リモートのログ (止めると消えるので先に吐く)"
    dump_remote_logs
  fi
  say "セッションを止めます"
  src/scripts/colab_watch.sh --stop >/dev/null 2>&1 || true
  src/scripts/colab.sh stop -s "$SESSION" 2>&1 | tail -3 || true
  # **台帳から消えることと、リモートが止まることは別。** colab exec が
  # 失敗すると colab-cli はセッションを prune するので stop が
  # 「not found」になる。実体が生きていれば課金が続くため、現物を必ず見る
  say "サーバ側に残っていないか確認します"
  src/scripts/colab.sh sessions 2>&1 | tail -5 || true
  # トンネルも畳む。セッションが無いと ProxyCommand (colab ssh) が失敗して
  # ssh が即死し、restart: unless-stopped と噛み合って再起動を繰り返す
  docker compose stop tunnel >/dev/null 2>&1 || true
  exit $code
}

mkdir -p .colab works
docker compose up -d colab >/dev/null

# **確保する前に認証を見る。** 切れていると確保も問い合わせも停止もできない。
# GPU を掴む前に落ちれば課金はゼロで済む。ここは人待ちが起きてよい唯一の場所で、
# まだ何も確保していないから待たせても損が出ない
say "認証を確認します"
if ! docker compose exec -T colab python /app/src/scripts/colab_auth.py; then
  cat >&2 <<'EOS'

認証が切れています。次を実行してから、もう一度このコマンドを打ってください。

  docker compose exec colab colab sessions

URL をブラウザで開き、表示されたコードを貼り付けます。**再認証のあとは
ランタイムが残っていないかを必ず確認してください。** 切れている間は問い合わせが
できないので、止めたつもりのものが動いたままになっていることがあります。

  src/scripts/colab.sh sessions
EOS
  exit 3
fi

say "$GPU を確保します"
src/scripts/colab.sh new -s "$SESSION" --gpu "$GPU"
# 確保した GPU を残す。コストは枚数でも本数でもなく **どの GPU を何秒握ったか** で
# 決まるので、見積もり側 (src/lib/colab_link.py) がここを読む
printf '%s' "$GPU" > .colab/gpu
trap cleanup EXIT INT TERM

# 前回の状態を先に消す。残っていると今回のものと読み違える
rm -f "$STATE"
# 見張りは確保の直後に始める。ここから先はどこで転んでも上限で止まる
COLAB_IDLE_MINUTES="$IDLE" src/scripts/colab_watch.sh "$SESSION" "$MAX"

say "コードとキーを送ります"
src/scripts/colab_push.sh "$SESSION"
src/scripts/colab_key.sh "$SESSION"

if [ -n "$MODELS" ]; then
  printf '%s\n' "$MODELS" > .colab/.setup-models
  src/scripts/colab.sh upload -s "$SESSION" .colab/.setup-models /content/setup-models.txt
  rm -f .colab/.setup-models
fi

if [ -n "$QUANT" ]; then
  printf '%s\n' "$QUANT" > .colab/.setup-quant
  src/scripts/colab.sh upload -s "$SESSION" .colab/.setup-quant /content/setup-quant.txt
  rm -f .colab/.setup-quant
fi

say "構築を始めます (取得〜サーバ起動まで無人で進みます)"
src/scripts/colab.sh exec -s "$SESSION" -f "$SETUP"

say "準備完了を待ちます (見張りの上限 ${MAX}分を超えたら打ち切られます)"
# **この待ちにも期限を付ける。** 見張りが落ちると誰も上限を数えなくなり、
# 待ち続けたぶんがそのまま課金になる。見張りより少しだけ長く待って諦める
DEADLINE=$(( $(date +%s) + (MAX + 5) * 60 ))
while true; do
  case "$(read_state)" in
    ready)
      break ;;
    stopped)
      say "準備できる前に見張りが打ち切りました。ログ: $LOG"
      tail -5 "$LOG"
      # 見張りが回収した構築ログ。停止の理由はここにしか残らない
      if [ -f "works/.rescue/$SESSION/logs/setup.log" ]; then
        say "回収した構築ログの末尾 (works/.rescue/$SESSION/logs/setup.log)"
        tail -25 "works/.rescue/$SESSION/logs/setup.log"
      fi
      exit 1 ;;
  esac
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    say "$((MAX + 5))分待っても準備できませんでした (見張りが落ちた可能性)。ログ: $LOG"
    tail -5 "$LOG"
    exit 1
  fi
  sleep 20
done

say "トンネルを張ります"
docker compose up -d tunnel >/dev/null
# API(uvicorn) は ComfyUI より少し遅れて上がる。疎通するまで待つ。
# 動画モデルは ComfyUI がウェイトを見つけ終えるまで ready が立たない。取得直後の初回は
# 10分では足りずに空振りした(2026-08-10)。待ちを伸ばし、経過も出す
WAIT_MODEL=""
NEED=comfy_ready
TRIES=60
[ "$KIND" = h3 ] && { WAIT_MODEL=minimax-h3; NEED="video_ready[minimax-h3]"; TRIES=180; }
if [ "$KIND" = video ]; then
  # --models の先頭を、そのセッションで使うモデルとみなす
  VIDEO_MODEL="${MODELS%% *}"
  VIDEO_MODEL="${VIDEO_MODEL:-wan2.2}"
  # wan2.2-t2v は wan2.2 のウェイト違いなので、health 上は同じ名前で見る
  [ "$VIDEO_MODEL" = "wan2.2-t2v" ] && VIDEO_MODEL=wan2.2
  WAIT_MODEL="$VIDEO_MODEL"
  NEED="video_ready[$VIDEO_MODEL]"
  TRIES=180
fi

# 疎通の判定は colab_link の health() をそのまま使う。**ここで別実装しない。**
# 以前はインラインの python で同じことを書いていて、colab_link の health() は
# 誰も呼んでいなかった。落ちている理由も colab_link.diagnose() が名指しする
health_probe() {
  CW_WAIT_MODEL="$WAIT_MODEL" docker compose run --rm -e CW_WAIT_MODEL client -c "
import os, sys
sys.path.insert(0, '/app/src')
from lib import colab_link
want = os.environ.get('CW_WAIT_MODEL') or ''
try:
    h = colab_link.health(colab_link.read_endpoint())
except Exception as e:
    print(e); sys.exit(1)
if not h.get('comfy_ready'):
    print('ComfyUI がまだ応答しません'); sys.exit(1)
if want and not h.get('video_ready', {}).get(want):
    print(f'{want} のウェイトがまだ載っていません: {h.get(\"video_ready\", {})}'); sys.exit(1)
print('準備できた')
" 2>&1
}

api_up=0
for i in $(seq 1 $TRIES); do
  if probe_out="$(health_probe)"; then
    api_up=1
    break
  fi
  # 黙って待たない。落ちているのか、まだ上がりきらないのかを切り分けられるようにする
  if [ $((i % 6)) = 0 ]; then
    printf '  %s を待っています (%d/%d): %s\n' "$NEED" "$i" "$TRIES" \
      "$(printf '%s' "$probe_out" | tail -1)"
  fi
  sleep 10
done
# **黙って先へ進まない。** 60回失敗しても素通りする作りだったため、API が
# 上がっていないまま生成を投げて ConnectionReset で落ちた(2026-08-09)。
# 見張りは ComfyUI(8188)しか見ないので、API(8000)の不在はここでしか気づけない
if [ "$api_up" != 1 ]; then
  say "API ($NEED) が10分たっても応答しません。トンネルの転送先が落ちています"
  docker compose logs --tail 5 tunnel 2>&1 | sed 's/^/  /'
  exit 1
fi

say "実行します: ${CW_ARGS[*]}"
docker compose run --rm client "${CW_ARGS[@]}"

say "完了しました"
