# つまずいたときは

実際に踏んだものだけを並べてある。失った時間はそのまま課金になるので、まず
「いま GPU を掴んでいるか」を確かめてから原因を追いたい。

```bash
cw status      # compose / セッション / 監視 / 疎通
cw sessions    # サーバに現物を問い合わせる
```

## 症状から引く

| 症状 | 見るところ |
|---|---|
| 生成が届かない | `cw status` の疎通。セッションが生きていればトンネルだけ張り直す |
| 401 が返る | キーストアを作り直した後。`cw key issue` して呼ぶ側の `.env` を差し替える |
| 静止画は通るのに動画が 503 | そのモデルのウェイトが載っていない。`/health` の `video_ready` |
| 400 `value_not_in_list` | 構築したモデルと `--model` がずれている |
| 生成中に CUDA out of memory | `megapixels` と `duration` を下げる。`upscale_model` を外す |
| `cw sessions` に `[?]` が残る | 管理から外れた孤児。`cw stop --orphans` |

## 確保したのに CPU ランタイムだった

`cw up --gpu L4` が成功しても、実際には CPU の VM が返ってくることがある。2026-08-12 には
気づかないまま 28GB のウェイトを CPU 機へ落としていた。確保の直後に `cw sessions` で
Hardware と Variant を見る。

## gated リポジトリで 403、しかも構築が止まらない

`Lightricks/LTX-2.5` は gated で、HF トークンのアカウントがライセンスに未同意だと5ファイル
すべて 403 になる。

```
Cannot access gated repo for url https://huggingface.co/Lightricks/LTX-2.5/resolve/main/...
Access to model Lightricks/LTX-2.5 is restricted and you are not in the authorized list.
```

厄介なのは、`download_video_models.py` の `fetch()` が失敗を `[warn]` にして先へ進むこと。
構築は「完了しました」と言って終わり、ウェイトが1つもないままサーバが立ち上がる。生成の
直前まで気づけず、L4 を10分ほど掴んだ (2026-08-17、約3円)。

構築の後は `GET /health` の `video_ready` を見れば一発で分かる。同意はモデルページの
"Agree and Access" で、gated の種別は `auto` なので審査待ちは無い。2.3 と 2.5 は別
リポジトリなので、同意も別に要る。

## 構築中に GitHub へ繋がらない

GGUF のウェイトは ComfyUI 本体だけでは読めず、構築が `custom_nodes` に ComfyUI-GGUF を
入れる。この clone が 135秒でタイムアウトした
(`Failed to connect to github.com port 443`、2026-08-12)。引いたランタイムの回線の問題で、
取り直した2回目は問題なく通った。再試行以外に手はない。

このときは clone が固まっている間ディスク使用量が 48.4GB から動かなくなり、8分の無進捗で
監視が自動停止させた。上限60分まで回れば18円になっていたところが3円で済んでいる。

## 管理から外れた孤児が残る

`cw sessions` に `[?]` で始まる名前なしのセッションが並ぶことがある。管理から外れたもので、
CPU ランタイムなら CU は減らないが、`cw stop -s <名前>` では引けない。

```bash
cw stop --orphans                                    # 名前なしのものを解放する
docker compose exec -T colab python \
  /app/src/scripts/colab_unassign.py --dry-run       # 何が消えるかだけ見る
```

名前つきも含めてすべて外す `--all` もあるが、走っている GPU セッションまで巻き添えに
なる。生成中に使わないこと。放っておくと GPU の場合は課金が続く。

## トンネルだけが落ちた

セッションは生きているのに何も届かないとき、確保し直してはいけない。生きている
ランタイムを捨てて GPU を取り直すことになる。

```bash
cw tunnel restart
docker compose logs --tail 50 tunnel
```

逆に、セッションが無い状態でトンネルを残すと、ProxyCommand がその名前でランタイムを
確保してしまう。`cw stop` はトンネルも畳むので、止めるときは `cw stop` を通す。

## 構築したモデルと `--model` がずれた

静止画で `--models qwen-image` を入れながら既定の `z-image` のまま投げ、ComfyUI が
`value_not_in_list` で弾いた (2026-08-12)。セッションは残るので `--model qwen-image` で
投げ直せば済むが、代償は GPU を掴んだままの往復1分から2分。

`generate_image.py` は `value_not_in_list` を検知したらヒントを出すようにしてある。
スキル `colab-comfy` にも鉄則として入れた。

## ComfyUI の起動待ちがタイムアウトする

`/object_info/<ノード名>` が 404 のままなら ComfyUI が古い。ランタイム上で
`git -C /content/ComfyUI pull` してから起動し直す。

ウェイトが無くてもワークフローの妥当性は確かめられる。`src/scripts/probe_ltx25_nodes.py` は
組み立てるノード・入力名・選択肢を ComfyUI の `/object_info` と突き合わせる。403 で空振り
したセッションでも、グラフの検証だけは済ませられた(27ノードすべて一致)。投入して実行時
エラーを待つより早く、GPU 時間も食わない。VRAM のやりくりは `src/scripts/probe_vram_log.py`
で `loaded partially` の有無を見る。

## 放置するとランタイムが止まる

Colab はアイドルでランタイムを刈る(おおむね90分)。サーバは `subprocess` で動くので起動
そのものはすぐ終わり、Colab からは何も実行していないノートブックに見えてしまう。`colab`
サービスを動かしたままにして、keep-alive デーモンを生かしておくこと。

`docker compose run --rm` でコマンドを流すと、返った瞬間にコンテナごとデーモンが消える。
`docker compose up -d colab` で常駐させる。
