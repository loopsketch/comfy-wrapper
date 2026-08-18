"""mp4 のヘッダだけを読んで、寸法・fps・フレーム数を出す。

**仕上げ (postprocess) の見積もりに ffmpeg を要らなくするために在る。** 入力の
寸法・fps・フレーム数が分かれば、補間後のフレーム数も拡大の中間サイズも RAM も
計算できる。そのために ffprobe を1本入れさせるのは、Windows/WSL や最小構成の
ホストでは重い前提になる。

読むのは ISO-BMFF (mp4 / mov / m4v) だけで、**中身のデコードはしない**。必要な値は
すべて `moov` の中に平文で入っている。

- 寸法      : `stsd` の VisualSampleEntry (符号化された実寸。ffprobe の width/height と同じ)
- フレーム数: `stts` のサンプル数の合計
- fps       : `mdhd` の timescale ÷ サンプル1つあたりの尺

**読めないものは読めないと言う。** webm / mkv / avi、断片化された mp4 (`moof` に
サンプルが載るもの) は対象外なので `Unsupported` を上げ、呼ぶ側が ffprobe へ回せる
ようにする。黙って既定値で代用すると、見積もりが入力と噛み合わないまま投入されて
GPU 時間をそのまま捨てることになる。
"""

from __future__ import annotations

import struct
from pathlib import Path


class Unsupported(ValueError):
    """この実装では読めない。呼ぶ側は ffprobe へ回すこと。"""


# 一度に読む先頭のバイト数。moov が末尾にある mp4 もあるので、足りなければ全部読む
HEAD_BYTES = 1 << 20


def _boxes(data: bytes, start: int, end: int):
    """[start, end) にある box を (種類, 中身の開始, box の終わり) で返す。"""
    i = start
    while i + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, i)
        body = i + 8
        if size == 1:  # 64bit 長。size のあとに実際の長さが入る
            size = struct.unpack_from(">Q", data, i + 8)[0]
            body = i + 16
        elif size == 0:  # 最後まで
            size = end - i
        if size < 8 or i + size > end:
            return
        yield kind.decode("latin1"), body, i + size
        i += size


def _find(data: bytes, path: list[str], start: int, end: int):
    """入れ子の box を辿る。見つからなければ None。"""
    for kind, body, stop in _boxes(data, start, end):
        if kind != path[0]:
            continue
        if len(path) == 1:
            return body, stop
        # 中身の前に version+flags などが挟まる container がある
        skip = {"stsd": 8}.get(kind, 0)
        found = _find(data, path[1:], body + skip, stop)
        if found:
            return found
    return None


def _video_trak(data: bytes):
    """映像の trak を探す。音声つきのクリップでも映像を取り違えないため。"""
    moov = _find(data, ["moov"], 0, len(data))
    if not moov:
        raise Unsupported("moov がありません (mp4 ではないか、壊れています)")
    for kind, body, stop in _boxes(data, *moov):
        if kind != "trak":
            continue
        hdlr = _find(data, ["mdia", "hdlr"], body, stop)
        # hdlr: version+flags(4) + pre_defined(4) + handler_type(4)
        if hdlr and data[hdlr[0] + 8:hdlr[0] + 12] == b"vide":
            return body, stop
    raise Unsupported("映像トラックがありません")


def probe(path: Path | str) -> dict:
    """寸法・fps・フレーム数・尺を返す。読めなければ Unsupported。"""
    path = Path(path)
    data = path.read_bytes()
    if len(data) < 8 or data[4:8] not in (b"ftyp", b"moov", b"free", b"mdat", b"skip"):
        raise Unsupported(f"ISO-BMFF (mp4 / mov) ではありません: {path.name}")

    start, end = _video_trak(data)

    stsd = _find(data, ["mdia", "minf", "stbl", "stsd"], start, end)
    if not stsd:
        raise Unsupported("stsd がありません")
    entry = next(_boxes(data, stsd[0] + 8, stsd[1]), None)  # version+flags と entry_count を飛ばす
    if not entry:
        raise Unsupported("サンプルの記述がありません")
    # VisualSampleEntry: reserved(6) data_reference_index(2) pre_defined/reserved(16) width(2) height(2)
    width, height = struct.unpack_from(">HH", data, entry[1] + 24)

    mdhd = _find(data, ["mdia", "mdhd"], start, end)
    if not mdhd:
        raise Unsupported("mdhd がありません")
    if data[mdhd[0]] == 1:  # version 1 は 64bit の時刻と尺
        timescale, duration = struct.unpack_from(">IQ", data, mdhd[0] + 20)
    else:
        timescale, duration = struct.unpack_from(">II", data, mdhd[0] + 12)
    if not timescale:
        raise Unsupported("timescale が 0 です")

    stts = _find(data, ["mdia", "minf", "stbl", "stts"], start, end)
    if not stts:
        raise Unsupported("stts がありません")
    count = ticks = 0
    entries = struct.unpack_from(">I", data, stts[0] + 4)[0]
    for i in range(entries):
        samples, delta = struct.unpack_from(">II", data, stts[0] + 8 + i * 8)
        count += samples
        ticks += samples * delta
    if not count:
        # 断片化された mp4。サンプルは moof 側にあり、stts は空になる
        raise Unsupported("フレームが数えられません (断片化された mp4 の可能性)")

    return {
        "width": width,
        "height": height,
        "fps": timescale / (ticks / count),
        "frames": count,
        "duration": duration / timescale,
    }
