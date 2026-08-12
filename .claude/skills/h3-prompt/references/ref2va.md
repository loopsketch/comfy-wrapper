# 参照モード(Ref2VA / full-reference)

出典: MiniMax-H3 `VIDEO_PROMPT_WRITING_GUIDE_ref_en.md`。
ショット・カメラ・話者・台詞・通常音の書式は基本モードと共通
([notation.md](notation.md))。ここは参照ラベル・分析セクション・
基本モードとの差分だけを扱う。

API では `task: "r2v"`(`minimax-h3` のみ)。`ref_images` / `ref_videos` /
`ref_audios` に渡した順が `<Picture N>` / `<Video N>` / `<Audio N>` の番号になる。

## 全体構造

出力は次の6セクションをこの順で並べる。

| セクション | 役割 |
|---|---|
| `subject_definitions` | 参照内容とラベルの定義 |
| `summary` | タスク種別・目標動画・主要な参照関係の要約 |
| `retention_analysis` | 参照内容がどう保存・転写・再利用されるか |
| `detailed_description` | 再生順の映像・動作・ショット・音・台詞 |
| `overall_soundscape` | 環境音と物理音の要約 |
| `non_diegetic_music` | 観客だけに聞こえる音楽 |

`detailed_description` は**できるだけ詳細に**書く。各ショットについて、現在の構図、
被写体の外見と位置、環境と照明、動作と状態変化、カメラワーク、いま鳴っている音、
参照内容が実際に現れる/効き始める箇所を明示する。あらすじの要約や
参照関係の列挙に縮めない。

## 参照ラベル(`subject_definitions`)

| ラベル | 意味 |
|---|---|
| `<Subject N>` | 参照素材から抽象化した、目標動画で再利用・改変できる可視内容 |
| `<Picture N>` | 具体的な目標フレーム、またはショット設計のアンカーとして使う参照画像 |
| `<Video N>` | 編集元・継続の起点・全体の時間構造を与える参照動画 |
| `<Audio N>` | 複製または参照される音声信号 |

一度割り当てたラベルの意味は、6セクションすべてを通して変えない。

`subject_definitions` は後で個別に追跡する必要がある参照内容を1行ずつ定義し、
そのラベルが何を指すか、参照上の役割、追うべき主要な特徴を書く。出所を
明示すべきときは元の素材名も書く。`<Picture N>` や `<Video N>` が
「別の参照項目の出所」を示すだけで単独では使われないなら、独立した行を作らず、
その項目の定義の中で引用する。

### `<Subject N>`

再利用できる可視内容に使う。人物・動物・物、場面・背景・環境、衣装・小物・
インターフェース・視覚効果、スタイル・動作・表情・ポーズ。
**素材ファイルそのものではなく、目標動画で実際に使う内容の単位**を指す。
一つの subject が複数素材から定義されることも、一つの素材が複数の subject を
提供することもある。

```text
<Subject 1> is the young woman in <Picture 1>, with long dark hair, a blue cardigan, and a thin silver necklace.
<Subject 1> is the woman whose appearance comes from <Picture 1> and whose walking motion comes from <Video 1>.
```

### `<Picture N>`

参照画像そのものが、ショットの先頭フレーム・キーフレーム・末尾フレーム・
編集済みキーフレーム・構図アンカーとして働くときだけ独立行にする。

```text
<Picture 2> is the first frame of [Shot 1], showing a woman seated beside a café window.
<Picture 3> is a storyboard reference for [Shot 1] and [Shot 2], defining their viewpoint, subject placement, and shot order.
```

**画像がキャラクター・場面・衣装・スタイルの定義にしか使われないなら、独立した
picture 行を作らない**。対応する `<Subject N>` の定義の中で出所として引用する。
キャラクターシートはこちらに当たる(独立行にするのは、構図の基準にする
具体フレームだけ)。

### `<Video N>`

動画全体に関わる関係にだけ使う。元動画の編集、元動画の末尾からの継続、
元動画のカメラワーク・カット・リズム・時間構造の参照。

```text
<Video 1> is the source video for the target video edit.
```

