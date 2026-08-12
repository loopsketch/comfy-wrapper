"""止まったら殺して再開する取得。20GB 級のウェイトを落とすための共通部品。

huggingface_hub 1.x の既定の転送経路 **Xet は速いが、無応答のまま固まることがある**。
21GB のファイルの 84% で停止し、8回とも同じ形になった。py-spy で見ると
`xet_get()` の中で待ち続けている(2026-08-09)。

    Thread (idle): "MainThread"
        xet_get (huggingface_hub/file_download.py:563)
        hf_hub_download (huggingface_hub/file_download.py:992)

**Xet は Python の HTTP 層を通らないので `HF_HUB_DOWNLOAD_TIMEOUT` が効かない。**
例外が上がらないため、呼び出し側の `try/except` によるリトライにも入れない。

`HF_HUB_DISABLE_XET=1` で素の HTTP に落とせばタイムアウトは効くようになるが、
実測で 469MB/s が 4〜29MB/s まで落ちた。42.5GB 一式で 2分 が 1時間超になり、
GPU の時間課金では成立しない。

そこで **Xet のまま取り、外から無進捗を見張って殺す**。書きかけの増え方を見て、
一定時間伸びなければ子プロセスを落として取り直す。書きかけは残るので続きから再開する。
最後の1回だけは Xet を切って挑む(遅いが確実)。
"""

from __future__ import annotations

import multiprocessing as mp
import os
import time
from pathlib import Path

# これだけ書きかけが伸びなければ「固まった」と見なす
STALL_SECONDS = float(os.environ.get("CW_DOWNLOAD_STALL_SECONDS", "180"))
# 何回まで取り直すか。最後の1回は Xet を切る
ATTEMPTS = int(os.environ.get("CW_DOWNLOAD_ATTEMPTS", "5"))
# 進捗と見なす最小の増分。ゆらぎで誤検知しないため
MIN_GROWTH = 5 * 1024 ** 2
POLL_SECONDS = 10.0

# 書きかけの置き場。local_dir を使うかどうかで変わるので両方見る
INCOMPLETE_ROOTS = (
    "/root/.cache/huggingface",
    str(Path.home() / ".cache" / "huggingface"),
)


def partials(roots) -> list[Path]:
    """書きかけファイルの一覧。取れないディレクトリは黙って飛ばす。"""
    out: list[Path] = []
    for root in roots:
        d = Path(root)
        try:
            if not d.exists():
                continue
            out += [f for f in d.rglob("*.incomplete") if f.is_file()]
        except OSError:
            continue
    return out


def incomplete_bytes(roots) -> int:
    """書きかけファイルの合計サイズ。取れないものは 0 として無視する。"""
    total = 0
    for f in partials(roots):
        try:
            total += f.stat().st_size
        except OSError:
            continue
    return total


def purge(roots, log, why: str, older_than: float | None = None) -> int:
    """書きかけを捨てる。捨てたバイト数を返す。

    **殺して取り直すと、書きかけが別名で作られることがある。** Xet と素の HTTP は
    一時ファイル名の付け方が違うため、経路が変わると前の書きかけは再開に使われず
    そのまま残る。21GB 級が数本残るとディスクを食い潰し、次のファイルが
    `No space left on device` で落ちる(2026-08-09 に実際に踏んだ。書きかけが 49.9GB
    まで積み上がった)。**再開に使えない書きかけは、その場で捨てる。**

    older_than を渡すと、それより古いものだけを消す(直前の試行で書いていた分は
    再開に使えるので残す)。
    """
    freed = 0
    for f in partials(roots):
        try:
            st = f.stat()
            if older_than is not None and st.st_mtime >= older_than:
                continue
            freed += st.st_size
            f.unlink()
        except OSError:
            continue
    if freed:
        log(f"[purge] 書きかけ {freed / 1024 ** 3:.2f}GB を捨てた ({why})")
    return freed


def _child(repo: str, filename: str, local_dir: str | None, disable_xet: bool, q) -> None:
    if disable_xet:
        os.environ["HF_HUB_DISABLE_XET"] = "1"
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")
    try:
        from huggingface_hub import hf_hub_download

        kwargs = {"repo_id": repo, "filename": filename}
        if local_dir:
            kwargs["local_dir"] = local_dir
        q.put(("ok", hf_hub_download(**kwargs)))
    except Exception as e:  # 子の例外は親へ文字列で渡す
        q.put(("err", f"{type(e).__name__}: {e}"))


def download(
    repo: str,
    filename: str,
    local_dir: Path | None = None,
    watch_roots=None,
    log=print,
) -> str:
    """1ファイルを取る。固まったら殺して取り直す。最後は Xet を切って挑む。

    戻り値は落ちたファイルのパス。すべて失敗したら最後の例外を上げる。
    """
    roots = list(watch_roots or INCOMPLETE_ROOTS)
    if local_dir:  # local_dir を使うと書きかけはその下に置かれる
        roots.append(str(Path(local_dir) / ".cache" / "huggingface"))

    last_error = "原因不明"
    attempt_started = time.time()
    for attempt in range(1, ATTEMPTS + 1):
        disable_xet = attempt == ATTEMPTS  # 最後の1回は遅くても確実な経路で
        if disable_xet:
            log(f"[fallback] Xet を切って取り直す ({attempt}/{ATTEMPTS})")
            # 経路が変わると書きかけは再利用されない。残すとディスクを食うだけ
            purge(roots, log, "Xet から素の HTTP へ切り替えるので再開できない")
        elif attempt > 1:
            # 前の試行より古い書きかけは、もう再開に使われない孤児
            purge(roots, log, "前の試行より古い", older_than=attempt_started)
        attempt_started = time.time()

        q: mp.Queue = mp.Queue()
        proc = mp.Process(
            target=_child,
            args=(repo, filename, str(local_dir) if local_dir else None, disable_xet, q),
        )
        proc.start()

        seen = incomplete_bytes(roots)
        moved_at = time.time()
        stalled = False
        while proc.is_alive():
            time.sleep(POLL_SECONDS)
            now = incomplete_bytes(roots)
            if now > seen + MIN_GROWTH:
                seen, moved_at = now, time.time()
            elif time.time() - moved_at > STALL_SECONDS:
                log(f"[stall] {STALL_SECONDS:.0f}秒 進まないので殺して取り直す "
                    f"({attempt}/{ATTEMPTS}, 書きかけ {now / 1024 ** 3:.2f}GB)")
                proc.kill()
                proc.join(timeout=30)
                stalled = True
                break

        if stalled:
            last_error = "無応答"
            continue

        proc.join(timeout=30)
        if q.empty():
            last_error = f"子プロセスが結果を返さず終了 (exitcode={proc.exitcode})"
            log(f"[retry] {last_error} ({attempt}/{ATTEMPTS})")
            continue

        kind, payload = q.get()
        if kind == "ok":
            return payload
        last_error = payload
        log(f"[retry] {payload} ({attempt}/{ATTEMPTS})")
        time.sleep(5)

    raise RuntimeError(f"{repo}/{filename} を {ATTEMPTS}回試して取得できませんでした: {last_error}")
