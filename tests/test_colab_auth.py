"""Colab の OAuth 延長 (scripts/colab_auth.py)。

ここで固定したいのは2つ。

- **どんな失敗でも例外を投げない。** これは無人のループから呼ばれる。落ちると
  「切れたことにも気づけない」状態になり、見張りが盲目のまま上限まで回る
- **切れているのか、こちらの都合で見られないだけなのかを混ぜない。**
  再認証が要るのは reauth_needed だけで、判定できないときは unknown にする

google-auth はこのコンテナに入っていないが、`check()` は必要になった時点で
import するので、モジュールの読み込み自体はできる。壊れたトークンの扱いだけは
google-auth が要るので、無ければその回だけ飛ばす。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _bootstrap  # noqa: F401

_SCRIPTS = Path(_bootstrap.SRC) / "scripts"


def _load(name):
    sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


auth = _load("colab_auth")


class FakeFlow:
    """google_auth_oauthlib の InstalledAppFlow のうち、ここで使う面だけ。"""

    def __init__(self):
        self.code_verifier = None
        self.state = None
        self.fetched = None
        self.error = None

    def authorization_url(self, **kwargs):
        # 本物と同じく、URL を作る時点で PKCE の verifier が生える
        self.code_verifier = "verifier-1"
        return "https://accounts.example.invalid/o/oauth2/auth", "st-1"

    def fetch_token(self, code):
        if self.error:
            raise self.error
        self.fetched = code

    @property
    def credentials(self):
        return _FakeCredentials()


class _FakeCredentials:
    def to_json(self):
        return json.dumps({"token": "at", "refresh_token": "rt"})


def _has_google_auth() -> bool:
    # find_spec は親パッケージが無いと ModuleNotFoundError を上げる。ここは
    # 「入っていない」が正常系なので、例外にせず False に潰す
    try:
        return importlib.util.find_spec("google.oauth2.credentials") is not None
    except ModuleNotFoundError:
        return False


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_missing_token_asks_for_reauth(self):
        self.enterContext(patch.object(auth, "TOKEN", self.tmp / "token.json"))
        state = auth.check()
        self.assertEqual(state["state"], "reauth_needed")
        self.assertIn("error", state)

    @unittest.skipUnless(_has_google_auth(), "google-auth が無い")
    def test_broken_token_asks_for_reauth(self):
        token = self.tmp / "token.json"
        token.write_text("これは JSON ではない", encoding="utf-8")
        self.enterContext(patch.object(auth, "TOKEN", token))
        self.assertEqual(auth.check()["state"], "reauth_needed")

    @unittest.skipUnless(_has_google_auth(), "google-auth が無い")
    def test_token_without_refresh_token_asks_for_reauth(self):
        token = self.tmp / "token.json"
        token.write_text(
            json.dumps({"token": "x", "client_id": "c", "client_secret": "s",
                        "token_uri": "https://example.invalid/token"}),
            encoding="utf-8",
        )
        self.enterContext(patch.object(auth, "TOKEN", token))
        self.assertEqual(auth.check()["state"], "reauth_needed")

    def test_state_is_always_recorded(self):
        """入口 (colab_run.sh) と見張りが読むので、失敗した回こそ残す必要がある。"""
        self.enterContext(patch.object(auth, "TOKEN", self.tmp / "token.json"))
        self.enterContext(patch.object(auth, "STATE", self.tmp / "auth-state.json"))
        state = auth.check_and_record()
        self.assertEqual(auth.read_state(), state)
        self.assertTrue(state["checked_at"])

    def test_read_state_returns_none_when_never_checked(self):
        self.enterContext(patch.object(auth, "STATE", self.tmp / "not-yet.json"))
        self.assertIsNone(auth.read_state())

    def test_write_is_a_swap_not_a_truncate(self):
        """token.json は colab-cli も読む。書きかけを読ませない。"""
        path = self.tmp / "out.json"
        auth._write_json(path, {"a": 1})
        auth._write_json(path, {"a": 2})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": 2})
        # 一時ファイルを残さない
        self.assertEqual([p.name for p in self.tmp.iterdir()], ["out.json"])


class LoginTest(unittest.TestCase):
    """認可 URL とコードを別プロセスに割ったときに、間で落とせないもの。

    google-auth-oauthlib はこのコンテナに無いので flow ごと差し替える。**ここで
    見たいのは Google とのやりとりではなく、URL 側でしか作れない code_verifier を
    コード側へ渡せているか**。落とすと fetch_token が必ず失敗する。
    """

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.flow_file = self.tmp / ".auth-flow.json"
        self.token = self.tmp / "token.json"
        self.enterContext(patch.object(auth, "FLOW_FILE", self.flow_file))
        self.enterContext(patch.object(auth, "TOKEN", self.token))
        self.enterContext(patch.object(auth, "STATE", self.tmp / "auth-state.json"))

        self.built: list[dict] = []
        self.flow = FakeFlow()

        def build(state=None, code_verifier=None):
            self.built.append({"state": state, "code_verifier": code_verifier})
            self.flow.state = state
            self.flow.code_verifier = code_verifier or self.flow.code_verifier
            return self.flow

        self.enterContext(patch.object(auth, "_build_flow", build))

    def test_url_is_returned_and_the_flow_is_parked(self):
        self.assertEqual(auth.login_url(), "https://accounts.example.invalid/o/oauth2/auth")
        parked = json.loads(self.flow_file.read_text(encoding="utf-8"))
        self.assertEqual(parked, {"state": "st-1", "code_verifier": "verifier-1"})

    def test_code_writes_the_token_and_clears_the_flow(self):
        auth.login_url()
        with patch.object(auth, "check_and_record", lambda force=False: {"state": "ok"}):
            self.assertEqual(auth.login_code("4/0AX-code")["state"], "ok")
        self.assertEqual(self.flow.fetched, "4/0AX-code")
        self.assertEqual(json.loads(self.token.read_text(encoding="utf-8"))["token"], "at")
        # 認可コードと対になるものを残さない
        self.assertFalse(self.flow_file.exists())

    def test_code_carries_the_verifier_from_the_url_step(self):
        """PKCE は URL を出した側にしか無い。渡し損ねると必ず fetch_token で落ちる。"""
        auth.login_url()
        with patch.object(auth, "check_and_record", lambda force=False: {"state": "ok"}):
            auth.login_code("4/0AX-code")
        self.assertEqual(
            self.built[-1], {"state": "st-1", "code_verifier": "verifier-1"}
        )

    def test_code_without_a_parked_flow_names_the_fix(self):
        with self.assertRaises(RuntimeError) as cm:
            auth.login_code("4/0AX-code")
        self.assertIn("cw auth login", str(cm.exception))

    def test_a_failed_exchange_keeps_the_old_token(self):
        """通らなかったコードで、いま生きているトークンを潰さない。"""
        self.token.parent.mkdir(parents=True, exist_ok=True)
        self.token.write_text('{"token": "生きている"}', encoding="utf-8")
        auth.login_url()
        self.flow.error = RuntimeError("invalid_grant")
        with self.assertRaises(RuntimeError):
            auth.login_code("4/0AX-code")
        self.assertEqual(
            json.loads(self.token.read_text(encoding="utf-8")), {"token": "生きている"}
        )


class DescribeTest(unittest.TestCase):
    def test_describe_tells_the_next_move(self):
        cases = [
            ({"state": "ok", "refreshed": True, "remaining_min": 60.0}, "更新した"),
            ({"state": "ok", "refreshed": False, "remaining_min": 12.0}, "まだ有効"),
            ({"state": "reauth_needed", "error": "壊れた"}, "cw auth login"),
            ({"state": "unknown", "error": "読めない"}, "確認できない"),
        ]
        for state, expected in cases:
            with self.subTest(state=state["state"]):
                self.assertIn(expected, auth.describe(state))

    def test_exit_code_follows_the_state(self):
        """colab_run.sh は終了コードで確保をやめる。0 以外を「切れた」に潰さない。"""
        self.assertEqual(auth.EXIT_CODES["ok"], 0)
        self.assertEqual(auth.EXIT_CODES["reauth_needed"], 3)
        self.assertNotIn("unknown", auth.EXIT_CODES)

    def test_refresh_margin_outlasts_the_check_interval(self):
        """同じにすると、見送った次の確認が期限後になって切れた状態を作る。"""
        watch = _load("colab_keepalive_watch")
        self.assertGreater(auth.REFRESH_MARGIN_MIN, watch.AUTH_CHECK_MINUTES)


if __name__ == "__main__":
    unittest.main()