参照動画の中の人・物・場面・動作・効果を可視内容として再利用するなら、それは
`<Subject N>` の担当。`<Video N>` は素材や構造の出所を示すだけで、
subject ラベルの代わりにはならない。

### `<Audio N>`

単独の音声素材、または参照動画の同期音声トラック。用途は、信号の全部/一部の複製、
BGM のスタイル参照、話者の音色・話し方の参照、元音声の台詞・歌詞・効果音の利用、
拍・リズム・音の連続性の参照。

`<Audio N>` が目標側の話者に対応するときは、その話者のグローバルIDを再利用する。
subject に対応するなら `<Subject N> (Sx)`、しないなら安定した声の説明 + `(Sx)`。
IDは目標動画の発声順から決まるもので、音声定義の側で新規に割り振らない。

```text
<Audio 1> is the voice-timbre reference for <Subject 1> (S1).
```

`<Video N>` と `<Audio N>` の番号は独立に振る。同じ参照動画が `<Video 1>` と
`<Audio 2>` になることがあり、番号違いは別素材を意味しない。
ファイルに音が入っているというだけでは `<Audio N>` を作らない。

## `summary`

英語1段落。角括弧のタスク種別が先頭に付く。

| タスク種別 | 使うとき |
|---|---|
| `keyframe completion` | 画像が先頭フレーム・キーフレーム・末尾フレーム等、具体的なフレームのアンカーになる |
| `reference generation` | 画像・動画・音声がキャラ・場面・スタイル・動作・カメラ・絵コンテ等の生成指針を与える(具体フレームでも編集元でもない) |
| `video editing` | 既存の元動画を直接改変する |
| `video continuation` | 既存の元動画から継続・延長・再開・遷移する |
| `audio reuse` | 同じ音声信号を全部または一部再利用する |
| `audio reference` | 信号は複製せず、音楽スタイル・音色・台詞や歌詞の内容・効果音の質感・拍・連続性だけを参照する |

複数該当するときは ` + ` でつなぎ、同じ種別を繰り返さない
(例: `[video continuation + keyframe completion]`、`[video editing + audio reuse]`)。
動画や音声が「ある」だけでは種別は発生しない。参照動画がカメラワーク・カット・
リズムしか与えないなら通常は `reference generation`。

要約では既定義の `<Subject N>` `<Picture N>` `<Video N>` `<Audio N>` を使って
主要な被写体・ショットの流れ・各素材の役割を書く。ここで新しいラベルを導入しない。
動画編集タスクでは種別の直後を `The target video is an edited version of <Video 1>.` で始める。

## `retention_analysis`

参照ラベルごとに1行。`subject_definitions` で定めた意味を保つ。

可視内容(`<Subject N>` / `<Picture N>` / `<Video N>`)の関係マーカー:

| マーカー | 意味 |
|---|---|
| `fully_preserved` | 定義された役割が完全に保たれる |
| `partially_preserved` | 使われてはいるが、定義した特徴の一部が変わる/部分的にしか残らない |
| `attribute_transfer` | 参照した特徴が別の識別可能な被写体へ転写される |
| `weak_reference` | スタイル・カテゴリ・構図・雰囲気の大まかな類似だけ残る |

```text
<Subject 1> (appears in [Shot 1], [Shot 3]): fully_preserved - ...
<Picture 2> ([Shot 1] first frame): fully_preserved - ...
<Video 1> (cut and pacing structure): weak_reference - ...
```

音声(`<Audio N>`)の関係マーカー:

| マーカー | 意味 |
|---|---|
| `fully_copy` | 元音声全体が目標動画の最終音声トラックそのものになる |
| `partially_copy` | 時間軸の一部/一部レイヤーだけ複製する、または複製後に他の音を足す・除く・差し替える |
| `reference` | 信号は複製せず、音色・リズム・音楽スタイル・台詞内容・音の質感だけ参照する |
| `weak_reference` | カテゴリや雰囲気の大まかな類似だけ残る |

