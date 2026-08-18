---
name: colab-comfy
description: Colab の GPU 上の ComfyUI で画像や動画を生成する。「画像を作って」「この静止画を動かして」「参照画像から生成して」など、このリポジトリのラッパ経由で生成するときに使う。ランタイムの確保・構築・生成・停止の手順と、課金を止めるための鉄則が入っている。
---

# Colab で画像・動画を生成する

Colab のランタイムを確保して ComfyUI を立て、手元から HTTP API で生成する。
仕組みの説明は [README.ja.md](../../../README.ja.md)。ここは手順と判断だけ。

## 鉄則

- **GPU は稼働時間で課金される。** 確保したら必ず止める。止め忘れは寝ている間も課金が続く。
- **確保の前にユーザーへ確認する。** `cw up` / `cw run` は課金の始まりなので、
  GPU の種類と上限時間を伝えてから実行する。生成そのものの繰り返しは確認不要。
- **人待ちを作らない。** 確保してから「次どうしますか」と聞いている間も課金される。
  何をどこまでやるかは確保の前に決めておく。
- **見張りを必ず動かす。** `cw up` / `cw run` は自動で始める。手で `colab.sh new` した
  ときは `cw watch <セッション> <上限分>` で自分で始めること。
- **投入を安易にリトライしない。** 202 が返っていれば生成は走っている。投げ直すと
  GPU 時間をそのまま捨てる。
- **構築したモデルと、生成で指定するモデルを必ず一致させる。** `--models` で入れたのは
  1モデルだけで、`cw image` の既定は `z-image`。ずれると ComfyUI が
  `value_not_in_list` (400) で弾く(2026-08-12 に踏んだ)。**`--model` は毎回明示する。**

## 0. いまの状態を見る

生成を頼まれたら、まずここから。すでにセッションがあれば確保は要らない。

```bash
cw status     # compose / セッション / 見張り / 疎通 を1画面で
```

`cw` が無ければ同じものを個別に見る。

```bash
docker compose ps                      # colab / tunnel が動いているか
src/scripts/colab.sh sessions          # サーバーに問い合わせる(ローカルの記録ではない)
src/scripts/colab_watch.sh --status    # 見張りと keep-alive の生存
```

`colab` コンテナが動いていなければ `docker compose up -d colab`。
認証が切れていたら **URL を出してユーザーに開いてもらう**。対話端末は要らないので、
返ってきたコードをこちらで渡せる。

```bash
cw auth                              # 状態を見る (期限が近ければ延長する)
cw auth login                        # 切れていたら: 認可 URL を出してユーザーに渡す
cw auth login --code <返ってきたコード>  # 通す。そのままセッションの残りも確認する
```

## 1. セッションが無いとき: 確保して構築する

続けて何枚も生成するなら `cw up` で確保して構築する(**セッションは残るので、
止める責任がこちらに残る**。手順は 3)。

```bash
# 画像(Qwen-Image-Edit / Z-Image / Qwen-Image)
# --models と、あとで使う --model は同じものにする。ずれると 400 で弾かれる
cw up --setup image --models qwen-image-edit --gpu L4 --max 60

# 動画(Wan2.2 / LTX)
cw up --setup video --models ltx-2.5 --gpu L4 --max 90
```

1本だけ流して確実に止めたいときは `cw run`。確保 → 構築 → 実行 → 停止 を1本で流し、
**何が起きても最後に止める**。`--` のあとは client コンテナのパスで書く。

```bash
cw run --setup video --models ltx-2.5 --gpu L4 --max 90 -- \
  src/scripts/measure_video.py submit works/still.png --model ltx-2.5 --aspect 9x16
```

- `--max` は見張りの上限分。ここを超えると見張りが強制的に止める。構築に 15〜25分
  かかるので、生成の時間を足して決める。
- 構築は **15〜25分かかる**。Bash ツールの上限を超えるので `run_in_background: true` で
  流し、進捗は下で見る。

```bash
tail -20 .colab/keepalive-watch.log
src/scripts/colab.sh exec -s comfy -f src/scripts/colab_setup_status.py
```

**`.colab/keepalive-watch.log` の日本語を grep して判断しない。** 起動行の
「アイドル 8分で自動停止」を停止と読み違える。機械可読な状態は
`.colab/watch-state.json` の `state`(`building` / `ready` / `stopped`)。

## 2. セッションがあるとき: 生成だけ投げる

`--keep` で残したあと、あるいは既にセッションが立っているとき。

`cw` が入っていれば (`uv tool install --editable .`)、パスは CWD 相対でよい。
入っていない環境では `docker compose run --rm client src/scripts/<同じスクリプト>` で
同じことができる(この節のコマンドはどちらでも通る)。

```bash
# 画像。既定は完成まで待って CWD に書き出す
# --model はそのセッションで構築したモデル。省略すると既定の z-image になる
cw image "プロンプト" --model qwen-image --aspect 9x16 --out works/foo.png

# 参照画像つき(渡すと model によらず Qwen-Image-Edit の編集経路になる。最大3枚)
cw image "image 1 の人物が公園のベンチに座っている" --ref works/ref.png --aspect 1x1

# まとめて投げて、あとで回収する
cw image "..." --no-wait
cw jobs
```

