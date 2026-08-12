#!/usr/bin/env python3
"""アクセスキーの発行・一覧・失効。

  python genkey.py issue --name comfy-local     # 発行(平文はこの時だけ表示)
  python genkey.py list
  python genkey.py revoke --id 1a2b3c4d
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from auth import KeyStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="MiniMax H3 サーバのアクセスキー管理")
    parser.add_argument("--keys", default="keys.json", help="キーストアのパス")
    sub = parser.add_subparsers(dest="command", required=True)

    p_issue = sub.add_parser("issue", help="キーを発行する")
    p_issue.add_argument("--name", default="default", help="用途がわかる名前")
    p_issue.add_argument("--env", action="store_true", help=".env に貼る形式で出力")

    sub.add_parser("list", help="発行済みキーを一覧する")

    p_revoke = sub.add_parser("revoke", help="キーを失効させる")
    p_revoke.add_argument("--id", required=True)

    args = parser.parse_args()
    store = KeyStore(Path(args.keys))

    if args.command == "issue":
        key, record = store.issue(args.name)
        if args.env:
            print(f"COLAB_API_KEY={key}")
        else:
            print(f"id      : {record.id}")
            print(f"name    : {record.name}")
            print(f"created : {record.created_at}")
            print(f"key     : {key}")
            print("\nこの平文キーは再表示できません。手元の .env に控えてください。")
        return 0

    if args.command == "list":
        if not store.records:
            print("キーがありません")
            return 0
        for r in store.records:
            mark = "revoked" if r.revoked else "active"
            print(f"{r.id}  {mark:8}  {r.created_at}  {r.name}")
        return 0

    if args.command == "revoke":
        if store.revoke(args.id):
            print(f"{args.id} を失効させました")
            return 0
        print(f"{args.id} が見つかりません", file=sys.stderr)
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
