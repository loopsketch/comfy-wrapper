# ドキュメント

入れ方と最短の使い方は [README.ja.md](../README.ja.md)(英語版は [README.md](../README.md))。
ここに置いてあるのは、そこに書ききれなかった「なぜそうしているか」と「実際にどうだったか」。

| ドキュメント | 中身 |
|---|---|
| [architecture.md](architecture.md) | 構成と設計判断。ComfyUI を外に出さない理由、トンネルの変遷、キーの扱い、ジョブの記録 |
| [operations.md](operations.md) | 運用ガイド。課金を止めながら回す手順、確保と停止、監視、他プロジェクトから頼む経路 |
| [models.md](models.md) | モデルの選び方。L4 に載るか、尺のグリッド、参照の渡し方、静止画、解像度と拡大 |
| [benchmarks.md](benchmarks.md) | 実測記録。モデル間の比較、GPU 別、セッション単位のコスト、外部サービスとの比較 |
| [troubleshooting.md](troubleshooting.md) | 実際に踏んだ失敗と、そのときどうしたか |

読む順番に決まりはないが、初めてなら README.ja.md から operations.md、models.md と
辿るのが素直だと思う。いくらかかるかだけ知りたいときは benchmarks.md を直接どうぞ。

## 数字の扱い

実測値には測定日と条件を必ず添えてある。GPU の当たり外れや回線の速さで簡単に倍近く
変わるので、金額を決める場面では自分の環境で測り直してほしい。
`src/scripts/measure_video.py` がロード込みとロード済みを分けて出す。

換算は 1$ = 157.87円、Colab は 100CU = 1179円(1CU = 11.79円)で統一している。

## Claude Code のスキル

同じ手順を実行側から持っているものが `.claude/skills/` にある。`colab-comfy` が確保から
停止までの手順と課金を止めるための鉄則を、`ltx-prompt` が LTX-2.5 向けのプロンプトの
書き方を持っている。別のプロジェクトへ入れるときは `cw init skills` で足りる。

MiniMax H3 の記法は
[公式スキル](https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills) に任せている。
