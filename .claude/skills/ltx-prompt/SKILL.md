---
name: ltx-prompt
description: LTX-2.5 のプロンプトを書く。「LTX で生成したい」「1本の生成で複数カットにしたい (多ショット)」「最初と最後のフレームを指定したい」「喋らせたい・環境音を指定したい」ときに使う。散文での書き方、カットのつなぎ方、尺のグリッド、効かない書き方まで含む。
---

# LTX-2.5 のプロンプトを書く

LTX は散文で読む。H3 のような形式フィールドも、重み記法も、品質語の効き目も無い。
「カメラマンがそのまま撮れる段落」を書くのが唯一の作法になっている。

生成そのものの回し方 (ランタイムの確保・停止・課金) は `colab-comfy` スキルが持っている。
プロンプトを詰めるのはここで終わらせる。書き直しは無料、生成のやり直しは GPU 時間を使う。

## 1. 素材からタスクを決める

| 渡す素材 | 投げ方 | 中の構成 |
|---|---|---|
| なし | `cw video --prompt "..."` | 2段構え (半解像度 → x2 で仕上げ) |
| 先頭フレーム | `cw video ./first.png` | 同上 |
| 先頭 + 末尾フレーム | `cw video ./first.png --last-frame ./last.png` | 単パス・フル解像度 |

- 末尾フレームだけは渡せない
  - 終点は始点があって初めて決まる
- 参照つき生成 (r2v) は LTX に無い
  - キャラを固定したいなら H3 の Ref2VA か、2.3 の参照シート (`ltx-2.3-ic`)

## 2. 尺を確定する

8k+1 フレームのグリッドに切り上がる。既定 24fps での代表値は次のとおりで、
`--duration` に何を渡してもこのどれかになる。

| フレーム | 秒 | | フレーム | 秒 |
|---|---|---|---|---|
| 97 | 4.04 | | 241 | 10.04 |
| 121 | 5.04 | | 289 | 12.04 |
| 145 | 6.04 | | 361 | 15.04 |
| 193 | 8.04 | | 401 | 16.71 (上限) |

fps は 8〜50 で変えられる (LTX 系だけ)。変えるとグリッドの秒数も動く。

## 3. 1ショットを書く

現在形のひと続きの段落で、画と動きの6つを埋める。連続した1カットなら 4〜8文が目安。

まず画を決める。

- ショット
  - 実在する撮影用語で大きさまで書く (`low-angle medium shot`)
- 場面
  - 光の向き、色、質感、空気
- 人物
  - 年齢、髪、服、特徴。感情は「悲しそう」ではなく体の所作で書く

そのうえで動きと音を足す。

- 動作
  - 始まりから終わりまでを順に。1文1動作
- カメラ
  - 動きと、動いたあとに何が見えるかまで
- 音
  - 環境音、音楽、台詞。台詞は引用符に入れ、言語や訛りを添える
  - 要らないものも名指しする。黙っていると勝手に音楽が乗るので、要らなければ
    `no music` と書く

i2v では構図を描き直さない。画は先頭フレームが決めているので、動きとカメラと音だけ書く。

## 4. 多ショット (2.5 から)

1回の生成で複数カットを出せる。箇条書きや「Shot 1:」ではなく、時系列の散文でつなぐ。
カットごとに4つを言い直す。

1. つなぎを言葉で名指す
   - `A hard cut transitions to...` / `A match cut jumps to...`
2. 画角を置き直す
   - 新しいショットの大きさをもう一度言う
3. 被写体を見た目で言い直す
   - 「彼女」ではなく「黄色いレインコートの女性」。言い直さないと別人になる
4. 音が続くのか変わるのかを書く
   - `the synth score continues across the cut`

2〜4カットまで。それ以上を1回に詰めると、1カットあたりの尺が足りなくなってどれも
中途半端になる。カットごとに役割を1つ持たせる (全景 → 寄り → 反応、など)。

## 5. 効かない書き方

- タグの羅列
  - `8k, masterpiece, cinematic` のような列は読まれない。文にする
- 重み記法・括弧
  - `(word:1.3)` に相当する仕組みが無い
- 光源の混在
  - 1カットに1つの光源で書く
- 画面内の長文テキスト
  - 綴りは保証されない。短く保つか、後から載せる

## 6. ネガティブ

既定は `pc game, console game, video game, cartoon, childish, ugly`。

- アニメ調や絵画調を狙うときは外すこと
  - 既定のままだと打ち消し合う
  - 差し替えは `cw video --negative "..."`、空にするなら `--negative ""`

## 例

単一ショット:

> A slow push-in on a woman in her forties, gray-streaked hair tied back, wearing a worn
> canvas apron, standing at a steel workbench in a narrow ceramics studio. Late-afternoon
> light comes through a single dusty window on the left. She turns a bowl on the wheel,
> thumbs steady against the clay. Ambient sound: the low hum of the wheel and wet clay,
> no music.

多ショット:

> A cinematic car commercial in three shots. A wide aerial establishing shot frames a
> matte-grey sports car carving along a mountain road at dawn, low engine hum and wind in
> the air. A hard cut transitions to a tight interior close-up of the driver's gloved hands
> on the stitched wheel, the engine note continuing across the cut. A final match cut jumps
> to a low three-quarter shot of the same matte-grey car cresting the ridge into the rising
> sun, the engine swelling.

## 投げる

書けたら本文をファイルに置いてから投げる。同じプロンプトで振り直せるようにしておくと、
比べるのが楽になる。

```bash
cw video ./first.png --model ltx-2.5 --prompt "$(cat ./prompts/cut01.txt)" \
  --aspect 9x16 --duration 5 --out ./clip.mp4
```

- `--model ltx-2.5` は毎回明示する
  - そのセッションで構築したモデルと一致していないと 400 で弾かれる
- `--duration` は上のグリッドに切り上がる
  - 何を渡しても表のどれかになる
- 生成は課金対象
  - 実行の前にユーザーの承認を取る

## 出典

- [LTX-2.5 Prompt Guide (Lightricks)](https://ltx.io/blog/ltx-2-5-prompt-guide)
- [LTX-2.5 ComfyUI テンプレート](https://docs.comfy.org/tutorials/video/ltx/ltx-2-5)
