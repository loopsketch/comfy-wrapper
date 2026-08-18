---
name: colab-comfy
description: Colab の GPU 上の ComfyUI で画像や動画を生成するための環境の活用方法。「画像を作って」「この静止画を動かして」「参照画像から生成して」など、cw コマンド経由で生成するときに使う。ランタイムの確保・構築・生成・停止の手順と、課金を止めるための鉄則が入っている。
---

# Colab で画像・動画を生成する

comfy-wrapper は、Colab 上に ComfyUI 環境を構築し、ローカル環境から生成を依頼するための
ツールである。操作はすべて `cw` CLI コマンドで行い、生成物は `--out` で指定した場所に
出力できる。どのプロジェクトの中からでもそのまま叩ける。


## 前提

- `cw` CLI がインストールされていること
  - `cw --help` が通らなければ入れる

    ```bash
    git clone https://github.com/loopsketch/comfy-wrapper   # まだ無ければ
    uv tool install --editable /path/to/comfy-wrapper       # pipx install -e . でも入る
    ```

- Docker と Compose プラグイン
  - ランタイムの確保と停止で内部的に使う
- Colab のコンピューティングユニット
  - 無料枠では GPU ランタイムを確保できないので、課金枠の Colab Pro / Pro+ が要る


## 鉄則

- GPU は稼働時間で課金される
  - 確保したら必ず止める。止め忘れは寝ている間も課金が続く
  - 手で確保したときは `cw watch` で監視も始める
- 確保の前にユーザーへ確認する
  - `cw up` と `cw run` は課金の始まりなので、GPU の種類と上限時間を伝えてから実行する
  - 確保してから「次どうしますか」と聞いている間も課金される。どこまでやるかは先に決める
- 投入を安易にリトライしない
  - 202 が返っていれば生成は走っている。投げ直すと GPU 時間をそのまま捨てる
- 構築したモデルと `--model` を一致させる
  - `--models` で入れたのは1モデルだけで、`cw image` の既定は `z-image`
  - ずれると ComfyUI が `value_not_in_list` (400) で弾く (2026-08-12 に踏んだ)


## 主な利用フロー
comfy-wrapperを使った画像・動画コンテンツの生成は、次の5ステップで行います。
0はインストール直後の初回のみで、通常は、1以降のステップを順に実行する。

0. 初期化(インストール直後の初回のみ)
1. セッションの状態を確認
2. 認証、監視
3. ランタイムの確保と環境構築
4. 画像・動画コンテンツの生成


## コマンドリファレンス

### 初期化 (`cw init`)

SSH 鍵と Hugging Face のトークンを clone 先の `.colab/` に置く。引数なしで叩くと、
初期化が不足しているものと、それを埋めるコマンドが出力される。

```bash
cw init                    # 何が足りないかを確認
cw init ssh                # SSH 鍵を作る (ed25519)
cw init hf < token.txt     # HF トークンを保存する (--token でも渡せる)
cw init skills             # スキル一式を別のプロジェクトへ入れる (--global もある)
```

- 鍵は作り直さない
  - `cw init ssh` は既にあれば何もしない。作り直すとトンネルを張り直すことになる
- HF トークンは無くても動く
  - ただしウェイトの取得が大きく絞られ、その待ち時間はそのまま GPU の課金になる

### セッションの状態を確認 (`cw status` / `cw sessions`)

生成を依頼されたら、まずここから。すでにセッションがあれば確保は要らない。

```bash
cw status     # 宛先、セッション、監視、疎通を1画面で
cw sessions   # サーバーに問い合わせる (ローカルの記録ではない)
```

### 認証する (`cw auth`)

Colabの認証が切れていたら 認証用の URL を取得し、利用者に渡してブラウザで認証してもらう必要がある。取得したコードを伝えてもらい、コマンドで設定する。初回もこの手順で通す。

```bash
cw auth                              # 状態を見る (期限が近ければ延長する)
cw auth login                        # 切れていたら認可 URL を出してユーザーに渡す
cw auth login --code <返ってきたコード>  # 通す。そのままセッションの残りも確認する
```

### ランタイムを確保して環境構築する (`cw up`)

続けて何枚も生成するときに使う。セッションは残るので、止める責任がこちらに残る。

```bash
# 画像。--models と、あとで使う --model は同じものにする
cw up --setup image --models qwen-image-edit --gpu L4 --max 60

# 動画
cw up --setup video --models ltx-2.5 --gpu L4 --max 90

# MiniMax H3。参照つき生成 (r2v) を使うなら ref2va のウェイト (+21GB) も要る
cw up --setup h3 --models "fl2va ref2va" --gpu L4 --max 90
```

