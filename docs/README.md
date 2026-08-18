# ドキュメント

入れ方と最短の使い方は [README.ja.md](../README.ja.md) (英語版は [README.md](../README.md))。
ここには、そこに書ききれない「なぜそうしているか」と「実際にどうだったか」を置いている。

| ドキュメント | 中身 |
|---|---|
| [architecture.md](architecture.md) | 構成と設計判断。ComfyUI を出さない理由、トンネル、キー、ジョブ台帳、モジュールの役割 |
| [operations.md](operations.md) | 運用ガイド。課金を止める回し方、確保と停止、見張り、他プロジェクトから頼む |
| [models.md](models.md) | モデルの選び方。L4 に載るか、尺のグリッド、参照の渡し方、静止画、解像度と拡大 |
| [benchmarks.md](benchmarks.md) | 実測記録。モデル間の比較、GPU 別、セッション単位のコスト、外部サービスとの比較 |
| [troubleshooting.md](troubleshooting.md) | 実際に踏んだ失敗と対処 |

読む順番に決まりは無いが、初めてなら README.ja.md → operations.md → models.md が素直。
コストの判断材料が要るときは benchmarks.md を直接見る。

## 数字の扱い

実測値には測定日と条件を必ず添えている。GPU の当たり外れや回線の速さで簡単に倍近く
変わるので、額を決める場面では自分の環境で測り直すこと。`src/scripts/measure_video.py` が
ロード込みとロード済みを分けて出す。

換算は 1$ = 157.87円、Colab は 100CU = 1179円 (1CU = 11.79円) で統一している。

## Claude Code のスキル

`.claude/skills/` にある2つが、このドキュメントの手順を実行側から持っている。

- `colab-comfy` は確保から停止までの手順と、課金を止めるための鉄則
- `ltx-prompt` は LTX-2.5 向けのプロンプトの書き方

別のプロジェクトへ入れるときは `cw init skills`。MiniMax H3 の記法は
[公式スキル](https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills) に任せている。
