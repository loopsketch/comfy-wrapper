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


class DescribeTest(unittest.TestCase):
    def test_describe_tells_the_next_move(self):
        cases = [
            ({"state": "ok", "refreshed": True, "remaining_min": 60.0}, "更新した"),
            ({"state": "ok", "refreshed": False, "remaining_min": 12.0}, "まだ有効"),
            ({"state": "reauth_needed", "error": "壊れた"}, "colab sessions"),
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