- `--max` は監視の上限分
  - 超えると監視がランタイムを強制的に止める
  - 構築に 15〜25分かかるので、生成の時間を足して決める
- 構築の 15〜25分は Bash ツールの上限を超える
  - `run_in_background: true` で流し、進捗は `cw status` で見る
- `--gpu L4` を渡しても CPU ランタイムが返ることがある
  - 確保直後に `cw sessions` で `Hardware: L4 | Variant: GPU` を確認する

### 1本流して必ず止める (`cw run`)

ランタイム確保、環境構築、処理実行、停止を連続して行い、完了後はランタイムを停止させる。
`--` のあとだけは clone先のスクリプトを書く。

```bash
cw run --setup video --models ltx-2.5 --gpu L4 --max 90 -- \
  src/scripts/measure_video.py submit works/still.png --model ltx-2.5 --aspect 9x16
```

### 監視する (`cw watch`)

`cw up` と `cw run` を行った際は自動で監視が行われるが、確認したい時や、手動で確保したときだけ使う。

```bash
cw watch <セッション> <上限分>   # 開始
cw watch --status               # 生存を見る
cw watch --stop                 # 止める
```

### 画像を生成する (`cw image`)

既定は完成まで待って書き出す。`--out` を省くと、実行したカレントディレクトリに、日時つきの名前で書き出す。

```bash
# --model はそのセッションで構築したモデル。省略すると既定の z-image になる
cw image "プロンプト" --model qwen-image --aspect 9x16 --out ./foo.png

# 参照画像つき。渡すと model によらず Qwen-Image-Edit の編集経路になる (最大3枚)
cw image "image 1 の人物が公園のベンチに座っている" --ref ./ref.png --aspect 1x1

# 投入だけして、あとで回収する
cw image "..." --no-wait
```

### 動画を生成する (`cw video`)

画像を渡すと i2v、渡さなければ t2v で動画を生成する。

```bash
cw video ./still.png --model ltx-2.5 --out ./clip.mp4
cw video --prompt "..." --aspect 9x16 --duration 5 --out ./clip.mp4

# 先頭と末尾を置く (ltx-2.5 / wan2.2 / minimax-h3)
cw video ./first.png --last-frame ./last.png --model ltx-2.5

# ネガティブを差し替える。空にするなら --negative ""
cw video ./still.png --model ltx-2.5 --negative "..."
```

### 後処理 (`cw post`)

後処理を実行する。今は、フレーム補間とアップスケールが利用できる。

```bash
cw post ./clip.mp4 --size 4k --multiplier 2
```

### 投げたものを回収する (`cw jobs`)

```bash
cw jobs
```

ジョブの記録はサーバーのメモリにしかない。ランタイムを止めると回収できなくなるので、
`--no-wait` で投げたものは止める前に必ず回収する。

### 使用できるモデルを調べる (`cw models`)

使用できるモデルを調べたり、コンテンツ生成の見積もりをするにあたり、情報を取得するために使用する。

```bash
cw models              # タスク、末尾フレーム、音声、参照、fps、ウェイト、秒/映像秒、円
cw models --gpu A100   # 単価を別の GPU で見る
```

### トンネルを張り直す (`cw tunnel`)

セッションは生きているのに手元へ届かないときに使う。ランタイムは確保し直さない。

```bash
cw tunnel restart     # up / stop / logs もある
```

### ログを読む (`cw logs`)

Colab 側の `/content/logs` を手元から読む。**ランタイムを止めると `/content` ごと消える**
ので、原因を追うなら止める前に読む。

```bash
cw logs                    # 置いてあるログを並べる
cw logs setup --tail 100   # 末尾を読む (setup / api / comfyui)
cw logs --save             # 全文を works/.rescue/<セッション>/logs/ へ落とす
```

監視も30秒ごとに同じ `colab exec` を使う。重なると返りが遅くなるので、**ループで回さない**。

### 止める (`cw stop`)

`cw up` でランタイムを確保したときや手動で確保したときは、作業が終わったら必ず実行する。

```bash
cw stop              # 監視、ログの回収、セッション、現物の確認、トンネルの順に畳む
cw stop --orphans    # 名前の無い割り当て ([?] で出るもの) も解放する
cw stop --no-logs    # 止める前のログ回収を飛ばす
```

