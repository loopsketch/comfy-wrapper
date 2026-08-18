"""止まったら殺して再開する取得。20GB 級のウェイトを落とすための共通部品。

huggingface_hub 1.x の既定の転送経路 **Xet は速いが、無応答のまま固まることがある**。
21GB のファイルの 84% で停止し、8回とも同じ形になった。py-spy で見ると
`xet_get()` の中で待ち続けている。

    Thread (idle): "MainThread"
        xet_get (huggingface_hub/file_download.py:563)
        hf_hub_download (huggingface_hub/file_download.py:992)

**Xet は Python の HTTP 層を通らないので `HF_HUB_DOWNLOAD_TIMEOUT` が効かない。**
例外が上がらないため、呼び出し側の `try/except` によるリトライにも入れない。

`HF_HUB_DISABLE_XET=1` で素の HTTP に落とせばタイムアウトは効くようになるが、
実測で 469MB/s が 4〜29MB/s まで落ちた。42.5GB 一式で 2分 が 1時間超になり、
GPU の時間課金では成立しない。

そこで **Xet のまま取り、外から無進捗を監視して殺す**。書きかけの増え方を見て、
一定時間伸びなければ子プロセスを落として取り直す。最後の1回だけは Xet を切って挑む
(遅いが確実)。

**書きかけからの再開は効かない。** 2026-08-18 に計測した。試行のたびに別名の
`.incomplete` が作られ、前の書きかけは一度も使われない。

    1/5  ...f3963829.959623b1.incomplete   10.69GB まで
    2/5  ...f3963829.36e4a1d0.incomplete   12.34GB まで
    3/5  ...f3963829.f0127b01.incomplete   17.76GB まで

前半の blob の SHA は同じで、後ろの乱数部分が毎回変わる。19.09GB の1ファイルに対して
書きかけが 30GB 積み上がり、空きを削るだけだった。**取り直しの前に捨てる。**

**同じ実体を2度数えない。** roots には同じディレクトリが並ぶことがあり(Colab は
HOME=/root なので INCOMPLETE_ROOTS の2つが一致する)、重複したまま数えて書きかけの
合計が実際の2倍に見えていた。上の計測はその二重計上を取り除いた値。

空きが MIN_FREE_GB を切ったら理由を書いて打ち切る。**取れないまま GPU の時間を
使い続けない。**
"""

from __future__ import annotations

import multiprocessing as mp
import os
import shutil
import time
from pathlib import Path

# これだけ書きかけが伸びなければ「固まった」と見なす
STALL_SECONDS = float(os.environ.get("CW_DOWNLOAD_STALL_SECONDS", "180"))
# 何回まで取り直すか。最後の1回は Xet を切る
ATTEMPTS = int(os.environ.get("CW_DOWNLOAD_ATTEMPTS", "5"))
# 進捗と見なす最小の増分。ゆらぎで誤検知しないため
MIN_GROWTH = 5 * 1024 ** 2
POLL_SECONDS = 10.0
# 空きがこれを切ったら取得を諦める(GB)。**取れないまま GPU 時間を使い続けない。**
# 19GB のファイルを取る間に書きかけが 57GB まで積み上がり、空き 64GB を
# 食い潰したことがある。詰まるほど書き込みは遅くなり、次の stall を呼ぶ
MIN_FREE_GB = float(os.environ.get("CW_DOWNLOAD_MIN_FREE_GB", "5"))

# 書きかけの置き場。local_dir を使うかどうかで変わるので両方見る
INCOMPLETE_ROOTS = (
    "/root/.cache/huggingface",
    str(Path.home() / ".cache" / "huggingface"),
)


def partials(roots) -> list[Path]:
    """書きかけファイルの一覧。取れないディレクトリは黙って飛ばす。

    **同じ実体は1度だけ返す。** INCOMPLETE_ROOTS は /root/.cache/huggingface と
    $HOME/.cache/huggingface を並べているが、Colab は HOME=/root なので同じ
    ディレクトリを指す。重複したまま数えて、書きかけの合計が実際の2倍に見えていた。
    """
    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        d = Path(root)
        try:
            if not d.exists():
                continue
            for f in d.rglob("*.incomplete"):
                if not f.is_file():
                    continue
                key = f.resolve()
                if key in seen:
                    continue
                seen.add(key)
                out.append(f)
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


