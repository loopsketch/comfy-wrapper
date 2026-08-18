"""止まったら殺して再開する取得(setup/resilient_download.py)。

**実際にハングさせて確かめる。** Xet の停止は例外を上げないので、try/except の
テストでは再現にならない。子プロセスを本当に固めて、親が殺して取り直すことを見る。
"""

from __future__ import annotations

import importlib.util
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import _bootstrap  # noqa: F401

_SRC = Path(_bootstrap.SRC) / "setup" / "resilient_download.py"
_spec = importlib.util.spec_from_file_location("resilient_download", _SRC)
rd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rd)


def _hang(*args):
    """永遠に固まる子。Xet の xet_get() と同じで、例外を上げない。"""
    time.sleep(3600)


def _succeed(repo, filename, local_dir, disable_xet, q):
    q.put(("ok", f"/fake/{filename}"))


def _fail(repo, filename, local_dir, disable_xet, q):
    q.put(("err", "ConnectionError: 切れた"))


class ResilientDownloadTest(unittest.TestCase):
    def setUp(self):
        # 待ち時間を縮めてテストを現実的な長さにする
        for name, value in (("STALL_SECONDS", 1.0), ("POLL_SECONDS", 0.2), ("MIN_GROWTH", 1)):
            self.enterContext(patch.object(rd, name, value))
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_incomplete_bytes_counts_and_ignores_missing(self):
        d = self.tmp / "cache" / "download"
        d.mkdir(parents=True)
        (d / "a.incomplete").write_bytes(b"x" * 100)
        (d / "b.incomplete").write_bytes(b"y" * 50)
        (d / "done.safetensors").write_bytes(b"z" * 999)  # 完成品は数えない

        self.assertEqual(rd.incomplete_bytes([str(self.tmp / "cache")]), 150)
        # 存在しないディレクトリで落ちない
        self.assertEqual(rd.incomplete_bytes([str(self.tmp / "nope")]), 0)

    def test_kills_a_hung_download_and_retries(self):
        """無応答の子を殺して取り直し、最終的に成功する。

        これが出来ないと、例外の上がらない Xet の停止で永久に待つ。
        子は別プロセスなので、呼ばれた記録はファイル越しに受け取る。
        """
        ledger = self.tmp / "calls.txt"

        def child(repo, filename, local_dir, disable_xet, q):
            with ledger.open("a") as f:
                f.write(f"{disable_xet}\n")
            if len(ledger.read_text().splitlines()) < 2:
                _hang()
            _succeed(repo, filename, local_dir, disable_xet, q)

        self.enterContext(patch.object(rd, "_child", child))
        self.enterContext(patch.object(rd, "ATTEMPTS", 3))

        got = rd.download(
            "repo", "big.safetensors", watch_roots=[str(self.tmp)], log=lambda *_: None
        )
        self.assertEqual(got, "/fake/big.safetensors")
        # 1回目は殺され、2回目で成功
        self.assertEqual(len(ledger.read_text().splitlines()), 2)

    def test_last_attempt_disables_xet(self):
        """遅くても確実な経路を最後に必ず試す。"""
        ledger = self.tmp / "seen.txt"

        def child(repo, filename, local_dir, disable_xet, q):
            with ledger.open("a") as f:
                f.write(f"{disable_xet}\n")
            if not disable_xet:
                _hang()
            _succeed(repo, filename, local_dir, disable_xet, q)

        self.enterContext(patch.object(rd, "_child", child))
        self.enterContext(patch.object(rd, "ATTEMPTS", 3))

        rd.download("repo", "big.safetensors", watch_roots=[str(self.tmp)], log=lambda *_: None)
        # 最後だけ Xet 無効
        self.assertEqual(ledger.read_text().split(), ["False", "False", "True"])

    def test_progress_prevents_the_kill(self):
        """伸びている間は殺さない。長い取得を誤って打ち切らないため。"""
        root = self.tmp / "cache"
        root.mkdir()
        inc = root / "x.incomplete"
        inc.write_bytes(b"")

        def child(repo, filename, local_dir, disable_xet, q):
            # 止まっているように見える間隔をあけつつ、少しずつ書き足す
            for i in range(1, 6):
                time.sleep(0.5)
                inc.write_bytes(b"x" * (i * 1000))
            q.put(("ok", "/fake/done"))

        self.enterContext(patch.object(rd, "_child", child))
        self.enterContext(patch.object(rd, "ATTEMPTS", 2))

        got = rd.download("repo", "f", watch_roots=[str(root)], log=lambda *_: None)
        self.assertEqual(got, "/fake/done")

    def test_gives_up_after_all_attempts(self):
        self.enterContext(patch.object(rd, "_child", _fail))
        self.enterContext(patch.object(rd, "ATTEMPTS", 2))

        with self.assertRaisesRegex(RuntimeError, "2回試して"):
            rd.download("repo", "f", watch_roots=[str(self.tmp)], log=lambda *_: None)


if __name__ == "__main__":
    unittest.main()