`stop` が「not found」でも安心しない。一覧から消えることと実体が止まることは別なので、
最後に出る `cw sessions` で現物を見る。`[?]` で始まる名前なしのものが残っていたら、
`cw stop --orphans` で解放する。名前が無い割り当ては `cw stop` だけでは引けず、放って
おくと課金が続く。

### 他のプロジェクトから頼む

生成サーバは1台に1つ。**clone を増やさず、そこへ頼む。**

- 人が頼む: 呼ぶ側のディレクトリでそのまま `cw`。生成物はそこに出て、記録とキーは
  comfy-wrapper 側に残る
- コードが頼む: HTTP。宛先はホストの `http://127.0.0.1:8000`、呼ぶ側がコンテナの中なら
  `http://host.docker.internal:8000` (compose に
  `extra_hosts: ["host.docker.internal:host-gateway"]` が要る)

**呼ぶ側の compose に comfy-wrapper のサービスやネットワークを足さない。** 共有ネットワーク
`comfy-net` 経由 (`http://tunnel:8000`) の経路は外した。見るのはホストの 8000 番だけでよい。

頼めることは下の「API リファレンス」にある。ランタイムの確保と停止は comfy-wrapper 側の
仕事なので、呼ぶ側がやるのは生成の投入と回収だけ。

キーはプロジェクトごとに `cw key issue --name <用途>` で発行し、`cw key push` でランタイムへ
反映する。渡るのは SHA-256 のハッシュだけ。

## API リファレンス

`cw` を使えないところ (他のプロジェクトのコード、別言語) はここを直に叩く。すべて
`Authorization: Bearer <key>` が要る。**`/health` だけ無認証。**

大半は `cw` と同じことができる。**`cw` からは出せないのは次のもの。** ここが要るときは
HTTP を選ぶ。

- 動画の `task: "r2v"` と `ref_images` / `ref_videos` / `ref_audios` (H3 の参照つき生成)
- 動画の `audio` (wan2.2-s2v の音声駆動)、`fps` (LTX の可変 fps)、`steps` / `seed`、
  `lightning` (Wan2.2 の 4steps 蒸留 LoRA)、`ref_image_size`
- 仕上げの `keep_audio`
- `GET /v1/jobs` (サーバ側のジョブ一覧。`cw jobs` が見るのは手元の台帳)、
  `DELETE /v1/jobs/{id}` (実行中の中断)

逆に運用 (確保・停止・監視・ログ・キー) と `cw measure` は API に無い。

宛先とキーの解決・瞬断のリトライ・落ちている理由の切り分けは `src/lib/colab_link.py` が
持っているので、python からなら `colab_link.request(endpoint, key, method, path, body)` を使う。

| | | |
|---|---|---|
| GET | `/health` | ComfyUI の生死と動画ウェイトの有無。認証不要 |
| GET | `/v1/info` | GPU 名・VRAM・載っているモデル・ジョブ数 |
| GET | `/v1/models` | どのモデルで何ができるか。**呼ぶ側で表を写さない** |
| POST | `/v1/generate` | 動画の生成を投入。202 で `job_id` |
| POST | `/v1/images/generate` | 静止画の生成を投入。202 で `job_id` |
| POST | `/v1/postprocess` | 生成済み動画の仕上げを投入。202 で `job_id` |
| GET | `/v1/jobs/{id}` | 状態・待ち位置・エラー |
| GET | `/v1/jobs/{id}/video` | 完成した mp4 (`video/mp4`) |
| GET | `/v1/jobs/{id}/image` | 完成した png (`image/png`) |
| GET | `/v1/jobs` | ジョブ一覧 |
| DELETE | `/v1/jobs/{id}` | 実行中のジョブを中断 |

### 投入と回収の流れ

**投入は 202 ですぐ返り、生成は裏で走る。** 届いていれば投げ直さない (二重生成は GPU
時間をそのまま捨てる)。ジョブの記録はサーバのメモリにしかないので、**ランタイムを
止める前に回収する。**

```bash
curl -X POST "$COLAB_ENDPOINT/v1/generate" \
  -H "Authorization: Bearer $COLAB_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"ltx-2.5","task":"t2v","prompt":"...","duration":5,"aspect":"16x9"}'
# -> 202 {"job_id":"...","status":"queued","width":...,"height":...,"seconds":5.0,...}

curl -H "Authorization: Bearer $COLAB_API_KEY" "$COLAB_ENDPOINT/v1/jobs/$JOB"
# -> {"status":"running","queue_position":0,...} -> "succeeded" になったら取りに行く

curl -H "Authorization: Bearer $COLAB_API_KEY" \
  "$COLAB_ENDPOINT/v1/jobs/$JOB/video" -o clip.mp4
```

