"""確保されたまま台帳から外れたランタイムを解放する。**colab コンテナで動かす。**

`colab stop -s <名前>` は台帳にあるセッションしか止められない。接続に失敗した回や
台帳を作り直した回は、割り当てだけがサーバ側に残る。`colab sessions` には
`[?]` で始まる名前なしとして出てきて、**放っておくと課金が続く**。

    python src/scripts/colab_unassign.py            名前の無い割り当てだけ解放する
    python src/scripts/colab_unassign.py --all      名前つきも含めてすべて解放する
"""

from __future__ import annotations

import argparse

from colab_cli.common import state


def main() -> int:
    parser = argparse.ArgumentParser(description="確保されたままの割り当てを解放する")
    parser.add_argument("--all", action="store_true",
                        help="台帳にあるセッションも含めてすべて解放する")
    parser.add_argument("--dry-run", action="store_true", help="何を解放するかだけ出す")
    args = parser.parse_args()

    sessions, assignments = state.sync_sessions()
    named = {getattr(s, "endpoint", None) for s in sessions}
    targets = [a for a in assignments
               if args.all or getattr(a, "endpoint", None) not in named]

    if not targets:
        print("解放するものはありません")
        return 0

    for assignment in targets:
        endpoint = assignment.endpoint
        if args.dry_run:
            print(f"解放する対象: {endpoint}")
            continue
        state.client.unassign(endpoint)
        print(f"解放しました: {endpoint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
