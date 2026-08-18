# comfy-wrapper

[English](README.md) | 日本語

オープンウェイトの動画・静止画モデルを Google Colab の GPU で動かし、手元からは
キー認証つきの HTTP API として使うためのラッパです。

Colab ランタイム上で [ComfyUI](https://github.com/comfyanonymous/ComfyUI) を動かし、その前段に
小さな FastAPI を置いて、SSH トンネルで手元へ通します。ホストに入れるのは Docker と、
依存ゼロの CLI `cw` だけです。

```bash
uv tool install --editable /path/to/comfy-wrapper

cw up --setup image --models z-image --gpu L4 --max 60
cw image "a pug on a sunlit windowsill" --out ./hero.png
cw stop
```

```
POST /v1/generate  ->  job_id   (202 で返り、生成は裏で走る)
GET  /v1/jobs/{id} ->  状態
GET  /v1/jobs/{id}/video -> mp4
```

## 特徴

- **複数モデルを同じ API で扱える。** 動画は MiniMax H3 / Wan2.2 (14B MoE・TI2V-5B・S2V) /
  LTX-2.3 (fp8・GGUF・IC-LoRA) / LTX-2.5、静止画は Z-Image と Qwen-Image / Qwen-Image-Edit。
  モデルの切り替えはリクエストのフィールド1つです。
- **音声つきの生成。** MiniMax H3 は映像とステレオ音声を1パスで同時に生成し、画像・動画・音声を
  参照として受け取れます。LTX-2.3 / LTX-2.5 も音声つきで出力し、Wan2.2-S2V は入力音声で駆動します。
- **ComfyUI は外に出さない。** ComfyUI には認証機構が無いため、ランタイム上では `127.0.0.1` に
  閉じたままにします。外から届くのは SSH トンネル越しの FastAPI だけで、Bearer キーが要ります。
- **平文のキーは手元から出ない。** `cw key issue` が手元でキーを発行し、ランタイムへ送るのは
  SHA-256 のハッシュだけです。
- **呼ぶ側は3つを知らなくてよい。** リポジトリの場所・compose のサービス名・コンテナ内の
  パスを `cw` が隠します。生成はホストの python で直接動く (依存ゼロ・stdlib だけ) ので、
  `--ref ./ref.png` も `--out ./hero.png` も CWD 相対のまま通ります。
- **ホストに CUDA も ComfyUI も要らない。** コンテナは `client` / `colab` / `tunnel` の3つで、
  どれも `python:3.12-slim` です。
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
| `cw` | Python **3.11以上**。入れるのは `uv` か `pipx`。パッケージ側の依存はゼロで、ffmpeg も要りません (`cw post` は mp4 のヘッダを自前で読みます。mp4 以外を仕上げるときだけ ffprobe を使います) |
| Google アカウント | **Colab のコンピューティングユニットが要ります。** 無料枠では CLI から GPU ランタイムを確保できません |
| ブラウザ | 初回の認証1回だけ。コードの貼り付け方式なので**別のマシンのブラウザでよく**、ヘッドレスなサーバでも通ります |
| SSH 鍵 | ed25519 か ecdsa。RSA は拒否されます |
| ネットワーク | 手元から `colab.pa.googleapis.com` / `oauth2.googleapis.com`、ランタイムから `github.com` と `huggingface.co` |
| GPU | 既定は L4 (24GB)。`ltx-2.3` の fp8 だけは A100 が要ります |

`.colab/hf-token` の Hugging Face トークンは任意ですが実質必須です。未認証だと取得が
大きく絞られ(実測で 622MB/s が 8MB/s)、その待ち時間はそのまま GPU の課金になります。

## 使い方

### 0. `cw` を入れる

```bash
uv tool install --editable /path/to/comfy-wrapper   # pipx install -e . でも入ります
cw --help
```

`--editable` で入れるのは、`cw` がこのソースツリーをそのまま使うからです。**生成系
(`image` / `video` / `post` / `jobs` / `models`) はホストの python で直接動き**、
運用系 (`up` / `stop` / `status` など) だけが内側で `docker compose` を呼びます。
リポジトリを別の場所へ移したときは `COMFY_WRAPPER_HOME` で指せます。

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
cw up --setup image --models z-image --gpu L4 --max 60
```

確保 → コードとキーの送付 → 構築 → トンネル まで無人で進み、**セッションは残ります**
(`cw run` と違って止めません)。ComfyUI の導入とウェイトの取得で 15〜25 分かかります。
`--setup` は `image`(静止画)/ `video`(Wan2.2 / LTX)/ `h3`(MiniMax H3)。
`--max` は見張りの上限分で、超えたら見張りが強制停止します。

```bash
cw status     # compose / セッション / 見張り / 疎通 を1画面で
```

### 3. 生成する

パスは **CWD 相対**です。どのディレクトリから叩いても構いません。

```bash
# 静止画(完成まで待って png を書き出す)
cw image "a cat on a neon-lit rooftop" --model z-image --aspect 9x16 --out ./cat.png
cw image "image 1 の人物が公園のベンチに座っている" --ref ./ref.png

# 動画。画像を渡すと i2v、渡さなければ t2v
cw video ./cat.png --model ltx-2.5 --out ./clip.mp4
cw video --prompt "rain on neon streets" --duration 5

# 仕上げ(フレーム補間 + アップスケール)
cw post ./clip.mp4 --size 4k --multiplier 2

# まとめて投げて、あとで回収する
cw image "..." --no-wait
cw jobs

cw models     # どのモデルで何ができるか・1秒あたりいくらか
```

出力は CWD に書き、**ジョブの台帳はリポジトリの `.colab/jobs/` に絶対パスで**残ります。
呼ぶ側のプロジェクトを汚さず、別のディレクトリから `cw jobs` を叩いても同じ場所へ
回収できます。

### 4. 止める

```bash
cw stop
```

見張り → セッション → **サーバへの問い合わせ** → トンネル の順に畳みます。
台帳から消えることとリモートが止まることは別なので、最後に現物を出します。
GPU の稼働時間で課金されるので、止まっていることを必ず確かめてください。

**`tunnel` を上げっぱなしにしない。** セッションが無い状態で常駐させると、
ProxyCommand の `colab ssh -s comfy` がその名前のランタイムを確保します(実測では
CPU ランタイムなのでコンピューティングユニットは減りませんが、意図しない確保です)。
`cw stop` はトンネルまで畳みます。

### 無人で1本流す

`cw run` は確保 → 構築 → 実行 → 停止 をまとめて流し、途中で何が起きても最後に必ず
セッションを止めます。`--` のあとは `client` コンテナの python にそのまま渡るので、
**ここだけはコンテナの中のパス**で書きます。

```bash
cw run --setup video --models ltx-2.5 --gpu L4 --max 60 -- \
  src/scripts/measure_video.py submit works/still.png --model ltx-2.5 --aspect 9x16
```

### 内側 (トラブル時)

セッションは生きているのに届かない、というときはトンネルだけを張り直します。
**確保し直さないでください。** 生きているランタイムを捨ててもう一度 GPU を掴むことになります。

```bash
cw tunnel restart     # up / stop / logs もあります
```

`cw` が呼んでいるのは既存のスクリプトそのものです。うまく動かないときは直接叩けます。

```bash
docker compose ps
docker compose logs --tail 50 tunnel
src/scripts/colab.sh sessions
src/scripts/colab.sh exec -s comfy -f src/scripts/colab_setup_status.py
src/scripts/colab_watch.sh --status
```

ホストに python を入れられない環境では、生成もコンテナで動きます。
`docker/Dockerfile.client` はそのために残してあります(ffmpeg 入りなので、mp4 以外を
仕上げたいときの逃げ道にもなります)。

```bash
docker compose run --rm client src/scripts/generate_image.py submit "..." --model z-image
docker compose run --rm client src/scripts/generate_video.py --first-frame works/still.png
```

## Claude Code から使う

`.claude/skills/` にスキルを2つ置いてあります(git 管理下・共有対象)。
`.claude/settings.json` は個人設定なので git 管理外です。

- **`colab-comfy`** — 動かす側。「画像を作って」「この静止画を動かして」と頼むと、
  状態の確認 → 確保 → 構築 → 生成 → 停止 の手順と、課金を止めるための鉄則を持っています。
- **`h3-prompt`** — MiniMax H3 に書く側。公式の記法(タスク選択、ショットとカメラの語彙、
  台詞・歌唱の `<d>`、参照ラベル、音の2フィールド)。H3 はこの形式で学習しているので、
  外すとカメラ指示やリップシンクが効かなくなります。

## 自前のコードから呼ぶ

宛先とキーの解決、瞬断のリトライ、落ちている理由の切り分けは `lib/colab_link.py` に
入っています。

```python
import sys, base64, json
sys.path.insert(0, "/path/to/comfy-wrapper/src")   # コンテナの中なら /app/src
from lib import colab_link

endpoint = colab_link.read_endpoint()      # コンテナ内なら tunnel:8000、ホストなら 127.0.0.1:8000
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

## 他のプロジェクトから使う

**このリポジトリは1台に1つだけ動かし、他のプロジェクトはコピーせずに HTTP で頼む。** `.colab/` の OAuth トークン・SSH 鍵・セッション台帳は1つしか持てず、
複製すると `Already-active SSH session (HTTP 429)` やアイドル刈り取りの取り合いになります。
呼ぶ側に `colab` / `tunnel` サービスを持たせないでください。

経路は2つあります。**手で頼むなら `cw`、コードから頼むなら HTTP** です。

```bash
# 呼ぶ側のプロジェクトで
uv tool install --editable /path/to/comfy-wrapper
cd /path/to/your-project
cw image "..." --out ./assets/hero.png     # 出力はこのプロジェクト、台帳は comfy-wrapper 側
```

`cw` は1台に1つのリポジトリを指すだけなので、複製は起きません。生成物は呼んだ場所に
書かれ、ジョブの台帳と鍵は comfy-wrapper 側に集まります。

コードから頼むときは共有ネットワークを1本張ると、宛先が `http://tunnel:8000` のまま
解決します。ホストにポートを晒さずに済み、呼ぶ側はコードも宛先の設定も変えなくて
済みます。呼ぶ側の compose に、このリポジトリと**同じ宣言**を置くだけです。

```yaml
# 呼ぶ側の docker-compose.yml
services:
  app:
    networks: [default, comfy]
networks:
  comfy:
    name: comfy-net
```

**`external: true` にしないでください。** external にすると、まだネットワークが無い環境で
compose ごと起動に失敗します。呼ぶ側は生成と関係ない処理まで動かせなくなり、こちらも
単体で立ち上がらなくなります。名前を固定した非 external なら、先に上がった方が作り、
あとから来た方がそれに乗ります。事前に `docker network create` を打つ必要もありません。

キーは**プロジェクトごとに発行**してください。`.colab/colab-api-key` を共有すると、
片方だけ失効させられなくなります。

```bash
cw key issue --name <プロジェクト名>   # 平文はこのときだけ出る
cw key list
cw key push                            # ハッシュをランタイムへ送る
cw key revoke --id <id>
```

出た `COLAB_API_KEY=...` を呼ぶ側の `.env` に置きます。**ランタイムへ渡るのは
SHA-256 のハッシュだけ**なので、発行と反映 (`cw key push`) は別の操作です。

呼ぶ側が知っておく必要があるのは3つだけです。

- 投入は 202 で返り、生成は裏で走る。**届いていれば投げ直さない**(二重生成は
  GPU 時間をそのまま捨てる)
- ジョブ台帳はサーバのメモリにしかない。ランタイムを止めると回収できない
- ランタイムの確保と停止は自前生成サーバ側の仕事。`/health` に届かなければ「いま動いていない」

実例は [music-video-creator2](https://github.com/loopsketch/music-video-creator2) です
(元はこのコードを内部に抱えていて、自前生成サーバとして切り出した側)。

## モデル

同じ表は `cw models` でも出ます(ランタイムが無くても出ます。載っているかどうかだけが
分かりません)。

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
| `ltx-2.5` (22B int8) | 約40GB | あり | 8〜50(既定24) | 同上 | t2v / i2v / **先頭・末尾** |

- **参照つき生成 (r2v) は `minimax-h3` だけ。** プロンプト側から `<Picture 1>` `<Video 1>`
  `<Audio 1>` で参照を指します。
- **L4 では `ltx-2.3-gguf` が一番速い。** MiniMax H3 の 5.6倍、Wan2.2 の 2.3倍で、しかも
  音声つき 25fps です。i2v をまとめて回すならこれが既定候補になります。
- **`ltx-2.3` の fp8 は L4 に載りません**(本体 29GB)。L4 では GGUF 版、fp8 は A100 向けです。
- **`ltx-2.5` は int8 のまま L4 に載ります**(本体 21.5GB)。480p も 720p も部分オフロード
  なしで回り、音声つき 24fps です。**最初と最後のフレームを置ける LTX はこれだけ**で、
  そのときだけ単パス・フル解像度になります(2段構えより時間はかかります)。
- **参照シート (IC-LoRA Ingredients) の 2.5 版はまだありません。** 参照シートを使うなら
  `ltx-2.3-ic` のままにしてください。
- `duration` はモデルごとの latent フレームグリッドに切り上げられます。実際の尺は
  レスポンスの `seconds` に入ります。

実測(L4、480p・約5秒・9x16、同じ静止画・同じ seed・同じプロンプト):

| model | 1本目(ロード込み) | 2本目(ロード済み) | 映像1秒あたり | 1本 |
|---|---|---|---|---|
| `minimax-h3` (fp8, 20step) | 471秒 | 400秒 | 77.4秒 | 2.02円 |
| `wan2.2` (4step 蒸留) | 213秒 | 165秒 | 32.6秒 | 0.83円 |
| `ltx-2.3-gguf` (Q4_K_M) | **127秒** | **72秒** | **14.0秒** | **0.36円** |
| `ltx-2.5` (22B int8) | 約201秒 (合算) | 105秒 | 20.8秒 | 0.53円 |

`ltx-2.5` の1本目だけは合算値です。ロードにかかる 96秒は 2秒の回で測ったもので、
5秒の cold は測っていません。同じセッションで測った他の条件は次のとおりです。

| 条件 | 時間 |
|---|---|
| 480p (512x832) / 2秒 / i2v | 148秒(ロード込み)、52秒(ロード済み) |
| 720p (704x1280) / 2秒 / i2v | 96秒 |
| 480p (512x832) / 2秒 / 先頭+末尾 | 134秒(単パス・フル解像度のため i2v より重い) |

生成中の VRAM は 21.4/23.0GB 使用で、`loaded partially` は一度も出ていません。
**L4 で 720p まで素で回ります。**

いずれも L4・ウェイト取得済みでの値です。手元の環境で測るなら
`scripts/measure_video.py` を使ってください(ロード込みとロード済みを分けて出します)。

## API

`/health` を除き、すべて `Authorization: Bearer <key>` が必要です。

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/health` | 認証不要。トンネル疎通と、モデルごとのウェイトの有無 |
| GET | `/v1/info` | GPU 名・VRAM・使用中のモデル |
| GET | `/v1/models` | どのモデルで何ができるか。呼ぶ側がモデル表を持たなくて済むようにする |
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
| `first_frame` / `last_frame` | — | base64 か data URI。`i2v` は `first_frame` 必須。`last_frame` は H3・`wan2.2`・`ltx-2.5` のみ(いずれも `first_frame` と一緒に) |
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
cw post ./clip.mp4 --size 4k-portrait --multiplier 2
cw jobs
```

使うモデルは合計 226MB で、どの構築にも既定で入ります(`--no-postprocess` で外せます)。
補間は尺を変えず fps が上がります。倍率は整数しか取れないので、生成側の fps を目標が
割り切れる値に寄せてください。効くのは VRAM ではなくシステム RAM で、しかも目標サイズでは
なく拡大モデルが吐く中間サイズ(入力 x 倍率)で決まります。`postprocess.py` は入力と目標の
比から x2 / x4 を選び、見積もりが 30GB を超えたら投入前に止めます。

入力の寸法・fps・フレーム数は `lib/mp4_probe.py` が mp4 のヘッダから直接読むので、
**仕上げのためだけに ffmpeg を入れる必要はありません。** mp4 以外 (webm / mkv) や
断片化された mp4 のときだけ ffprobe に回し、それも無ければ何が足りないかを名指しして
止まります(黙って既定値で代用すると、見積もりが入力と噛み合わないまま投入されます)。

## テスト

```bash
python3 -m unittest discover -s tests -t tests                      # ホストで
docker compose run --rm client -m unittest discover -s tests -t tests   # コンテナで
```

**両方で通ること**を確かめてください。`cw` はホストの python で動き、
`docker compose run --rm client` はコンテナの中で動くので、置き場の解決 (`/app` か
クローン先か) を取り違えると片方だけ落ちます。

一部だけ流すときは `-p` でファイルを選びます(`tests/` の外から `import _bootstrap`
する作りなので、`python3 tests/test_cli.py` の形では動きません)。

```bash
python3 -m unittest discover -s tests -t tests -p 'test_cli.py'        # cw の振り分け
python3 -m unittest discover -s tests -t tests -p 'test_mp4_probe.py'  # mp4 のヘッダ読み
python3 -m unittest discover -s tests -t tests -k Endpoint             # 名前で絞る
```

GPU も Colab のランタイムもネットワークも要りません。**課金が始まってからでないと
気づけない類の間違い**を、手元で落とすためのものです。

- キャンバス寸法と latent のフレームグリッド(この README の表がそのまま期待値)
- **ワークフローのグラフ整合性**(全モデル)。`[ノード, 番号]` の参照先が実在するか、
  保存ノードから辿れないノードが無いか、拡大段のノード ID が本体と衝突しないか。
  ここが崩れていると ComfyUI は 400 で弾くが、それが分かるのは GPU を確保したあと
- 宛先・キーの解決、障害の切り分け、リトライの方針(**POST は再送しない** = 二重生成しない)
- キーストア。保存されるのはハッシュだけで平文は残らないこと、失効が効くこと
- **ウェイト取得の事前チェック。** 空き容量が足りない回に1ファイルも取りに行かないこと、
  共用のエンコーダを二重に数えないこと、置き場が ComfyUI の見るフォルダに合っていること。
  40GB を取り終えてから足りないと分かると、そこまでの課金が丸損になる
- **止まった取得を殺して取り直す経路。** 子プロセスを実際に固めて確かめる。Xet の停止は
  例外を上げないので、try/except のテストでは再現にならない
- **OAuth の延長。** 無人のループから呼ばれるので、どんな失敗でも例外を投げないこと。
  再認証が要る状態と、こちらの都合で判定できない状態を混ぜないこと
- **`cw` の振り分け。** 何をどこへ渡すか、`cw run ... -- <作業>` の `--` を落とさないこと、
  運用系が `docker compose` を呼ぶ側に見せないこと、`cw models` がランタイム無しでも
  表を出せること
- **mp4 のヘッダ読み。** 音声トラックを映像と取り違えないこと、stts が複数に割れていても
  フレーム数を数え落とさないこと、断片化された mp4 を 0フレームと答えないこと。
  ここがずれると倍率も RAM も狂い、**それが分かるのは投入したあと**
- **仕上げの前提。** mp4 は外部コマンド無しで読めること、読めない形式で黙って既定値に
  逃げず名指しして止まること

## ディレクトリ構成

```
pyproject.toml         cw / comfy-wrapper のパッケージ定義(依存ゼロ)
docker-compose.yml     client / colab / tunnel の3サービス
docker/                各サービスのイメージ
.claude/skills/        Claude Code 用のスキル(colab-comfy: 運用 / h3-prompt: H3 の記法)
src/
  cli/                 cw の振り分け(comfy_wrapper として入る)
  server/              Colab 上で動く FastAPI + ComfyUI 橋渡し
  setup/               ウェイトの取得
  scripts/             手元と Colab 側の運用スクリプト
  lib/                 手元側の共有層(宛先・キー・リトライ・単価)
tests/                 単体テスト(標準の unittest。GPU もネットワークも不要)
works/                 生成物・測定結果・見張りの回収先(git 管理外)
.colab/                トークン・SSH 鍵・キーストア・ジョブ台帳(git 管理外)
```

主なモジュール(パスは `src/` からの相対):

| パス | 役割 |
|---|---|
| `cli/main.py` | `cw` の振り分け。生成系は `scripts/` の `main()` をその場で呼び、運用系は `*.sh` を実行する |
| `server/app.py` | FastAPI 本体。認証・ジョブ管理・ComfyUI への橋渡し |
| `server/{h3,wan,ltx,ltx25,image,post}_workflows.py` | 各モデルのワークフロー生成(ComfyUI API フォーマット) |
| `server/video_common.py` | 共通の寸法・尺の計算と出力段の拡大 |
| `server/comfy.py` | ComfyUI クライアント(投入・ポーリング・出力回収) |
| `server/auth.py` | キーの発行・保存・検証(平文は保存しない) |
| `setup/download_*.py` | ウェイトの取得 |
| `scripts/colab.sh` | Colab CLI のラッパ(常駐コンテナ経由で叩く) |
| `scripts/colab_run.sh` | 確保 → 構築 → 実行 → 停止 を無人で1本流す |
| `scripts/colab_watch.sh` | 見張り。keep-alive・進捗記録・自動停止と成果物の回収 |
| `scripts/generate_image.py` | 静止画の生成を投入して png を回収する |
| `scripts/generate_video.py` | 動画の生成を投入して mp4 を回収する(引数なしなら疎通テスト) |
| `scripts/postprocess.py` | 仕上げ(補間 + 拡大)の投入と回収 |
| `scripts/measure_video.py` | 生成時間の測定(ロード込み/ロード済みを分けて出す) |
| `lib/colab_link.py` | 宛先・キーの解決、リトライ、障害の切り分け |
| `lib/mp4_probe.py` | mp4 のヘッダから寸法・fps・フレーム数を読む(仕上げの見積もり用。ffmpeg 不要) |
| `lib/video_sizes.py` | `480p` / `720p` / `1080p` をモデルごとの生成キャンバスと出力寸法に落とす |

## 設定

標準の経路では設定するものはありません。宛先はコンテナの中と外で自動的に振り分けられ、
キーは `.colab/colab-api-key` から読みます。以下は別の構成にするときのための変数です。

| 変数 | 既定 | 場所 |
|---|---|---|
| `COMFY_WRAPPER_HOME` | `cw` から見たソースツリー | cw。リポジトリを移したときに指す |
| `COLAB_ENDPOINT` | コンテナ内 `http://tunnel:8000` / ホスト `http://127.0.0.1:8000` | client・cw。Colab 以外に立てたときに上書きする |
| `COLAB_SESSION` | `comfy` | tunnel。どのセッションへ通すか |
| `COLAB_AUTH_LOOP_MINUTES` | `30` | colab。OAuth 延長の間隔 |
| `TZ` | `Asia/Tokyo` | colab。見張りのログの時刻 |
| `COMFY_URL` | `http://127.0.0.1:8188` | server |
| `WRAPPER_KEYS_PATH` | ランタイム上のパス | server。キーストアの置き場 |
| `H3_*` / `LTX_*` / `LTX25_*` / `CW_*` | モデルごと | server。ウェイトのファイル名の上書き |

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
  止まっていることは、利用者自身が `cw sessions`(サーバへの問い合わせ)で確認してください。
- **モデルと生成物の利用も利用者の責任です。** 各モデルのライセンス・利用規約の確認と遵守、
  および生成物の取り扱いは利用者が行うものとし、それらに起因して問題が発生しても、
  作者は一切の責任を負いません。
- **上記は例示であり、これらに限りません。** 本ソフトウェアの使用または使用不能から生じる
  いかなる損害(意図しない課金、データの消失、認証情報の漏洩、外部サービスの規約違反・
  仕様変更・停止、第三者との紛争などを含みますがこれらに限りません)についても、
  作者は一切の責任を負いません。利用は自己責任で行ってください。

## ライセンス

MIT — [LICENSE](LICENSE) を参照してください。Copyright (c) 2026 LOOPSKETCH.

これが及ぶのはこのリポジトリのコードだけです。`src/server/` のワークフロー生成は
ComfyUI 公式テンプレート(MIT / Copyright (c) 2023-present Comfy Org)を展開したもので、
表示は [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) にあります。
ウェイトにはそれぞれの規約があり、
MiniMax H3 は MiniMax H3 Community License(USA/EU/UK/韓国向けには別途申請フォーム)、
Wan2.2 は Apache-2.0、LTX-2.3 / LTX-2.5 は LTX-2 Community License Agreement です。商用利用の前に
確認してください。

**LTX 系の HF リポジトリは gated です。** ウェイトを取得する前に、HF トークンのアカウントで
ライセンスへの同意(モデルページの "Agree and Access")を済ませてください。未同意だと
取得が 403 になり、**構築は止まらないまま**ウェイトの無いセッションが立ち上がります。
2.3 と 2.5 は別リポジトリなので、同意も別に必要です。

## 謝辞

- [MiniMaxAI/MiniMax-H3](https://huggingface.co/MiniMaxAI/MiniMax-H3) と
  [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3)(量子化ウェイト)
- [Wan-AI](https://huggingface.co/Wan-AI) /
  [Lightricks/LTX-2.3](https://huggingface.co/Lightricks/LTX-2.3) /
  [Lightricks/LTX-2.5](https://huggingface.co/Lightricks/LTX-2.5)
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) と
  [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF)
- [google-colab-cli](https://github.com/googlecolab/google-colab-cli)
