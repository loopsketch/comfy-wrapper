"""mp4 のヘッダ読み。仕上げの見積もりはここが正しいかにそのまま乗る。

寸法・fps・フレーム数を取り違えると、補間後のフレーム数も拡大の中間サイズも RAM も
全部ずれる。**それが分かるのは GPU を確保して投入したあと**なので、ここで落とす。

box は手で組み立てる。実ファイルを置くとリポジトリが重くなるうえ、どのバイトが
効いているのかがテストから読めなくなる。ffprobe があるときは実ファイルとの
突き合わせも行う (無い環境ではその分だけ飛ばす)。
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import _bootstrap  # noqa: F401

from lib import mp4_probe

ROOT = Path(_bootstrap.ROOT)


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload) + 8) + kind + payload


def visual_sample_entry(width: int, height: int) -> bytes:
    body = b"\x00" * 6 + struct.pack(">H", 1)          # reserved + data_reference_index
    body += b"\x00" * 16                                # pre_defined / reserved
    body += struct.pack(">HH", width, height)
    body += b"\x00" * 50                                # 以降はここでは読まない
    return box(b"avc1", body)


def stsd(width: int, height: int) -> bytes:
    return box(b"stsd", struct.pack(">II", 0, 1) + visual_sample_entry(width, height))


def stts(runs: list[tuple[int, int]]) -> bytes:
    body = struct.pack(">II", 0, len(runs))
    for samples, delta in runs:
        body += struct.pack(">II", samples, delta)
    return box(b"stts", body)


def mdhd(timescale: int, duration: int, version: int = 0) -> bytes:
    if version == 1:
        body = struct.pack(">I", 1 << 24) + b"\x00" * 16
        body += struct.pack(">IQ", timescale, duration)
    else:
        body = struct.pack(">I", 0) + b"\x00" * 8
        body += struct.pack(">II", timescale, duration)
    return box(b"mdhd", body + b"\x00" * 4)


def hdlr(kind: bytes) -> bytes:
    return box(b"hdlr", struct.pack(">II", 0, 0) + kind + b"\x00" * 12)


def trak(handler: bytes, *, width=640, height=360, timescale=24000,
         duration=48000, runs=((48, 1000),), mdhd_version=0) -> bytes:
    stbl = box(b"stbl", stsd(width, height) + stts(list(runs)))
    mdia = box(b"mdia", mdhd(timescale, duration, mdhd_version) + hdlr(handler)
               + box(b"minf", stbl))
    return box(b"trak", mdia)


def mp4(*traks: bytes, ftyp: bytes = b"isom") -> bytes:
    return box(b"ftyp", ftyp + b"\x00" * 8) + box(b"moov", b"".join(traks))


class ProbeTest(unittest.TestCase):
    def _probe(self, data: bytes) -> dict:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.mp4"
            path.write_bytes(data)
            return mp4_probe.probe(path)

    def test_reads_the_four_values(self):
        got = self._probe(mp4(trak(b"vide")))
        self.assertEqual((got["width"], got["height"]), (640, 360))
        self.assertEqual(got["frames"], 48)
        self.assertAlmostEqual(got["fps"], 24.0)
        self.assertAlmostEqual(got["duration"], 2.0)

    def test_picks_the_video_track(self):
        """音声つきのクリップで、音声の trak を映像と取り違えないこと。"""
        audio = trak(b"soun", width=0, height=0, timescale=48000,
                     duration=96000, runs=((2000, 1024),))
        got = self._probe(mp4(audio, trak(b"vide")))
        self.assertEqual((got["width"], got["height"]), (640, 360))
        self.assertEqual(got["frames"], 48)

    def test_sums_every_stts_run(self):
        """可変フレームレートだと stts が複数に割れる。1つ目だけ見ると数を取りこぼす。"""
        got = self._probe(mp4(trak(b"vide", runs=((10, 1000), (30, 1000)))))
        self.assertEqual(got["frames"], 40)

    def test_mdhd_version_1(self):
        """長いクリップは 64bit の時刻と尺で書かれる。読む位置が version で変わる。"""
        got = self._probe(mp4(trak(b"vide", mdhd_version=1)))
        self.assertAlmostEqual(got["fps"], 24.0)
        self.assertAlmostEqual(got["duration"], 2.0)
        self.assertEqual(got["frames"], 48)

    def test_64bit_box_size(self):
        """大きいクリップの box は 64bit 長で書かれる。size==1 を読み飛ばせること。"""
        data = mp4(trak(b"vide"))
        moov = data[data.index(b"moov") - 4:]
        body = moov[8:]
        large = struct.pack(">I", 1) + b"moov" + struct.pack(">Q", len(body) + 16) + body
        got = self._probe(data[:data.index(b"moov") - 4] + large)
        self.assertEqual(got["frames"], 48)


class UnsupportedTest(unittest.TestCase):
    """**読めないものは読めないと言う。** 黙って既定値で代用すると見積もりが狂う。"""

    def _expect(self, data: bytes, needle: str):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.bin"
            path.write_bytes(data)
            with self.assertRaises(mp4_probe.Unsupported) as cm:
                mp4_probe.probe(path)
        self.assertIn(needle, str(cm.exception))

    def test_not_iso_bmff(self):
        self._expect(b"\x1a\x45\xdf\xa3" + b"\x00" * 64, "ISO-BMFF")

    def test_no_video_track(self):
        self._expect(mp4(trak(b"soun")), "映像トラック")

    def test_fragmented_mp4_is_named(self):
        """断片化された mp4 は stts が空。フレーム数を 0 と答えてはいけない。"""
        self._expect(mp4(trak(b"vide", runs=())), "断片化")

    def test_truncated_file(self):
        self._expect(box(b"ftyp", b"isom" + b"\x00" * 8), "moov")


class AgainstFfprobeTest(unittest.TestCase):
    """手元にある実ファイルで ffprobe と突き合わせる。両方ある環境でだけ動く。"""

    def test_matches_ffprobe(self):
        if not shutil.which("ffprobe"):
            self.skipTest("ffprobe が無い")
        clips = sorted(ROOT.glob("works/*.mp4")) + sorted(ROOT.glob("works/.verify/*.mp4"))
        if not clips:
            self.skipTest("突き合わせる mp4 が無い")
        for clip in clips[:6]:
            with self.subTest(clip=clip.name):
                out = subprocess.run(
                    ["ffprobe", "-v", "error", "-select_streams", "v:0",
                     "-show_entries", "stream=width,height,r_frame_rate,nb_frames",
                     "-of", "json", str(clip)],
                    capture_output=True, text=True, check=True,
                )
                want = json.loads(out.stdout)["streams"][0]
                got = mp4_probe.probe(clip)
                self.assertEqual(got["width"], int(want["width"]))
                self.assertEqual(got["height"], int(want["height"]))
                self.assertEqual(got["frames"], int(want["nb_frames"]))
                num, den = want["r_frame_rate"].split("/")
                self.assertAlmostEqual(got["fps"], float(num) / float(den), places=3)


if __name__ == "__main__":
    unittest.main()
