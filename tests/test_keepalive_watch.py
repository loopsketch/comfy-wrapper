"""監視の進捗判定 (scripts/colab_keepalive_watch.py)。

ここで固定したいのは1つ。**取得の途中で書きかけを捨ててディスクが減っても、
そこから先の取得を進捗として数えられること。** 過去の最大だけを基準にすると、
捨てた分を埋め直すまで「何も進んでいない」に落ちる。実際、80GB 捨てて 19GB を
取り直す回では、素の HTTP で取れていたのに 9分で止められた。
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

_SCRIPTS = Path(_bootstrap.SRC) / "scripts"


def _load(name):
    sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


watch = _load("colab_keepalive_watch")


class DiskMoveTest(unittest.TestCase):
    def test_first_probe_has_no_baseline(self):
        self.assertEqual(watch._disk_move(53.7, None), "init")

    def test_growth_is_progress(self):
        self.assertEqual(watch._disk_move(134.3, 130.0), "grew")

    def test_a_drop_is_reported_not_ignored(self):
        self.assertEqual(watch._disk_move(53.7, 134.3), "shrank")

    def test_jitter_is_not_a_move(self):
        # MOVE_GB 以下の増減で「動いた」と言わない。速度表示が暴れる
        self.assertEqual(watch._disk_move(100.02, 100.0), "flat")
        self.assertEqual(watch._disk_move(99.98, 100.0), "flat")

    def test_download_after_a_purge_counts_as_progress(self):
        """issue #12 の再現。134.3GB から 53.7GB へ落ちたあとの 3.4GB を拾う。"""
        used = 134.3
        self.assertEqual(watch._disk_move(53.7, used), "shrank")
        used = 53.7  # 基準を取り直す
        # 素の HTTP は 4〜29MB/s。30秒ごとの確認なら 1回で 0.12GB 以上は伸びるので、
        # MOVE_GB(0.05GB)を超えて grew に入る
        self.assertEqual(watch._disk_move(53.82, used), "grew")
        self.assertEqual(watch._disk_move(57.1, used), "grew")


if __name__ == "__main__":
    unittest.main()