```bash
# 動画(画像を渡すと i2v、渡さなければ t2v)
cw video works/still.png --model ltx-2.5 --out works/clip.mp4
cw video --prompt "..." --aspect 9x16 --out works/clip.mp4

# 仕上げ(フレーム補間 + アップスケール)
cw post works/clip.mp4 --size 4k --multiplier 2
cw jobs

# どのモデルで何ができるか・1秒あたりいくらか
cw models
```

**ジョブ台帳はサーバのメモリにしかない。** ランタイムを止めると回収できなくなるので、
`--no-wait` で投げたものは止める前に必ず `status` で回収する。

## 3. 止める

`--keep` を付けた、または手で確保したときは、**作業が終わったら必ず実行する。**

```bash
cw stop     # 見張り -> セッション -> 現物の確認 -> トンネル
```

`cw` が無ければ同じ順で手で叩く。

```bash
src/scripts/colab_watch.sh --stop
src/scripts/colab.sh stop -s comfy
src/scripts/colab.sh sessions        # 現物が消えたことを確認する
docker compose stop tunnel
```

`stop` が「not found」でも安心しない。台帳から消えることと実体が止まることは別なので、
`sessions` で必ず現物を見る。`[?]` で始まる名前なしのセッションが出たら台帳から外れた
孤児で `stop -s` では引けない。**放っておくと課金が続く**ので `unassign` で解放する。

```bash
docker compose exec -T colab bash -lc 'python - <<PY
from colab_cli.common import state
sessions, assignments = state.sync_sessions()
for a in assignments:
    state.client.unassign(a.endpoint)
PY'
```

## モデルの選び方

**1セッションに1モデル。** ディスクの都合で H3(動画 42.5GB)と Qwen 系(画像 28GB)は
同居できない。画像と動画の両方を頼まれたら、**どちらを先にやるかを決めて別セッションにする。**

| 用途 | `--setup` | `--models` | 備考 |
|---|---|---|---|
| 画像・参照つき編集 | `image` | `qwen-image-edit` | 参照画像を渡せる。キャラの一貫性はこちら |
| 画像・速い / 素直 | `image` | `z-image` | 11.3GB。8step で速い |
| 画像・追従が強い | `image` | `qwen-image` | 28GB |
| 動画・既定候補 | `video` | `ltx-2.3-gguf` | L4 で一番速い(5秒が約72秒)。音声つき 25fps |
| 動画・いちばん新しい | `video` | `ltx-2.5` | L4 で 720p まで素で回る(480p 5秒が約105秒)。音声つき 24fps。**先頭+末尾を置ける** |
| 動画・素直な i2v | `video` | `wan2.2` | 4step 蒸留。音声なし |
| 動画・音声で駆動 | `video` | `wan2.2-s2v` | リップシンク。1チャンク 4.81秒まで |
| 動画・参照つき (r2v) | `h3` | `fl2va` / `ref2va` | 参照つき生成は H3 だけ。42.5GB |

GPU は既定の `L4` でよい。A100 が要るのは `ltx-2.3` の fp8 だけ(`ltx-2.5` は int8 で L4 に載る)。

**LTX 系の HF リポジトリは gated。** 未同意のトークンだと取得が 403 になるのに構築は
止まらず、ウェイトの無いセッションが立ち上がる(2026-08-17 に踏んだ)。落ちたかどうかは
`GET /health` の `video_ready` で見る。同意はモデルページの "Agree and Access" で、
**2.3 と 2.5 は別リポジトリなので別々に要る。**

## やってはいけないこと

- **ComfyUI(8188)を外に出さない。** 認証が無く、ワークフローの投入は任意コード実行と等価。
- **`docker compose run --rm colab` を使わない。** `colab new` が起こす keep-alive
  デーモンが道連れになってランタイムが刈り取られる。`colab` は常駐、操作は
  `src/scripts/colab.sh`(中身は `docker compose exec`)経由。
- **`docker compose restart tunnel` を連打しない。** 1ランタイムにつき `colab ssh` は
  1本だけで、叩き直すと `Already-active SSH session (HTTP 429)` で締め出される。
- **セッションが無いのに `tunnel` を上げたままにしない。** ProxyCommand の
  `colab ssh -s comfy` がその名前でランタイムを確保する(実測では CPU ランタイム)。
  止めるときは `colab.sh stop` と一緒に `docker compose stop tunnel` まで行う。
- **`.colab/` の中身をコミット・表示しない。** OAuth トークン・SSH 鍵・API キー・
  HF トークンが入っている。

## うまくいかないとき

| 症状 | 見るところ |
|---|---|
| `value_not_in_list` (400) | 構築したモデルと `--model` がずれている。セッションはそのまま、`--model` を直して投げ直す |
| 生成が届かない | `colab_link` のエラー文が原因を名指しする(認証切れ / セッション消失 / トンネル切れ) |
| `comfy_ready` が false | 構築がまだ。`colab_setup_status.py` |
| ウェイトが載らない | `curl` ではなく `GET /health` の `video_ready` を見る |
| 進んでいるのか分からない | `.colab/watch-state.json` と `.colab/keepalive-watch.log` |
| 落ちた理由が知りたい | 止める前に `/content/logs/{api,comfyui,setup}.log`。止めると消える |

見張りが自動停止したときの回収先は `works/.rescue/<セッション>/`。
`--gpu L4` を渡しても CPU ランタイムが返ることがあるので、**確保直後に
`src/scripts/colab.sh sessions` で `Hardware: L4 | Variant: GPU` を確認する。**
