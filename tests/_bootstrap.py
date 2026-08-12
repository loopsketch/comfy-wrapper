"""テストから src/ と src/server/ を import できるようにする。

server/ の各モジュールは Colab 上で `sys.path` に server/ を入れて動かすので、
`from video_common import ...` のように隣を直接 import している。テストでも
同じ形で読めるように、両方をパスへ入れる。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

for path in (SRC, SRC / "server"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