```text
<Audio 1>: fully_copy - <Audio 1> is reused 1:1 as the target video's complete final audio track.
<Audio 2>: reference - the target speaker follows <Audio 2>'s voice timbre and measured delivery without copying the original signal.
```

マーカーは `subject_definitions` で定義した参照上の役割の範囲内で選ぶ。
目標動画で新しく足した動作・背景・出来事は、参照忠実度の低下として扱わない。
`retention_analysis` に `(Sx)` は書かない。

## `detailed_description`

基本モードとの差分:

| 次元 | T2VA | 参照モード |
|---|---|---|
| 主フィールド | `integrated_multimodal_description` | `detailed_description` |
| スタイル宣言 | `[Shot 1]` の後に書く | `[Shot 1]` の**前**に英語1〜2文で置く |
| 参照情報 | 参照ラベルを使わない | 初出と役割が効く箇所に `<Subject N>` `<Picture N>` `<Video N>` `<Audio N>` を挿す |
| 音声関係 | 目標動画自身の音を書く | 対応するショット/音声フェーズで `<Audio N>` を引き、複製か参照かを述べる |

```text
The target video is in a cinematic, literary music-video style with soft lighting and a slightly desaturated color palette.
[Shot 1] The scene opens in a crowded urban street...
[Shot 2] At 00:09.000, the shot cuts to an extreme close-up...
```

生成タスクでは通常 **350〜500 英単語**。台詞が密な内容では語数を機械的に埋めるより
発話のタイムラインを収めることを優先する。単一ショットだからといって短くてよい
わけではなく、情報量に応じて各ショットへ詳細を配分する。

重要な `<Subject N>` の最初の明確な登場箇所で、参照された特徴・画面内の位置・
いまの動作を、そのショットで実際に見えている範囲で書く。以降のショットでは
同じラベルを使い続け、ラベルの中身を定義し直さない。

具体的なフレームアンカーは自然な言い回しで指す:

```text
the shot begins from <Picture 1>
the shot's keyframe corresponds to <Picture 2>
the shot ends on <Picture 3>
```

### 話者と参照音声

参照された被写体が実際に発話するときは、視覚ラベルと話者IDを両方残す。

```text
<Subject 2> (S1) turns toward the woman and says, <d>[English] Last summer, I went to my grandfather's house.</d>
```

`<Subject N>` は参照被写体、`(Sx)` は実際の発話者を指す。同じ被写体が画面外で
話すときも同じ形にして `off-screen` と書く。話者が定義済み subject に対応しない
ときは、安定した声の説明 + `(Sx)`。

そのまま再利用する BGM や完成済みサウンドトラックの中の言語的な要素で、
人物・キャラ・ナレーター等の独立した発声源が物理的に出しているのでないものは、
`<Audio N>` を音源として扱い、`(Sx)` を新設しない。

```text
When <Audio 1> reaches the phrase <d>[English] I'm lonely lonely lonely</d>, <Subject 1> performs the corresponding hand gesture without becoming a separate speaker source.
```

参照音声の台詞・ナレーション・歌詞をそのまま再利用する場合、または入力で
再演を明示的に求められた場合は、原文の語と言語を `<d>` の中に厳密に保つ。
聞き取れない区間は推測せず `[unclear]` と書く。句読点は文を表すのに必要な
基本記号(`,` `.` `?` `!`)に正規化し、連続チルダ・絵文字・装飾的な記号は落とす。
音色・リズム・感情・話し方だけを参照する場合は、元の台詞を目標動画に持ち込まない。

`(Sx)` は目標動画の実際の発声イベント順に一度だけ割り当て、以降は再利用する。

## `overall_soundscape` / `non_diegetic_music`

定義は基本モードと同じ。参照音声を使うときは、複製/参照の関係を**聞こえる層に
対応するセクションだけ**に書く(環境音・効果音は `overall_soundscape`、
観客だけに聞こえるスコアは `non_diegetic_music`)。同じ音声が両方を提供する
場合は、それぞれのセクションに対応する関係を書く。

```text
overall_soundscape: The copied ambience layer from <Audio 1> continues throughout the target video.
non_diegetic_music: <Audio 2> is directly reused as the complete audience-only score.
```

