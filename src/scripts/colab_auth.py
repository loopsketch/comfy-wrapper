"""Colab の OAuth を機械的に延長し、状態を .colab/auth-state.json に書く。

    docker compose exec -T colab python /app/src/scripts/colab_auth.py
    ... --force    期限に関係なく更新する(refresh_token がまだ生きているかの確認)
    ... --quiet    状態を出力しない(終了コードだけ見たいとき)

終了コード: 0 = ok / 3 = 再認証が要る / 4 = 判定できない

初回の認証だけは人が要る(ブラウザで URL を開いてコードを貼る)。そのあとの延長は
refresh_token で機械的にできるので、これを定期的に回してトークンを生かし続ける。

**`colab` コマンドを叩いて確認してはいけない。** colab-cli は refresh に失敗すると
対話フロー(`colab_cli/auth.py` の `_run_remote_flow`)へ落ち、`input()` で標準入力を
待つ。無人のループから呼ぶとそこで永久に止まる。ここでは google-auth を直接使い、
**refresh だけを試して、失敗したら失敗と書く**。

トークンの形式と保存先は colab-cli と同じ(`~/.config/colab-cli/token.json`)。
HOME は docker-compose.yml で /app/.colab に差し替えてある。
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HOME = Path(os.environ.get("HOME", "/app/.colab"))
TOKEN = HOME / ".config" / "colab-cli" / "token.json"
STATE = Path("/app/.colab/auth-state.json")

# 期限までこれを切ったら更新する。**見張りの確認間隔より長く取る。** 同じにすると
# 「まだ余裕がある」と見送った次の確認が期限後になり、切れた状態を作ってしまう
REFRESH_MARGIN_MIN = 35.0

# colab-cli が要求するスコープ。1つでも欠けるとキープアライブが 403 になる
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/colaboratory",
    "https://www.googleapis.com/auth/drive.file",
]

EXIT_CODES = {"ok": 0, "reauth_needed": 3}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _write_json(path: Path, data: dict) -> None:
    """同じディレクトリの一時ファイルに書いてから差し替える。

    token.json は colab-cli も読み書きする。書きかけを読ませないため、
    追記ではなく rename で入れ替える。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def check(force: bool = False) -> dict:
    """必要ならトークンを更新し、状態の dict を返す。例外は投げない。

    state は ok / reauth_needed / unknown のどれか。unknown は「認証が切れたのか
    こちらの都合で見られないだけなのか分からない」で、切れたことにはしない。
    """
    state: dict = {
        "state": "unknown",
        "checked_at": datetime.now().astimezone().isoformat(),
        "refreshed": False,
    }

    if not TOKEN.exists():
        state["state"] = "reauth_needed"
        state["error"] = f"{TOKEN} がありません(まだ一度も認証していない)"
        return state

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError as e:
        state["error"] = f"google-auth を読めません: {e}"
        return state

    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    except Exception as e:
        state["state"] = "reauth_needed"
        state["error"] = f"トークンを読めません: {e}"
        return state

    account = getattr(creds, "account", None)
    if account:
        state["account"] = account

    remaining_min = -1.0
    if creds.expiry is not None:
        expiry = creds.expiry
        if expiry.tzinfo is None:
            # google-auth は naive な UTC で持つ
            expiry = expiry.replace(tzinfo=timezone.utc)
        state["expiry"] = expiry.isoformat()
        remaining_min = (expiry - _now()).total_seconds() / 60

    if not creds.refresh_token:
        state["state"] = "reauth_needed"
        state["error"] = "refresh_token がありません"
        return state

    if not force and remaining_min > REFRESH_MARGIN_MIN:
        state["state"] = "ok"
        state["remaining_min"] = round(remaining_min, 1)
        return state

    try:
        creds.refresh(Request())
    except Exception as e:
        # ここに来るのが「半日で切れた」状態。人がブラウザで入れ直すしかない
        state["state"] = "reauth_needed"
        state["error"] = f"{type(e).__name__}: {e}"
        return state

    try:
        _write_json(TOKEN, json.loads(creds.to_json()))
    except OSError as e:
        # 更新はできたがファイルに残せなかった。次回また更新するだけで害は無い
        state["error"] = f"トークンを保存できません: {e}"

    state["state"] = "ok"
    state["refreshed"] = True
    if creds.expiry is not None:
        expiry = creds.expiry
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        state["expiry"] = expiry.isoformat()
        state["remaining_min"] = round((expiry - _now()).total_seconds() / 60, 1)
    return state


def check_and_record(force: bool = False) -> dict:
    """check() の結果を .colab/auth-state.json に残す。"""
    state = check(force=force)
    try:
        _write_json(STATE, state)
    except OSError:
        pass
    return state


def read_state() -> dict | None:
    """他のスクリプトから最後の確認結果を読む。無ければ None。"""
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def describe(state: dict) -> str:
    """人が読む1行。"""
    kind = state.get("state")
    if kind == "ok":
        tail = "更新した" if state.get("refreshed") else "まだ有効"
        remain = state.get("remaining_min")
        if remain is not None:
            tail += f" (残り {remain:.0f}分)"
        return f"認証は生きている: {tail}"
    if kind == "reauth_needed":
        return (
            f"再認証が要る: {state.get('error', '理由不明')} / "
            "docker compose exec colab colab sessions"
        )
    return f"認証を確認できない: {state.get('error', '理由不明')}"


def main() -> int:
    p = argparse.ArgumentParser(description="Colab の OAuth を延長して状態を書く")
    p.add_argument("--force", action="store_true", help="期限に関係なく更新する")
    p.add_argument("--quiet", action="store_true", help="出力しない")
    p.add_argument("--json", action="store_true", help="状態を JSON で出す")
    args = p.parse_args()

    state = check_and_record(force=args.force)
    if args.json:
        print(json.dumps(state, ensure_ascii=False))
    elif not args.quiet:
        print(describe(state))
    return EXIT_CODES.get(state["state"], 4)


if __name__ == "__main__":
    sys.exit(main())