`status` は `queued` / `running` / `succeeded` / `failed` / `canceled`。`failed` のときは
`error` に理由が入る。`/health` に届かなければ「いま動いていない」というだけ。

### `POST /v1/generate` (動画)

画像・音声・動画は base64 か data URI で渡す。

| フィールド | 既定 | |
|---|---|---|
| `model` | `minimax-h3` | `minimax-h3` / `wan2.2` / `wan2.2-5b` / `wan2.2-s2v` / `ltx-2.3` / `ltx-2.3-gguf` / `ltx-2.3-ic` / `ltx-2.5` |
| `task` | `i2v` | `t2v` / `i2v` / `r2v` (r2v は minimax-h3 のみ) |
| `prompt` | 必須 | |
| `negative` | モデルの既定 | Wan / LTX 用 |
| `first_frame` | | `i2v` は必須 |
| `last_frame` | | ltx-2.5 / wan2.2 / minimax-h3 |
| `ref_images` / `ref_videos` / `ref_audios` | `[]` | r2v の参照 (最大 9 / 3 / 3) |
| `audio` | | 口と動きを駆動する音声。**wan2.2-s2v 専用** |
| `duration` | `5.0` | 0.2〜20秒。モデルのフレームグリッドに切り上げ |
| `aspect` | first_frame から判定 | `16x9` / `9x16` / `1x1` など |
| `megapixels` | `0.4` | 0.1〜2.5。生成解像度 |
| `fps` | モデルの既定 | LTX のみ可変 (8〜50) |
| `steps` / `seed` | モデルの既定 / `-1` | `-1` でランダム |
| `lightning` | `true` | Wan2.2 14B の 4steps 蒸留 LoRA (速いが動きは大人しい) |
| `output_width` / `output_height` | | 両方指定でそのサイズちょうど (center crop) |
| `upscale_model` | lanczos | ComfyUI の `upscale_models/` のファイル名 |

### `POST /v1/images/generate` (静止画)

| フィールド | 既定 | |
|---|---|---|
| `model` | `z-image` | `z-image` / `qwen-image` / `qwen-image-edit` |
| `prompt` | 必須 | |
| `negative` | モデルの既定 | |
| `aspect` | `1x1` | `16x9` / `9x16` / `1x1` |
| `ref_images` | `[]` | 最大3枚。**渡すと model によらず編集経路になる** |
| `steps` / `seed` | モデルの既定 / `-1` | |
| `loras` | `[]` | `(ファイル名, 強さ)` を最大4つ |

完成品は `/v1/jobs/{id}/image` から png で取る。

### `POST /v1/postprocess` (仕上げ)

フレーム補間とアップスケール。**尺は変えない。** 補間したぶん fps が上がるので、
16fps を `multiplier: 3` にすると 48fps になる。24p が要るなら手元の ffmpeg で落とす。

| フィールド | 既定 | |
|---|---|---|
| `video` | 必須 | mp4 の base64 か data URI |
| `source_fps` | 必須 | 入力の fps。出力 fps の計算に使う |
| `multiplier` | `1` | 1〜16。1 なら補間しない |
| `target_width` / `target_height` | | 出力サイズ |
| `upscale_model` / `interp_model` | 既定 | ComfyUI 側のファイル名 |
| `keep_audio` | `true` | |

## モデルの選び方

どんなモデルが使えて、費用はどれくらいかかるかは `cw models` で調べることができる。

- 1セッションに1モデル
  - ディスクの都合で H3 (42.5GB) と Qwen 系 (28GB) は同居できない
  - 画像と動画の両方を頼まれたら、どちらを先にやるかを決めて別セッションにする
- 参照つき生成 (r2v) は `minimax-h3` だけ
  - `ref2va` のウェイト (+21GB) が要るので、構築時に `--models "fl2va ref2va"` を渡す
  - 参照画像でキャラを固定したいなら H3、静止画の編集なら `qwen-image-edit`
- GPU は既定の `L4` でよい
  - A100 が要るのは `ltx-2.3` の fp8 だけで、`ltx-2.5` は int8 で L4 に載る
