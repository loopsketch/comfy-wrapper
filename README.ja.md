# comfy-wrapper

[English](README.md) | 日本語

オープンウェイトの動画・静止画モデルを Google Colab の GPU で動かし、手元からは
キー認証つきの HTTP API として使うためのラッパです。

Colab ランタイム上で [ComfyUI](https://github.com/comfyanonymous/ComfyUI) を動かし、その前段に
小さな FastAPI を置いて、SSH トンネルで手元へ通します。ホストに入れるのは Docker だけです。

```
POST /v1/generate  ->  job_id   (202 で返り、生成は裏で走る)
GET  /v1/jobs/{id} ->  状態
GET  /v1/jobs/{id}/video -> mp4
```

## 特徴

- **複数モデルを同じ API で扱える。** 動画は MiniMax H3 / Wan2.2 (14B MoE・TI2V-5B・S2V) /
  LTX-2.3 (fp8・GGUF・IC-LoRA)、静止画は Z-Image と Qwen-Image / Qwen-Image-Edit。
  モデルの切り替えはリクエストのフィールド1つです。
- **音声つきの生成。** MiniMax H3 は映像とステレオ音声を1パスで同時に生成し、画像・動画・音声を
  参照として受け取れます。LTX-2.3 も音声つきで出力し、Wan2.2-S2V は入力音声で駆動します。
- **ComfyUI は外に出さない。** ComfyUI には認証機構が無いため、ランタイム上では `127.0.0.1` に
  閉じたままにします。外から届くのは SSH トンネル越しの FastAPI だけで、Bearer キーが要ります。
- **平文のキーは手元から出ない。** `colab_key.sh` が手元でキーを発行し、ランタイムへ送るのは
  SHA-256 のハッシュだけです。
- **ホストには何も入れない。** コンテナは `client` / `colab` / `tunnel` の3つで、どれも
  `python:3.12-slim` です。Python も CUDA も ComfyUI も手元には要りません。
- **課金を止める見張り。** Colab は GPU の稼働時間で課金されるので、`colab_watch.sh` が
  作業中はランタイムを保ち、アイドルや上限時間に達したら成果物を回収してから停止します。
- **仕上げの経路つき。** `POST /v1/postprocess` でフレーム補間とアップスケールを通し、
  4K / 24p まで持っていけます。

## 構成

```
[client コンテナ]   手元。生成の投入と回収
  |  http://tunnel:8000 + Bearer <key>
  v
[tunnel コンテナ]   ssh -L (colab ssh --proxy-mode を ProxyCommand に)
  |
  |  ~~~ Colab ランタイム ~~~
  v
[FastAPI :8000]     認証・ジョブ管理・入力の受け渡し
     |  127.0.0.1 のみ
     v
[ComfyUI :8188]     ワークフローの実行
```

宛先は `http://tunnel:8000` に固定で、セッションをまたいでも変わりません。

## 前提

| | |
|---|---|
| ホスト | Docker と Compose **v2** が動く Linux / macOS / WSL2 (`docker-compose` では動きません)。ポート 8000 と 8188 が空いていること |
| Google アカウント | **Colab のコンピューティングユニットが要ります。** 無料枠では CLI から GPU ランタイムを確保できません |
| ブラウザ | 初回の認証1回だけ。コードの貼り付け方式なので**別のマシンのブラウザでよく**、ヘッドレスなサーバでも通ります |
| SSH 鍵 | ed25519 か ecdsa。RSA は拒否されます |
| ネットワーク | 手元から `colab.pa.googleapis.com` / `oauth2.googleapis.com`、ランタイムから `github.com` と `huggingface.co` |
| GPU | 既定は L4 (24GB)。`ltx-2.3` の fp8 だけは A100 が要ります |

`.colab/hf-token` の Hugging Face トークンは任意ですが実質必須です。未認証だと取得が
大きく絞られ(実測で 622MB/s が 8MB/s)、その待ち時間はそのまま GPU の課金になります。

## 使い方

### 1. 一度だけ: 認証と鍵

```bash
docker compose up -d colab                  # 常駐させる(理由は下記)
docker compose exec colab colab sessions    # 認証。URL をブラウザで開きコードを貼る

docker compose exec colab mkdir -p /app/.colab/.ssh
docker compose exec colab ssh-keygen \
  -t ed25519 -N "" -C "comfy-wrapper" -f /app/.colab/.ssh/id_ed25519
```

トークンと鍵は `.colab/`(git 管理外)に残ります。`colab` コンテナが 30分おきに
OAuth トークンを延長するので、人が要るのは初回だけです。

> `colab` サービスは常駐させてください。`colab new` は keep-alive デーモンを detached な
> 子プロセスとして起こすため、`docker compose run --rm` で叩くとコマンド終了でコンテナごと
> 消えてデーモンも道連れになり、ランタイムがアイドル刈り取りされます。

### 2. ランタイムを確保して構築する

```bash
src/scripts/colab.sh new -s comfy --gpu L4
src/scripts/colab_watch.sh comfy 90              # 見張り。上限90分

src/scripts/colab_push.sh comfy                  # src/ を送る
src/scripts/colab_key.sh comfy                   # キーのハッシュを送る
src/scripts/colab.sh exec -s comfy -f src/scripts/colab_setup.py
src/scripts/colab.sh exec -s comfy -f src/scripts/colab_setup_status.py
```

ComfyUI の導入とウェイトの取得で 15〜25 分かかります。`colab exec` は同期実行で長時間の
処理では WebSocket が切れるため、構築は `nohup` で切り離し、進捗は
`colab_setup_status.py` で見ます。Wan / LTX 用は `colab_video_setup.py`、静止画モデル用は
`colab_image_setup.py` に差し替えてください。

### 3. トンネルを張る

```bash
src/scripts/colab.sh exec -s comfy -f src/scripts/colab_serve_status.py
docker compose up -d tunnel
```

### 4. 生成する

```bash
# 動画
docker compose run --rm client src/scripts/smoke_test.py --aspect 9x16 --out works/smoke.mp4
docker compose run --rm client src/scripts/smoke_test.py --first-frame works/still.png

# 静止画(完成まで待って png を書き出す)
docker compose run --rm client src/scripts/generate_image.py \
  submit "a cat on a neon-lit rooftop" --model z-image --aspect 9x16
docker compose run --rm client src/scripts/generate_image.py \
  submit "image 1 の人物が公園のベンチに座っている" --ref works/ref.png
```

### 5. 止める

```bash
src/scripts/colab.sh stop -s comfy
src/scripts/colab.sh sessions      # ローカルの記録ではなくサーバーに問い合わせる
docker compose stop tunnel
```

GPU の稼働時間で課金されるので、止まっていることを必ず確かめてください。

### 無人で1本流す

`colab_run.sh` は確保 → 構築 → 実行 → 停止 をまとめて流し、途中で何が起きても最後に必ず
セッションを止めます。`--` のあとは `client` コンテナの python にそのまま渡ります。

```bash
src/scripts/colab_run.sh --setup video --models ltx-2.3-gguf --gpu L4 --max 60 -- \
  src/scripts/measure_video.py submit works/still.png --model ltx-2.3-gguf --aspect 9x16
```

## Claude Code から使う

`.claude/skills/colab-comfy/` にスキルを置いてあります(git 管理下・共有対象)。
「画像を作って」「この静止画を動かして」のように頼むと、状態の確認 → 確保 → 構築 →
生成 → 停止 までの手順と、課金を止めるための鉄則をスキルが持っています。
`.claude/settings.json` は個人設定なので git 管理外です。

## 自前のコードから呼ぶ

宛先とキーの解決、瞬断のリトライ、落ちている理由の切り分けは `lib/colab_link.py` に
入っています。

```python
import sys, base64, json
sys.path.insert(0, "/app/src")
from lib import colab_link

endpoint = colab_link.read_endpoint()      # 既定 http://tunnel:8000
key = colab_link.require_api_key()         # .colab/colab-api-key を読む

still = base64.b64encode(open("works/still.png", "rb").read()).decode()
status, body = colab_link.request(
    endpoint, key, "POST", "/v1/generate",
    {
        "model": "ltx-2.3-gguf",
        "task": "i2v",
        "prompt": "slow dolly-in, neon reflections on wet asphalt",
        "first_frame": still,
        "duration": 5.0,
        "aspect": "9x16",
        "output_width": 1080, "output_height": 1920,
    },
)
job = json.loads(body)["job_id"]           # 202 で返る。生成は裏で回る
```

投入は 202 ですぐ返るので、**まとめて投げてから順に回収する**のが基本の使い方です
(モデルのロードはセッションに1回で済みます)。

```bash
curl -X POST "$COLAB_ENDPOINT/v1/generate" \
  -H "Authorization: Bearer $COLAB_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"task":"t2v","prompt":"rain on neon streets","duration":5,"aspect":"16x9"}'
```

## モデル

**1セッションに1モデル。** どれも 30〜45GB あり、`/content` にも VRAM にも同時には載りません。

| `model` | ウェイト計 | 音声 | fps | 素の解像度 | タスク |
|---|---|---|---|---|---|
| `minimax-h3` (既定) | 42.5GB | あり | 24 | 短辺768・最大 768x1344 | t2v / i2v / 先頭・末尾 / **r2v** |
| `wan2.2` (14B MoE) | 約38GB | なし | 16 | 832x480 / 1280x720 | t2v / i2v / 先頭・末尾 |
| `wan2.2-5b` (TI2V) | 約18GB | なし | 24 | 1280x704 | t2v / i2v |
| `wan2.2-s2v` (S2V 14B) | 約25GB | 入力を載せる | 16 | 832x480 | **音声で駆動** |
| `ltx-2.3` (22B fp8) | 約42GB | あり | 8〜50(既定25) | 1280x704 / 1920x1088 | t2v / i2v |
| `ltx-2.3-gguf` (Q4_K_M) | 約28GB | あり | 8〜50(既定25) | 同上 | t2v / i2v |
| `ltx-2.3-ic` (+IC-LoRA) | 約29GB | あり | 同上 | 同上 | i2v + **参照シート** |

- **参照つき生成 (r2v) は `minimax-h3` だけ。** プロンプト側から `<Picture 1>` `<Video 1>`
  `<Audio 1>` で参照を指します。
- **L4 では `ltx-2.3-gguf` が一番速い。** MiniMax H3 の 5.6倍、Wan2.2 の 2.3倍で、しかも
  音声つき 25fps です。i2v をまとめて回すならこれが既定候補になります。
- **`ltx-2.3` の fp8 は L4 に載りません**(本体 29GB)。L4 では GGUF 版、fp8 は A100 向けです。
- `duration` はモデルごとの latent フレームグリッドに切り上げられます。実際の尺は
  レスポンスの `seconds` に入ります。

実測(L4、480p・約5秒・9x16、同じ静止画・同じ seed・同じプロンプト):

| model | 1本目(ロード込み) | 2本目(ロード済み) | 映像1秒あたり | 1本 |
|---|---|---|---|---|
| `minimax-h3` (fp8, 20step) | 471秒 | 400秒 | 77.4秒 | 2.02円 |
| `wan2.2` (4step 蒸留) | 213秒 | 165秒 | 32.6秒 | 0.83円 |
| `ltx-2.3-gguf` (Q4_K_M) | **127秒** | **72秒** | **14.0秒** | **0.36円** |

いずれも L4・ウェイト取得済みでの値です。手元の環境で測るなら
`scripts/measure_video.py` を使ってください(ロード込みとロード済みを分けて出します)。

## API

`/health` を除き、すべて `Authorization: Bearer <key>` が必要です。

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/health` | 認証不要。トンネル疎通と、モデルごとのウェイトの有無 |
| GET | `/v1/info` | GPU 名・VRAM・使用中のモデル |
| POST | `/v1/generate` | 動画の生成を投入。202 で `job_id` を返す |
| POST | `/v1/images/generate` | 静止画の生成を投入 |
| POST | `/v1/postprocess` | 生成済み動画の仕上げ(補間・拡大)を投入 |
| GET | `/v1/jobs/{id}` | 状態・待ち位置・エラー |
| GET | `/v1/jobs/{id}/video` | 完成した mp4 |
| GET | `/v1/jobs/{id}/image` | 完成した png |
| DELETE | `/v1/jobs/{id}` | 実行中のジョブを中断 |
| GET | `/v1/jobs` | ジョブ一覧 |

`POST /v1/generate` の主なフィールド:

| フィールド | 既定 | 内容 |
|---|---|---|
| `model` | `minimax-h3` | 上のモデル表を参照 |
| `task` | `i2v` | `t2v` / `i2v` / `r2v`(`r2v` は H3 のみ) |
| `prompt` | — | 参照は `<Picture i>` `<Video k>` `<Audio j>` で指す |
| `negative` | モデルの既定 | Wan / LTX 用。H3 は使わない |
| `first_frame` / `last_frame` | — | base64 か data URI。`i2v` は `first_frame` 必須。`last_frame` は H3 と `wan2.2` のみ |
| `ref_images` / `ref_videos` / `ref_audios` | `[]` | `r2v` 用。最大 9 / 3 / 3 |
| `audio` | — | `wan2.2-s2v` の駆動音声 |
| `duration` | 5.0 | 秒。モデルのフレームグリッドに切り上げ |
| `aspect` | 自動 | `16x9` `9x16` `1x1` `4x3` `3x4` `21x9`。省略時は `first_frame` から判定 |
| `megapixels` | 0.4 | 生成キャンバスの画素数(1MP = 1024x1024)。0.4 で 16:9 → 864x480 |
| `fps` | モデルの既定 | LTX のみ指定可(8〜50) |
| `steps` | モデルの既定 | H3 は 20、Wan は蒸留ありで 4 / なしで 20 |
| `lightning` | `true` | `wan2.2` (14B) で 4step 蒸留 LoRA を使う |
| `seed` | -1 | -1 でランダム |
| `output_width` / `output_height` | — | 両方指定すると出力をそのサイズちょうどにする(center crop) |
| `upscale_model` | — | `upscale_models/` のファイル名。省略時は lanczos |

`POST /v1/images/generate` は `model`(`z-image` / `qwen-image` / `qwen-image-edit`)・
`prompt`・`aspect`・`ref_images`(渡すと model によらず edit 経路)・`loras`・`seed`・
`steps` を取ります。

## 仕上げ(補間とアップスケール)

`POST /v1/postprocess` は ComfyUI 本体のノードだけで、公式ブループリントと同じ構成の
フレーム補間とアップスケールを通します。

```
LoadVideo -> GetVideoComponents -> FrameInterpolate -> ImageUpscaleWithModel
          -> ImageScale -> CreateVideo -> SaveVideo
```

```bash
docker compose run --rm client src/scripts/postprocess.py \
  submit works/clip.mp4 --size 4k-portrait --multiplier 2
docker compose run --rm client src/scripts/postprocess.py status
```

使うモデルは合計 226MB で、どの構築にも既定で入ります(`--no-postprocess` で外せます)。
補間は尺を変えず fps が上がります。倍率は整数しか取れないので、生成側の fps を目標が
割り切れる値に寄せてください。効くのは VRAM ではなくシステム RAM で、しかも目標サイズでは
なく拡大モデルが吐く中間サイズ(入力 x 倍率)で決まります。`postprocess.py` は入力と目標の
比から x2 / x4 を選び、見積もりが 30GB を超えたら投入前に止めます。

## ディレクトリ構成

```
docker-compose.yml     client / colab / tunnel の3サービス
docker/                各サービスのイメージ
.claude/skills/        Claude Code 用のスキル(確保・構築・生成・停止の手順)
src/
  server/              Colab 上で動く FastAPI + ComfyUI 橋渡し
  setup/               ウェイトの取得
  scripts/             手元と Colab 側の運用スクリプト
  lib/                 手元側の共有層(宛先・キー・リトライ・単価)
  notebooks/           旧経路(凍結)
works/                 生成物・測定結果・見張りの回収先(git 管理外)
```

主なモジュール(パスは `src/` からの相対):

| パス | 役割 |
|---|---|
| `server/app.py` | FastAPI 本体。認証・ジョブ管理・ComfyUI への橋渡し |
| `server/{h3,wan,ltx,image,post}_workflows.py` | 各モデルのワークフロー生成(ComfyUI API フォーマット) |
| `server/video_common.py` | 共通の寸法・尺の計算と出力段の拡大 |
| `server/comfy.py` | ComfyUI クライアント(投入・ポーリング・出力回収) |
| `server/auth.py` | キーの発行・保存・検証(平文は保存しない) |
| `setup/download_*.py` | ウェイトの取得 |
| `scripts/colab.sh` | Colab CLI のラッパ(常駐コンテナ経由で叩く) |
| `scripts/colab_run.sh` | 確保 → 構築 → 実行 → 停止 を無人で1本流す |
| `scripts/colab_watch.sh` | 見張り。keep-alive・進捗記録・自動停止と成果物の回収 |
| `scripts/generate_image.py` | 静止画の生成を投入して png を回収する |
| `scripts/measure_video.py` | 生成時間の測定(ロード込み/ロード済みを分けて出す) |
| `scripts/smoke_test.py` | 公開 API への疎通テスト |
| `lib/colab_link.py` | 宛先・キーの解決、リトライ、障害の切り分け |
| `lib/video_sizes.py` | `480p` / `720p` / `1080p` をモデルごとの生成キャンバスと出力寸法に落とす |

## 設定

標準の経路では設定するものはありません。宛先は固定で、キーは `.colab/colab-api-key` から
読みます。以下は別の構成にするときのための変数です。

| 変数 | 既定 | 場所 |
|---|---|---|
| `COLAB_ENDPOINT` | `http://tunnel:8000` | client。Colab 以外に立てたときに上書きする |
| `COLAB_SESSION` | `comfy` | tunnel。どのセッションへ通すか |
| `COLAB_AUTH_LOOP_MINUTES` | `30` | colab。OAuth 延長の間隔 |
| `TZ` | `Asia/Tokyo` | colab。見張りのログの時刻 |
| `COMFY_URL` | `http://127.0.0.1:8188` | server |
| `WRAPPER_KEYS_PATH` | ランタイム上のパス | server。キーストアの置き場 |
| `H3_*` / `LTX_*` / `CW_*` | モデルごと | server。ウェイトのファイル名の上書き |

## Colab 以外で動かす

`src/server/` は Colab に依存していません。ComfyUI とウェイトを置いた環境で以下を動かし、
同じようにトンネルなりリバースプロキシなりを前段に置けばそのまま使えます。

```bash
COMFY_URL=http://127.0.0.1:8188 WRAPPER_KEYS_PATH=/data/keys.json \
  python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

## セキュリティ

ComfyUI は設計として認証を持たず、ワークフローを投げられること自体が任意コード実行と
等価になります。実際に露出インスタンスを狙ったクリプトマイニング・ボットネットの
キャンペーンも動いています。この構成では ComfyUI を `127.0.0.1` に閉じ、外に出るのは
SSH トンネル越しの FastAPI(Bearer キー必須)だけです。**ポート 8188 は公開しないでください。**

アクセスキーは手元で発行して `.colab/comfy-keys.json` に保存し、ランタイムへ送るのは
SHA-256 のハッシュだけなので、平文のキーはリモートに存在しません。

## 免責事項

本ソフトウェアは無保証で提供されます。作者は一切の責任を負いません。

- **課金は利用者の責任です。** 本プロジェクトは Colab の有料 GPU ランタイム(都度課金)を
  使います。見張り(`colab_watch.sh`)は上限時間とアイドルでランタイムを止めますが、
  **停止を保証するものではありません。** 認証切れ・ネットワーク断・スクリプトの異常終了などで
  停止に失敗し、意図しない課金が発生しても、作者は一切の責任を負いません。ランタイムが
  止まっていることは、利用者自身が `src/scripts/colab.sh sessions` で確認してください。
- **モデルと生成物の利用も利用者の責任です。** 各モデルのライセンス・利用規約の確認と遵守、
  および生成物の取り扱いは利用者が行うものとし、それらに起因して問題が発生しても、
  作者は一切の責任を負いません。
- **上記は例示であり、これらに限りません。** 本ソフトウェアの使用または使用不能から生じる
  いかなる損害(意図しない課金、データの消失、認証情報の漏洩、外部サービスの規約違反・
  仕様変更・停止、第三者との紛争などを含みますがこれらに限りません)についても、
  作者は一切の責任を負いません。利用は自己責任で行ってください。

## ライセンス

MIT — [LICENSE](LICENSE) を参照してください。Copyright (c) 2026 LOOPSKETCH.

これが及ぶのはこのリポジトリのコードだけです。ウェイトにはそれぞれの規約があり、
MiniMax H3 は MiniMax H3 Community License(USA/EU/UK/韓国向けには別途申請フォーム)、
Wan2.2 は Apache-2.0、LTX-2.3 は LTX-2 Community License Agreement です。商用利用の前に
確認してください。

## 謝辞

- [MiniMaxAI/MiniMax-H3](https://huggingface.co/MiniMaxAI/MiniMax-H3) と
  [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3)(量子化ウェイト)
- [Wan-AI](https://huggingface.co/Wan-AI) / [Lightricks/LTX-2.3](https://huggingface.co/Lightricks/LTX-2.3)
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) と
  [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF)
- [google-colab-cli](https://github.com/googlecolab/google-colab-cli)