台詞と歌詞の全文は `detailed_description` の `<d>` の中だけに書き、
この2セクションで繰り返さない。

## 完全な例

```text
subject_definitions:
<Subject 1> is the coffee-shop environment in <Picture 1>, featuring an exposed brick wall, an orange tufted sofa with patterned pillows, a neon sign, and a wooden coffee table.
<Subject 2> is the fluffy white Samoyed in <Picture 2>, <Picture 3>, and <Picture 4>, with thick white fur, pointed ears, a dark nose, and a curved tail.
<Subject 3> is the young blonde woman in <Video 1>, with long blonde hair and a light-pink button-down shirt with rolled-up sleeves.
<Subject 4> is the young man in <Video 2>, with short wavy brown hair and a dark-grey hoodie with drawstrings.
<Audio 1> is the voice-timbre reference for <Subject 3> (S1), containing a spoken English vocal layer.

summary:
[reference generation + audio reference] The target video shows <Subject 3> eating a cookie in <Subject 1>. <Subject 4> enters with <Subject 2>, which lunges toward the cookie. The three-shot exchange uses <Audio 1> as the voice-timbre reference for <Subject 3> and ends with a canned audience laugh.

retention_analysis:
<Subject 1> (appears in [Shot 1], [Shot 2], [Shot 3]): fully_preserved - the exposed brick wall, orange tufted sofa, patterned pillows, neon sign, and wooden coffee table are retained.
<Subject 2> (appears in [Shot 1], [Shot 2]): fully_preserved - the Samoyed's thick white fur, pointed ears, dark nose, and curved tail are retained.
<Subject 3> (appears in [Shot 1], [Shot 2], [Shot 3]): fully_preserved - the blonde woman's identity, long hair, and light-pink shirt are retained.
<Subject 4> (appears in [Shot 1], [Shot 2]): fully_preserved - the young man's short wavy brown hair and dark-grey hoodie are retained.
<Audio 1>: reference - its vocal timbre guides the dialogue delivery of <Subject 3> without copying the original signal.

detailed_description:
The target video uses a realistic multi-camera sitcom style with warm indoor lighting.
[Shot 1] A medium shot establishes <Subject 1>, the coffee shop with its exposed brick wall, orange tufted sofa, patterned pillows, neon sign, and wooden coffee table. <Subject 3> (S1), the young woman with long blonde hair and a light-pink button-down shirt with rolled-up sleeves, sits on the sofa holding a chocolate-chip cookie. From the left, <Subject 4>, the young man with short wavy brown hair and a dark-grey hoodie with drawstrings, enters holding the leash of <Subject 2>, the thick-furred white Samoyed with pointed ears, a dark nose, and a curved tail. The dog lunges toward the cookie and pulls the leash taut. <Subject 3> (S1) jerks her hand back and, using the clear youthful voice timbre referenced from <Audio 1>, exclaims with light annoyance, <d>[English] Hey! Watch your dog!</d> She closes her lips and guards the cookie while <Subject 4> pulls the dog back.
[Shot 2] At 00:03.000, the shot cuts to a close-up of <Subject 4> (S2), the young man in the dark-grey hoodie from Shot 1, sitting beside <Subject 3> on the sofa and holding <Subject 2> securely in his arms. <Subject 4> (S2) says in a casual young male voice with a playful tone and an easy conversational pace, <d>[English] He just likes cookies more than me.</d> He closes his mouth into an apologetic smile and strokes the dog's thick white fur.
[Shot 3] At 00:05.000, the shot cuts to a close-up of <Subject 3> (S1), the blonde woman in the light-pink shirt from Shot 1. Her annoyance softens as she looks toward the Samoyed. <Subject 3> (S1) replies in the same clear youthful voice referenced from <Audio 1> with an amused cadence, <d>[English] Well, he has good taste at least.</d> She smiles and raises the cookie in a small toast-like gesture. A classic canned audience laugh begins immediately after the line and continues through the final frame.

overall_soundscape:
Soft indoor coffee-shop room tone continues throughout the scene.

non_diegetic_music:
N/A
```