- LTX 系の Hugging Face リポジトリは gated
  - 未同意のトークンだと取得が 403 になるのに構築は止まらず、ウェイトの無いセッションが
    立ち上がる (2026-08-17 に踏んだ)
  - 同意はモデルページの "Agree and Access"。2.3 と 2.5 は別リポジトリなので別々に要る

## プロンプトを書く

プロンプトを詰めるのは生成の前に終わらせる。書き直しは無料だが、生成のやり直しは GPU
時間を使う。モデルごとに作法があるので、書き方はそれぞれのスキルが持っている。

- LTX-2.5 は `ltx-prompt` スキル
  - 散文の書き方、多ショットのつなぎ方、8k+1 のグリッド
- MiniMax H3 は MiniMax 公式の `h3-prompt-writing` スキル
  - 入っていなければ `cw init skills` で一緒に入る

### H3 でこのラッパ固有になるところ

公式スキルは記法だけを持っている。ここに頼むときの対応は次のとおり。

| 記法上のタスク | 渡す素材 | このラッパ |
|---|---|---|
| T2VA | なし | `cw video --prompt "..."` |
| I2VA | 先頭フレーム | `cw video ./first.png` |
| FL2VA | 先頭と末尾 | `cw video ./first.png --last-frame ./last.png` |
| Ref2VA | 参照画像、動画、音声 | `cw` に口が無い。`POST /v1/generate` に `task: "r2v"` と `ref_images` / `ref_videos` / `ref_audios` (最大 9 / 3 / 3) を直に投げる |
| L2VA | 末尾フレームだけ | 経路なし。先頭フレーム無しの末尾は受け付けない |

尺は API 側で 24fps、17k+5 フレームのグリッドに切り上がり、124〜362 フレーム
(5.167〜15.083秒) に収まる。`--duration` に何を渡してもこのどれかになるので、プロンプト
内のタイムスタンプは生成尺を基準に書く。5秒未満を頼まれても 5.167秒生成されるため、
切って使う前提なら切られる側に見せ場を置かない。

| フレーム | 秒 | | フレーム | 秒 |
|---|---|---|---|---|
| 124 | 5.167 | | 243 | 10.125 |
| 141 | 5.875 | | 260 | 10.833 |
| 158 | 6.583 | | 277 | 11.542 |
| 175 | 7.292 | | 294 | 12.250 |
| 192 | 8.000 | | 311 | 12.958 |
| 209 | 8.708 | | 328 | 13.667 |
| 226 | 9.417 | | 362 | 15.083 |

`negative` は Wan と LTX 用で、H3 では使わない。除外したい要素は肯定形で書き切って潰す。

## やってはいけないこと

- ComfyUI (8188) と `.colab/` を外に出さない
  - ComfyUI は認証が無く、ワークフローの投入は任意コード実行と等価になる
  - `.colab/` には OAuth トークン、SSH 鍵、API キー、HF トークンが入っている
- 届かないときに確保し直さない
  - 生きているランタイムを捨てて、もう一度 GPU を掴むことになる
  - `cw tunnel restart` の連打も避ける。SSH は1ランタイムに1本だけで、叩き直すと
    `Already-active SSH session (HTTP 429)` で締め出される
- セッションが無いのにトンネルを上げたままにしない
  - その名前でランタイムを確保してしまう (実測では CPU ランタイム)
  - `cw stop` はトンネルまで畳む
- 生成サーバを増やさない
  - 1台に1つだけ動かし、clone を増やさない。他のプロジェクトからは `cw` か HTTP で頼む
    (呼ぶ側の compose に comfy-wrapper のサービスやネットワークを足さない)
  - 並列接続にも複数セッションの同時実行にも対応していないので、複数のプロジェクトから
    同時には使えない。どのプロジェクトからでも叩けるが、頼むのは順番に

## うまくいかないとき

| 症状 | 見るところ |
|---|---|
| `value_not_in_list` (400) | 構築したモデルと `--model` がずれている。セッションはそのまま、`--model` を直して投げ直す |
| 生成が届かない | エラー文が原因を名指しする (認証切れ、セッション消失、トンネル切れ)。`cw status` |
| ComfyUI が応答しない | 構築がまだ終わっていない。`cw status` の「疎通」 |
| ウェイトが載らない | `cw status` の「動画ウェイト」。静止画モデルはここに出ないので `cw image` が通るかで見る |
| 落ちた理由が知りたい | **止める前に** `cw logs setup` / `api` / `comfyui`。止めると `/content` ごと消える |

監視が自動停止したときの回収先は、clone 先の `works/.rescue/<セッション>/`。
