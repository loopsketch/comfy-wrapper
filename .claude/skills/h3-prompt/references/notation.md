# H3 共通記法(全タスク共通)

T2VA / I2VA / FL2VA / L2VA / Ref2VA のすべてで共通する書式。
出典: MiniMax-H3 `VIDEO_PROMPT_WRITING_GUIDE_base_en.md` の 4章。

本文は英語で書く。原文の言語を保つのは `<d>` の中の台詞・歌詞と、
画面に実際に写っている文字だけ。

## ショットとカット

冒頭のショットにタイムスタンプは付けない。2本目以降は通し番号と、
尺の内側で厳密に増加するカット時刻を頭に置く。

```text
[Shot 1] Live-action, cinematic, a medium-wide shot frames...
[Shot 2] At 00:03.500, the camera cuts to...
```

カットの言い回し(通常カット):

- `the camera cuts to`
- `the shot cuts to`
- `the shot transitions to`
- `the shot changes to`
- `the shot switches to`

ユーザーが明示的に求めたときだけ cross-dissolve / fade / wipe を使う。

カットは被写体・空間・状態・視点・時間のいずれかについて**新しい情報**を
持ち込むこと。距離やわずかな角度が変わるだけならカットを切らず、
カメラワークで処理する。

`[Shot 1]` の冒頭で全体のスタイルと初期構図を宣言する。よく使う語:
`Cinematic` / `live-action` / `2D-animated` / `3D CG` / `claymation` /
`watercolor` / `vintage film`。参照画像があるタスクではスタイルは参照画像から
導く(勝手に別のスタイル語を足さない)。

## カメラワーク(種別 + 振幅 + 速度)

| 次元 | 表現 | 意味 |
|---|---|---|
| 種別 | `Zoom In / Zoom Out` | カメラは動かず焦点距離が変わる |
| 種別 | `Push In / Pull Out` | カメラ本体が前進 / 後退する |
| 種別 | `Pan Left / Pan Right` | 位置はそのままでレンズが水平に振れる |
| 種別 | `Truck Left / Truck Right` | カメラが水平に平行移動する |
| 種別 | `Tilt Up / Tilt Down` | 位置はそのままでレンズが垂直に振れる |
| 種別 | `Pedestal Up / Pedestal Down` | カメラ全体が上昇 / 下降する |
| 種別 | `Arc Shot` | 被写体の周りを弧を描いて回る |
| 種別 | `Tracking Shot` | 動く被写体を追う |
| 種別 | `Static Shot` | 位置もレンズも動かない |
| 種別 | `Shake Slightly / Shake Strongly` | 弱い / 強い手ぶれ |
| 種別 | `POV` | 被写体の主観視点 |
| 種別 | `Roll Clockwise / Roll Counterclockwise` | レンズ軸まわりの回転 |
| 振幅 | `with small amplitude` | 構図変化が小さい |
| 振幅 | `with large amplitude` | 構図変化が大きい |
| 速度 | `at slow speed` | ゆっくり動く |
| 速度 | `at fast speed` | 速く動く |

振幅と速度は意味があるときだけ足す。中程度の振幅・通常速度は書かない。
文末にラベルを積むのではなく、ショットの中の自然な英文として書く。

```text
The camera pushes in with small amplitude at slow speed toward the folded letter in her hands.
The camera pans right with large amplitude at fast speed, revealing the open doorway.
The camera holds a static shot as the runner exits the frame.
```

## 話者・台詞・歌唱

声を出す被写体には `(S1)` `(S2)` のような固定IDを与える。番号済みの話者が
同時に発話・歌唱するときは `(S1,S2)` のような複合IDにする。IDはショットを
またいでも変えない。一度も発声しない人物にはIDを与えない。

話者の初出では、視覚・聴覚の文脈から同一性が立つだけの情報を書く
(人物の種別、年齢、性別、画面内か画面外か、声の高さ、音色、話速、訛り)。

**話者の識別句・ID・動作・話し方は `<d>` の外**に書く。**`<d>` の中には
言語タグと実際の発話内容だけ**を入れ、語も句読点も原文のまま維持する
(翻訳も書き換えもしない)。

```text
The young woman with a quiet, breathy voice (S1) says: <d>[English] I get off at the next station.</d>
The two children (S1,S2) shout together, <d>[English] Wait for us!</d>
```

画面外のモノローグには `says in an off-screen voiceover` という句を厳密に使い、
`<d>` ブロックの直後に、画面内の当人の口が閉じたままであることを書く。

```text
The man (S1) says in an off-screen voiceover: <d>[English] I still remember that road.</d> while his lips remain completely closed.
```

同じ台詞・歌詞がカットをまたぐときは、両側の接続点に `<scenetrans>` を置き、
音がカットを越えて続くことを明示する。発話が動画の終端で切れるときは
`<cutoff>` を使う。継続の言い回し:

- `continues seamlessly across the cut`
- `continues uninterrupted into the next shot`
- `carries over from the previous shot`
- `remains audible across the transition`

## 画面内テキスト

看板・横断幕・ラベル・字幕・ネオンなど、実際に画面に写る文字は英語の
ダブルクォートで囲む。原文と句読点をそのまま保ち、翻訳しない。

```text
A red neon sign reading "営業中" glows above the doorway.
```

## overall_soundscape

英語1〜4文の連続した1段落。動画全体を通した環境音・動作音・非言語の人の声
(風、雨、交通、足音、衣擦れ、衝撃、呼吸、笑い、息切れ)をまとめる。
台詞・歌唱・劇中の音楽は多モーダル記述側の担当なので、ここで繰り返さない。
`N/A` はユーザーが全編無音を明示的に求めたときだけ。

```text
overall_soundscape: Steady rain taps against the café windows while low room ambience continues underneath. The entrance bell rings once, followed by wet footsteps and the soft scrape of a chair.
```

## non_diegetic_music

英語1〜3文。登場人物には聞こえず観客にだけ聞こえる音楽を書く。
楽器編成・速度・リズム・強弱の変化に集中し、抽象的なムード語や
「この曲は何を象徴するか」といった機能の説明は書かない。
登場人物に聞こえる歌唱・楽器・ラジオ・テレビ・電話の音楽は劇中音なので
多モーダル記述側に書く。劇伴が無ければ `N/A`。

```text
non_diegetic_music: Sparse piano notes at a slow tempo, joined by sustained low strings that gradually increase in volume before fading out.
```
