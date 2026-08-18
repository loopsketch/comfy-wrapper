"""Colab の OAuth を機械的に延長し、状態を .colab/auth-state.json に書く。
初回と再認証の入れ直しも、対話端末を使わずにここで済ませる。

    docker compose exec -T colab python /app/src/scripts/colab_auth.py
    ... --force    期限に関係なく更新する(refresh_token がまだ生きているかの確認)
    ... --quiet    状態を出力しない(終了コードだけ見たいとき)
    ... login --url          認可 URL を出す
    ... login --code <code>  貼られたコードで token.json を書く(対話なし)

終了コード: 0 = ok / 3 = 再認証が要る / 4 = 判定できない

**認証を2つのコマンドに割ってある。** colab-cli の `_run_remote_flow` は URL を出した
あと `input()` で標準入力を待つので、対話端末からしか終われない。認可 URL を出す側と
コードを渡す側を分けると、人がやるのは「リンクを開く」「出たコードを返す」だけになり、
端末に入らなくても認証を通せる。延長 (refresh) はそのあと機械的に回る。

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
# login --url と login --code は別プロセスなので、間の state と code_verifier を
# ここへ預ける。**認証が通ったら消す。** 中身は短命だが認可コードと対になる
FLOW_FILE = Path("/app/.colab/.auth-flow.json")
# colab-cli が読む OAuth クライアント設定。無ければパッケージ同梱のものへ落ちる
CLIENT_CONFIG = HOME / ".colab-cli-oauth-config.json"

# 期限までこれを切ったら更新する。**監視の確認間隔より長く取る。** 同じにすると
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


def _read_json(path: Path) -> dict | None:
    """読めなければ None。無い・壊れているを呼ぶ側で分けない。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def read_state() -> dict | None:
    """他のスクリプトから最後の確認結果を読む。無ければ None。"""
    return _read_json(STATE)


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
            f"再認証が要る: {state.get('error', '理由不明')} / cw auth login"
        )
    return f"認証を確認できない: {state.get('error', '理由不明')}"


# --- 認証の入れ直し (対話端末を使わない) ------------------------------------


def _client_config() -> dict:
    """OAuth クライアント設定を colab-cli と同じ順で探す。

    **同じ client_id を使うこと。** REMOTE_REDIRECT_URI (Google の貼り付けページ) は
    cloud-SDK のクライアントに登録されたものなので、別の client_id と組むと
    redirect_uri_mismatch で弾かれる。
    """
    if CLIENT_CONFIG.exists():
        return json.loads(CLIENT_CONFIG.read_text(encoding="utf-8"))
    from importlib import resources

    return json.loads(
        resources.files("colab_cli").joinpath("oauth_config.json").read_text()
    )


def _build_flow(state: str | None = None, code_verifier: str | None = None):
    """colab-cli の `_run_remote_flow` と同じ flow を、input() 抜きで組む。

    スコープと redirect は colab_cli から借りる。**写さない。** 1つでもずれると
    キープアライブが 403 になるので、向こうが変えたらこちらも一緒に変わってほしい。
    """
    from colab_cli.auth import PUBLIC_SCOPES, REMOTE_REDIRECT_URI
    from google_auth_oauthlib.flow import InstalledAppFlow

    kwargs: dict = {}
    if state:
        kwargs["state"] = state
    if code_verifier:
        kwargs["code_verifier"] = code_verifier
    flow = InstalledAppFlow.from_client_config(_client_config(), PUBLIC_SCOPES, **kwargs)
    flow.redirect_uri = REMOTE_REDIRECT_URI
    return flow


def login_url() -> str:
    """認可 URL を返し、続きに要る state と code_verifier を預ける。

    google-auth-oauthlib は既定で PKCE を付ける (`autogenerate_code_verifier`)。
    code_verifier は URL を出した側にしか無いので、**残さないと --code 側が必ず
    落ちる**。付かない版でも困らないよう、あるときだけ書く。
    """
    flow = _build_flow()
    url, state = flow.authorization_url(prompt="consent", token_usage="remote")
    _write_json(FLOW_FILE, {"state": state, "code_verifier": flow.code_verifier})
    return url


def login_code(code: str) -> dict:
    """貼られた認可コードで token.json を書き、確認結果を返す。

    書き込みは check() と同じ差し替え (`_write_json`)。**書きかけを colab-cli に
    読ませない。** 通ったら預けたものを消す。
    """
    saved = _read_json(FLOW_FILE)
    if saved is None:
        raise RuntimeError(
            f"{FLOW_FILE} がありません。cw auth login からやり直してください "
            "(認可 URL を出した側にしか code_verifier がありません)"
        )

    flow = _build_flow(state=saved.get("state"), code_verifier=saved.get("code_verifier"))
    flow.fetch_token(code=code)
    _write_json(TOKEN, json.loads(flow.credentials.to_json()))
    FLOW_FILE.unlink(missing_ok=True)
    return check_and_record()


def _main_login(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="colab_auth.py login",
                                description="対話端末を使わずに認証を入れ直す")
    p.add_argument("--url", action="store_true", help="認可 URL を出す (既定)")
    p.add_argument("--code", help="貼られた認可コードで token.json を書く")
    args = p.parse_args(argv)

    if not args.code:
        try:
            url = login_url()
        except Exception as e:
            print(f"認可 URL を作れません: {type(e).__name__}: {e}", file=sys.stderr)
            return 3
        print(url)
        return 0

    try:
        state = login_code(args.code)
    except Exception as e:
        # コードは1回しか使えず、数分で切れる。次の一手が分かるように名指しする
        print(f"認証できません: {type(e).__name__}: {e}\n"
              "コードは1度きりで有効時間も短いです。cw auth login からやり直してください",
              file=sys.stderr)
        return 3
    print(describe(state))
    return EXIT_CODES.get(state["state"], 4)


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "login":
        return _main_login(argv[1:])

    p = argparse.ArgumentParser(description="Colab の OAuth を延長して状態を書く")
    p.add_argument("--force", action="store_true", help="期限に関係なく更新する")
    p.add_argument("--quiet", action="store_true", help="出力しない")
    p.add_argument("--json", action="store_true", help="状態を JSON で出す")
    args = p.parse_args(argv)

    state = check_and_record(force=args.force)
    if args.json:
        print(json.dumps(state, ensure_ascii=False))
    elif not args.quiet:
        print(describe(state))
    return EXIT_CODES.get(state["state"], 4)


if __name__ == "__main__":
    sys.exit(main())
