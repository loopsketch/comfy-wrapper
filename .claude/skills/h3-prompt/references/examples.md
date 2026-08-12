# 書き分けの例

日本語の意図から H3 のプロンプトへ落とした例。

## 例1: 歌唱カット(I2VA)

意図: 「キッチンに立つ彼女が、カメラに身を乗り出してウインクしながら歌う。
カメラは据え置き。先頭フレームは用意済み」

欲しい尺が 4.6 秒でも、H3 の下限は 124 フレーム = **5.167 秒**。
歌詞は原文のまま `<d>` に入れる。

```text
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] 2D-animated, cel anime style, the young woman shown in <Picture 1> stays at the same kitchen counter, preserving her appearance, her mustard apron, the counter height and the bright morning kitchen behind her. The camera holds a static shot at counter level as she leans further down toward the lens, close enough that her face fills the upper half of the frame. The bright, slightly breathy young woman (S1) sings toward the camera, <d>[Japanese] きみのとなりで</d>, her mouth opening and closing on every syllable, and she closes one eye in a quick wink on the last syllable. Sunlight moves across her cheek as she straightens back up.

overall_soundscape: A gentle morning room tone with the faint sizzle of a pan behind her. Fabric rustles as she leans down toward the camera.

non_diegetic_music: A bright acoustic guitar pattern at a moderate tempo with a light shaker on every beat, holding a steady level throughout.
```

要点:

- 先頭フレームで決まっている見た目(服・場所・光)は「保持する」と書くだけにし、
  描き直さない。書くのは動きと、フレームの中でどう変化するか。
- lipsync カットでは歌詞を `<d>[Japanese] ...</d>` にそのまま入れ、
  「音節ごとに口が開閉する」ことを本文に書く。歌詞は翻訳しない。
- 曲は本人が歌っているので**劇中音**。`non_diegetic_music` にはその区間の
  伴奏の実態(編成・テンポ・リズム)を書き、「切ない」「エモい」といった
  ムード語は使わない。

## 例2: 参照シートつきカット(Ref2VA)

`ref_images` に渡した配列の順が `<Picture N>` の番号になる。ここでは
キャラシート = `<Picture 1>`、構図の基準にする1枚 = `<Picture 2>` の2枚を渡す。

シートはキャラ定義にしか使わないので**独立行にせず** `<Subject 1>` の中で引用し、
構図の基準だけを独立した `<Picture 2>` にする。

```text
subject_definitions:
<Subject 1> is the young woman in <Picture 1>, with a short black bob, a mustard canvas apron over a white long-sleeved shirt, and round silver earrings.
<Picture 2> is the composition anchor for [Shot 1], showing her at kitchen-counter level with a frying pan in her right hand.

summary:
[reference generation + keyframe completion] The target video shows <Subject 1> flipping a pancake in a sunlit kitchen, framed from counter level as established by <Picture 2>, in a single continuous shot.

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - her short black bob, mustard apron, white shirt and round silver earrings are retained.
<Picture 2> ([Shot 1] composition anchor): fully_preserved - the counter-level camera height, her placement in the right half of the frame, the morning light direction and the overall exposure are retained for the whole clip.

detailed_description:
The target video is in a 2D-animated cel anime style with warm morning light and a soft, slightly desaturated palette.
[Shot 1] The shot begins from the framing established by <Picture 2>: an extreme low angle at kitchen-counter level, looking up at <Subject 1>, the young woman with a short black bob and a mustard canvas apron, who holds a frying pan by its long black handle in her right hand. The camera holds a static shot as she swings the pan upward and a pancake lifts clear of it, turning once in the air above her. She tracks it with her eyes, steps half a pace to her left, and catches it back in the pan; her shoulders drop and she laughs with her mouth open. Sunlight from the window at frame left keeps the same direction and intensity from the first frame to the last, with no fade in or fade out.

overall_soundscape:
A steady sizzle from the pan continues under a quiet morning room tone, with a soft slap as the pancake lands back in the pan and a short bright laugh.

non_diegetic_music:
A bright acoustic guitar pattern at a moderate tempo with a light shaker, steady in level.
```

要点:

- **構図の基準にする1枚は必ず独立した `<Picture N>`** にして、構図・カメラ高さ・
  時刻・光の向き・露出を保持対象として明記する。ここを書かないと露出が拘束されず、
  昼のカットが暗転して始まることがある。
- キャラや小物のシートは `<Subject N>` の定義の中で引用する。独立行にしない。
- `retention_analysis` は参照ごとに1行。still の行に「クリップ全体で
  この明るさを保ち、フェードイン・フェードアウトしない」を入れておく。

## 例3: 1本の生成に複数カットを載せる

H3 は1回の生成の中でカットを切れる。連続する数カットが同じ場所・同じ流れなら、
1本の生成にまとめてプロンプト内で `[Shot 2]` 以降を切る方が、生成回数と
待ち時間を減らせる。

```text
integrated_multimodal_description: [Shot 1] 2D-animated, cel anime style, an extreme low angle from the kitchen counter looks up at the young woman in a mustard apron as she pours batter into a hot pan. The camera holds a static shot while the batter spreads and bubbles rise. [Shot 2] At 00:02.500, the shot cuts to a close-up of the pancake turning in the air above the pan, the same morning light coming from frame left. [Shot 3] At 00:04.000, the shot cuts back to the counter-level angle as she catches the pancake and laughs.

overall_soundscape: ...

non_diegetic_music: ...
```

タイムスタンプは**生成尺の内側**で厳密に増加させる。上の例なら生成尺は
5.167 秒以上が必要。カット時刻はクリップの先頭を 0 とした相対秒で書く。
