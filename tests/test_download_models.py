"""H3 ウェイト取得の事前チェックと、書きかけの後始末。

**実際に落として確かめない。** 42.5GB を取得するスクリプトなので、検証のつもりで
走らせると本当に落ち始める(2026-08-09 に手元で踏んだ)。判断のところだけを見る。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import _bootstrap  # noqa: F401

_SETUP = Path(_bootstrap.SRC) / "setup"


def _load(name):
    sys.path.insert(0, str(_SETUP))
    spec = importlib.util.spec_from_file_location(name, _SETUP / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dm = _load("download_models")
rd = _load("resilient_download")


class DownloadModelsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def _run(self, free_gb: float, extra=()):
        """main() を空き容量だけ差し替えて回す。取得には入らせない。"""
        self.enterContext(patch.object(
            shutil, "disk_usage",
            lambda _p: type("U", (), {"total": 0, "used": 0, "free": int(free_gb * 1024 ** 3)})(),
        ))
        called = []
        self.enterContext(patch.object(dm, "fetch", lambda *a, **k: called.append(a[0])))
        self.enterContext(patch.object(
            sys, "argv", ["download_models.py", "--comfy", str(self.tmp), *extra]
        ))
        return dm.main(), called

    def test_stops_before_downloading_when_disk_is_short(self):
        """21GB を取り終えてから次の 15.7GB で落ちると、そこまでの課金が丸損になる。"""
        code, called = self._run(free_gb=30.0)
        self.assertEqual(code, 1)
        self.assertEqual(called, [])  # 1ファイルも取りに行かない

    def test_proceeds_when_disk_is_enough(self):
        code, called = self._run(free_gb=80.0)
        self.assertEqual(code, 0)
        self.assertEqual(len(called), 4)  # fl2va / text encoder / vae 2本

    def test_already_present_files_are_not_counted(self):
        """取得済みのぶんは空き容量の判定から外す。"""
        big = self.tmp / "models" / dm.DIFFUSION["int8"]["fl2va"][0]
        big.parent.mkdir(parents=True)
        big.write_bytes(b"x")

        # 21GB を除けば 21.5GB で足りる
        code, _ = self._run(free_gb=30.0)
        self.assertEqual(code, 0)


class PurgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_purge_removes_only_the_stale_partials(self):
        """直前の試行で書いていた分は再開に使うので残す。"""
        d = self.tmp / "download"
        d.mkdir()
        old, new = d / "old.incomplete", d / "new.incomplete"
        old.write_bytes(b"x" * 1000)

        # cutoff = 直前の試行が始まった時刻。これ以降に書かれたものが「生きている」
        cutoff = time.time()
        os.utime(old, (cutoff - 600, cutoff - 600))
        time.sleep(0.01)
        new.write_bytes(b"y" * 500)

        freed = rd.purge([str(self.tmp)], lambda *_: None, "テスト", older_than=cutoff)

        self.assertEqual(freed, 1000)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())

    def test_purge_without_cutoff_removes_everything(self):
        """経路が変わると再開できないので、全部捨てる。"""
        d = self.tmp / "download"
        d.mkdir()
        (d / "a.incomplete").write_bytes(b"x" * 100)
        (d / "b.incomplete").write_bytes(b"y" * 200)
        (d / "keep.safetensors").write_bytes(b"z" * 999)

        freed = rd.purge([str(self.tmp)], lambda *_: None, "テスト")

        self.assertEqual(freed, 300)
        self.assertEqual(rd.partials([str(self.tmp)]), [])
        self.assertTrue((d / "keep.safetensors").exists())  # 完成品は消さない


if __name__ == "__main__":
    unittest.main()
