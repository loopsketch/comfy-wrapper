# 基本モード(T2VA / I2VA / FL2VA / L2VA)

出典: MiniMax-H3 `VIDEO_PROMPT_WRITING_GUIDE_base_en.md`。
共通記法(ショット・カメラ・話者・音)は [notation.md](notation.md)。

## タスクの選び方

| タスク | 条件 | `POST /v1/generate` |
|---|---|---|
| I2VA | 先頭フレームだけある | `task: "i2v"` + `first_frame` |
| T2VA | 参照画像なし | `task: "t2v"` |
| FL2VA | 先頭と末尾のフレームがある | `task: "i2v"` + `first_frame` + `last_frame` |
| L2VA | 末尾フレームだけある | 経路なし(`first_frame` 無しの `last_frame` は受けない) |

参照シート(キャラ・小物)を使うカットは基本モードではなく Ref2VA。
[ref2va.md](ref2va.md) を見る。

## プロンプトの構造

### 第1部: 整合指示

**T2VA は整合指示を持たない**。三つのコアフィールドから直接始める。

**I2VA**:

```text
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.
```

**FL2VA**:

```text
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot N) aligns with the S.SS-second mark of the target video.
```

**L2VA**:

```text
How the reference pictures align with the target video — <Picture 1> (from [Shot N]) aligns with the S.SS-second mark of the target video.
```

`N` は実際の最終ショットの番号、`S.SS` は**実効尺を小数第2位まで**書いた値。
整合指示は最終プロンプトの1行目に置き、空行を1つ挟んでコアフィールドを続ける。

### 第2部: 三つのコアフィールド

```text
integrated_multimodal_description: [Shot 1] ...

overall_soundscape: ...

non_diegetic_music: ...
```

- **integrated_multimodal_description**: 時間軸に沿って、映像・動作・ショット・
  話者・台詞・歌唱・劇中音を書く。すべての記述が「見えるもの」か「聞こえるもの」に
  対応していること。
- **overall_soundscape**: 全編の環境音・動作音・非言語の人声。
- **non_diegetic_music**: 登場人物に聞こえない、観客だけに聞こえる音楽。

## キーフレームの扱い

### I2VA — 画から始めて前へ展開する

`<Picture 1>` は 0.00 秒の実際の先頭フレームで、`[Shot 1]` に属する。
まず画の中のスタイル・被写体・構図・場面のアンカーを確定させ、その次に
起きる動作を書く。人物の同一性・服・色・重要な小物・空間関係は変えない。

推奨構造: **先頭フレームのアンカー → 動作の開始 → 連続した展開 → 結果や反応**

### FL2VA — 先頭と末尾のあいだの経路を書く

Picture 1 が開始、Picture 2 が終了。被写体がどう動き、姿勢がどう変わり、
物がどう扱われ、構図がどう変化し、場面や光がどう移るかに集中する。

FL2VA は**単一ショットを基本にする**(モデルが先頭から末尾へ連続補間できる)。
複数ショットは明示的に指定されたときだけ。末尾フレームは動画の終わりの
最終 `[Shot N]` で到達すること。

推奨構造: **先頭の状態 → 観測できる中間変化 → 差分が徐々に縮む → 末尾の状態**

### L2VA — 開始を推測して画に着地する

`<Picture 1>` は最終フレームで、最後の `[Shot N]` に属する(Shot 1 ではない)。
意図と最終フレームから妥当な過去の状態を推測し、人物・物・カメラ・場面が
参照画像へどう近づいていくかを書く。

推奨構造: **妥当な先行状態 → 明示的な動作と遷移の経路 → 最終ショットでの
漸進的な収束 → 末尾フレームへの着地**

## 例

### T2VA

```text
integrated_multimodal_description: [Shot 1] Live-action, cinematic, a medium-wide shot frames a baker opening the shutters of a small street bakery before sunrise. The camera pushes in with small amplitude at slow speed as the middle-aged baker with a calm, slightly raspy voice (S1) places a fresh loaf on the wooden counter and says: <d>[English] First batch of the morning.</d> [Shot 2] At 00:05.000, the camera cuts to a close-up of steam rising from the sliced bread while the baker's final words carry over from the previous shot.

overall_soundscape: Wooden shutters scrape open over a quiet street as trays clink softly inside the bakery. The doorbell rings once, followed by light footsteps and the crisp sound of bread being sliced.

non_diegetic_music: A soft acoustic-guitar pattern at a moderate tempo, joined by sparse upright-bass notes and a gentle fade at the end.
```

### I2VA

```text
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] Live-action, cinematic, the young woman shown in <Picture 1> remains beside the rain-covered train window, preserving her appearance, clothing, seat position, and the carriage layout. The camera trucks right with small amplitude at slow speed as she lifts her gaze from the folded letter toward the passing city lights. Her reflection moves across the glass while the quiet, breathy young woman (S1) says: <d>[English] I get off at the next station.</d> She folds the letter along its existing crease.

overall_soundscape: The train wheels produce a steady metallic rhythm beneath a low ventilation hum. Rain ticks against the window while paper rustles softly in her hands.

non_diegetic_music: Sustained cello notes at a slow tempo with widely spaced piano tones, gradually decreasing in volume.
```

### FL2VA(8秒・単一ショット)

二枚の画は開始と終了を固定する。本文で静止画の説明を二度繰り返すのではなく、
**両者をつなぐ運動経路**を書く。

```text
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 8.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] Live-action, cinematic, a rain-soaked cyclist begins in the position and framing established by Picture 1, holding a closed black umbrella beside a silver bicycle. The camera pulls out with small amplitude at slow speed as she releases the bicycle handle, raises the umbrella above her shoulder, and presses the runner upward until the canopy opens. Water rolls from the expanding fabric while she steps beneath it, rotates the handle into the final angle, and settles into the pose, spacing, and composition established by Picture 2 at the end of the shot.

overall_soundscape: Rain falls steadily on the pavement, followed by the metallic click of the umbrella runner and the soft snap of the canopy opening. Water drips from the bicycle frame as distant traffic passes.

non_diegetic_music: N/A
```

### L2VA(6秒・単一ショット)

```text
How the reference pictures align with the target video — <Picture 1> (from [Shot 1]) aligns with the 6.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] Live-action, cinematic, a close shot begins with an intact drinking glass near the edge of a dark wooden table, while the same hand and sleeve visible in <Picture 1> approach from the right. The camera pushes in with small amplitude at slow speed as the fingertips strike the rim. The glass tips, falls, and hits the floor with a sharp impact; cracks spread through it as fragments slide outward. Toward the end, the moving pieces lose momentum and settle into the exact broken arrangement, hand position, camera angle, lighting, and final composition established by <Picture 1>.

overall_soundscape: Fingertips tap the glass before it scrapes across the tabletop, falls, and breaks with a sharp crash. Small fragments scatter and gradually stop sliding across the floor.

non_diegetic_music: A low electronic pulse at a slow tempo, ending immediately after the glass breaks.
```