def describe_partials(roots) -> str:
    """書きかけの内訳。**再開が効いているかは、本数と1本の大きさで分かる。**

    再開が効いていれば1本が伸びていく。取り直すたびに別名で作られていれば本数が増える。
    19GB のファイルに対して 20GB 級が3本ある、という形が見えたら、そのリトライは
    再開に使われていない。合計だけ見ていると「少しずつ伸びている」と読み違える。
    """
    sizes = []
    for f in partials(roots):
        try:
            sizes.append((f.name, f.stat().st_size))
        except OSError:
            continue
    if not sizes:
        return "書きかけ なし"
    sizes.sort(key=lambda x: -x[1])
    total = sum(size for _, size in sizes)
    head = ", ".join(f"{name[-28:]} {size / 1024 ** 3:.2f}GB" for name, size in sizes[:3])
    tail = f" ほか{len(sizes) - 3}本" if len(sizes) > 3 else ""
    return f"書きかけ {len(sizes)}本 合計 {total / 1024 ** 3:.2f}GB ({head}{tail})"


def free_gb(path) -> float | None:
    """path のファイルシステムの空き(GB)。取れなければ None。

    取得の途中は、まだ出来ていないディレクトリを指していることがある。
    存在する親までさかのぼって見る。**分からないことを 0 と混ぜない。**
    """
    d = Path(path)
    while not d.exists() and d != d.parent:
        d = d.parent
    try:
        return shutil.disk_usage(d).free / 1024 ** 3
    except OSError:
        return None


def purge(roots, log, why: str) -> int:
    """書きかけを捨てる。捨てたバイト数を返す。

    **殺して取り直すと、書きかけは別名で作られる。** 前の書きかけは再開に使われず
    そのまま残る。21GB 級が数本残るとディスクを食い潰し、次のファイルが
    `No space left on device` で落ちる。**再開に使えない書きかけは、その場で捨てる。**

    以前は「直前の試行の分は再開に使えるので残す」として mtime で選り分けていたが、
    2026-08-18 の計測で再開が効かないと分かったので、選り分けをやめた。
    """
    freed = 0
    for f in partials(roots):
        try:
            freed += f.stat().st_size
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
    # 空きを見る先。書きかけの置き場と同じファイルシステムを指していればよい
    disk = Path(local_dir) if local_dir else Path(roots[0])

    last_error = "原因不明"
    for attempt in range(1, ATTEMPTS + 1):
        disable_xet = attempt == ATTEMPTS  # 最後の1回は遅くても確実な経路で
        if disable_xet:
            log(f"[fallback] Xet を切って取り直す ({attempt}/{ATTEMPTS})")
            # 経路が変わると書きかけは再利用されない。残すとディスクを食うだけ
            purge(roots, log, "Xet から素の HTTP へ切り替えるので再開できない")
        elif attempt > 1:
            # **再開は効かないので、取り直しの前に捨てる。** 残しても次の試行は
            # 別名で最初から書き始める。空きを削り、詰まるほど書き込みが遅くなって
            # 次の stall を呼ぶだけだった
            purge(roots, log, "再開に使われないので取り直しの前に捨てる")
        # **試行の頭で内訳を残す。** 合計だけでは再開が効いているか分からない。
        # 前の試行の終わりの姿でもあるので、並べれば取り直しの効き方が読める
        log(f"[開始] {attempt}/{ATTEMPTS} {describe_partials(roots)}")

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
            # **空きが尽きるより先に諦める。** 詰まるほど書き込みは遅くなり、
            # 次の stall を呼ぶ。取れないまま GPU の時間を使い続けない
            free = free_gb(disk)
            if free is not None and free < MIN_FREE_GB:
                log(f"[中断] 空きが {free:.1f}GB まで減った "
                    f"(下限 {MIN_FREE_GB:.0f}GB)。{describe_partials(roots)}")
                proc.kill()
                proc.join(timeout=30)
                purge(roots, log, "空きが尽きたので取得を諦める")
                raise RuntimeError(
                    f"{repo}/{filename} の取得を中断しました: 空きが {free:.1f}GB まで"
                    f"減りました(下限 {MIN_FREE_GB:.0f}GB)。書きかけが積み上がって"
                    "再開に使われていない可能性があります"
                )
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
