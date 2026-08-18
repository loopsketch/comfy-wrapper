"""cw — 呼ぶ側から「リポジトリの場所・compose のサービス名・コンテナ内のパス」を隠す。

    uv tool install --editable /path/to/comfy-wrapper
    cw image "a pug on a sunlit windowsill" --model z-image --out ./hero.png

引き受けるのは振り分けだけで、生成の中身も運用の手順もここには書かない。

- **生成系** (`image` / `video` / `post` / `measure` / `jobs` / `models`) は
  `src/scripts/` の `main()` をこのプロセスで呼ぶ。サブプロセスを挟まないので、
  エラーもトレースバックもそのまま出る。パスは CWD 相対のまま通る。
- **運用系** (`up` / `run` / `stop` / `status` / `sessions` / `watch` / `auth` /
  `tunnel` / `key`) は
  `cwd=<リポジトリ>` で既存の `src/scripts/*.sh` を実行する。**`docker compose` の
  露出はここで止める。**

リポジトリの場所は `__file__` の2つ上から出す (editable install なのでソースツリーを
指す)。`COMFY_WRAPPER_HOME` で上書きできる。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import unicodedata
from pathlib import Path


def repo_home() -> Path:
    """このリポジトリの場所。`src/cli/main.py` の2つ上。"""
    override = os.environ.get("COMFY_WRAPPER_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


REPO = repo_home()
SCRIPTS = REPO / "src" / "scripts"

# 手元側の共有層 (宛先・キー・単価・障害の切り分け) はリポジトリのものを使う。
# **cw 側に写さない。** 同じ判定を2か所に置くと必ずずれる
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

try:
    from lib import colab_link
except ImportError as exc:  # pragma: no cover - 置き場所が壊れているときだけ
    raise SystemExit(
        f"comfy-wrapper のソースが {REPO} に見つかりません ({exc})。\n"
        "COMFY_WRAPPER_HOME にリポジトリの場所を指すか、"
        "uv tool install --editable /path/to/comfy-wrapper で入れ直してください"
    ) from exc


USAGE = """\
cw — Colab の GPU 上の ComfyUI に生成を頼む

生成 (ホストの python で直接動く。パスは CWD 相対):
  cw image <プロンプト> [--model z-image] [--ref X] [--out ./a.png]
  cw video [先頭フレーム] [--prompt "..."] [--model ltx-2.5] [--out ./a.mp4]
  cw post <動画> [--size 4k] [--multiplier 2]
  cw measure submit|status|report ...      生成時間の測定
  cw jobs                                  投入済みを回収する
  cw models                                どのモデルで何ができるか

運用 (内側で docker compose を呼ぶ):
  cw up [--setup image] [--models z-image] [--gpu L4] [--max 60]
  cw run [同上] -- <スクリプト...>         確保 -> 構築 -> 実行 -> 停止 を無人で
  cw status                                compose / セッション / 見張り / 疎通
  cw sessions                              サーバに現物を問い合わせる
  cw watch [セッション] [上限分]           見張りの開始・状態・停止
  cw auth [login [--code <code>]]          Colab の認証を見る・入れ直す
  cw tunnel up|restart|stop|logs           トンネルだけを扱う (セッションは触らない)
  cw stop                                  ランタイムとトンネルを畳む
  cw key issue|list|revoke|push            アクセスキー

各コマンドの詳しい引数は `cw <コマンド> --help` で出ます。
GPU は稼働時間で課金されます。**使い終わったら cw stop まで含めて片づけてください。**
"""


# --- 出力の小物 -------------------------------------------------------------


def _width(text: str) -> int:
    """端末での表示幅。日本語の見出しを混ぜても表が崩れないようにする。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [max([_width(h)] + [_width(r[i]) for r in rows]) for i, h in enumerate(headers)]
    lines = ["  ".join(_pad(h, w) for h, w in zip(headers, widths)).rstrip()]
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append("  ".join(_pad(c, w) for c, w in zip(row, widths)).rstrip())
    return "\n".join(lines)


def _section(title: str) -> None:
    print(f"\n-- {title}", flush=True)


# --- 生成系: src/scripts/ の main() をこのプロセスで呼ぶ ---------------------


def _load_script(name: str):
    path = SCRIPTS / f"{name}.py"
    if not path.exists():
        raise SystemExit(
            f"スクリプトがありません: {path}\n"
            "COMFY_WRAPPER_HOME が別のディレクトリを指していないか確認してください"
        )
    spec = importlib.util.spec_from_file_location(f"cw_script_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _call_script(name: str, prog: str, argv: list[str]) -> int:
    """スクリプトの main() を、argv を差し替えて呼ぶ。

    サブプロセスにしないのは、**失敗をそのまま見せる**ため。間に1枚挟むと
    トレースバックが消え、次の一手が分からなくなる。argparse の usage に出る
    名前も `cw image` のように見せたいので argv[0] ごと置き換える。
    """
    module = _load_script(name)
    saved = sys.argv
    sys.argv = [prog, *argv]
    try:
        return module.main() or 0
    finally:
        sys.argv = saved


def _submit(argv: list[str]) -> list[str]:
    """`submit` を補う。help に出る usage が `cw image submit ...` になるので、
    そう打たれても通るようにしておく (打たなくても通る)。"""
    return argv if argv[:1] == ["submit"] else ["submit", *argv]


def cmd_image(argv: list[str]) -> int:
    return _call_script("generate_image", "cw image", _submit(argv))


def cmd_video(argv: list[str]) -> int:
    # `cw video ./hero.png` を i2v として受ける。先頭が オプションでなければ画像とみなす
    if argv and not argv[0].startswith("-"):
        argv = ["--first-frame", argv[0], *argv[1:]]
    return _call_script("generate_video", "cw video", argv)


def cmd_post(argv: list[str]) -> int:
    return _call_script("postprocess", "cw post", _submit(argv))


def cmd_measure(argv: list[str]) -> int:
    return _call_script("measure_video", "cw measure", argv)


def cmd_jobs(argv: list[str]) -> int:
    """投入済みを回収する。静止画と仕上げの台帳をまとめて見る。"""
    if argv:
        raise SystemExit("cw jobs は引数を取りません")
    rc = _call_script("generate_image", "cw jobs", ["status"])
    # 仕上げは使っていないことの方が多い。台帳が無ければ黙って飛ばす
    if (colab_link.JOBS_DIR / "postprocess.json").exists():
        _section("仕上げ")
        rc = _call_script("postprocess", "cw jobs", ["status"]) or rc
    return rc


# --- モデル一覧 -------------------------------------------------------------


def _fetch_catalog() -> tuple[list[dict], bool, str]:
    """`GET /v1/models` を引く。届かなければ手元のカタログで代替する。

    **見積もりは GPU を確保する前に出せないと意味がない。** ランタイムが無い状態でも
    表そのものは答えられるよう、落ちたらリポジトリ内の model_catalog へ落とす
    (`ready` だけが分からなくなる)。
    """
    endpoint = colab_link.read_endpoint()
    try:
        key = colab_link.require_api_key()
        _, body = colab_link.request(endpoint, key, "GET", "/v1/models")
        data = json.loads(body)
        return data["models"], data.get("ready_known", False), ""
    except (colab_link.LinkError, json.JSONDecodeError, KeyError) as exc:
        from lib import model_catalog

        return model_catalog.catalog(), False, str(exc)


def cmd_models(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="cw models", description="どのモデルで何ができるか")
    parser.add_argument("--gpu", default="L4", choices=list(colab_link.GPU_CU_PER_HOUR),
                        help="コスト換算に使う GPU (既定 L4)")
    parser.add_argument("--json", action="store_true", help="生の JSON を出す")
    args = parser.parse_args(argv)

    entries, ready_known, note = _fetch_catalog()
    if args.json:
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return 0

    yen_h = colab_link.yen_per_hour(args.gpu)

    def ready(entry: dict) -> str:
        if not ready_known:
            return "-"
        return {True: "○", False: "×"}.get(entry.get("ready"), "-")

    videos = [e for e in entries if e["kind"] == "video"]
    rows = []
    for e in sorted(videos, key=lambda e: e["id"]):
        rate = e.get("seconds_per_output_second") or {}
        sec = rate.get(args.gpu)
        speed = "-" if sec is None else f"{sec:.1f} ({'実測' if rate.get('measured') else '概算'})"
        cost = "-" if sec is None else f"{sec * yen_h / 3600:.2f}"
        rows.append([
            e["id"], ready(e), ",".join(e.get("tasks", [])),
            "○" if e.get("last_frame") else "-",
            "○" if e.get("audio_out") else "-",
            str(e.get("ref_images") or "-"),
            str(e.get("fps", {}).get("default", "-")),
            f"{e.get('weights_gb', 0):.1f}GB",
            speed, cost,
        ])
    print(_table(
        ["モデル", "用意", "タスク", "末尾", "音声", "参照", "fps", "ウェイト",
         f"{args.gpu} 秒/映像秒", "円/映像秒"],
        rows,
    ))

    images = [e for e in entries if e["kind"] == "image"]
    rows = []
    for e in sorted(images, key=lambda e: e["id"]):
        sec = (e.get("seconds_per_image") or {}).get(args.gpu)
        rows.append([
            e["id"], ready(e), str(e.get("ref_images") or "-"),
            f"{e.get('weights_gb', 0):.1f}GB",
            "-" if sec is None else f"{sec:.0f}",
            "-" if sec is None else f"{sec * yen_h / 3600:.2f}",
            e.get("notes", ""),
        ])
    print()
    print(_table(
        ["モデル", "用意", "参照", "ウェイト", f"{args.gpu} 秒/枚", "円/枚", "備考"],
        rows,
    ))

    print(f"\n単価は {args.gpu} の {yen_h:.1f}円/時 から。**枚数でも本数でもなく "
          "GPU の占有時間で課金されます**")
    if not ready_known:
        print("「用意」はランタイムに問い合わせられませんでした"
              + (f" ({note})" if note else ""))
    elif any(e.get("ready") is None for e in entries):
        # 静止画モデルの ready は /health が持っていない。○ でも × でもない理由を書く
        print("「-」はランタイムからは分からない項目です (静止画モデルの用意は "
              "/health に無いので、cw image が通るかで見てください)")
    return 0


# --- 運用系: 既存の *.sh を cwd=<リポジトリ> で実行する ---------------------


def _sh(script: str, *args: str) -> int:
    path = SCRIPTS / script
    if not path.exists():
        raise SystemExit(f"スクリプトがありません: {path}")
    cmd = [str(path)] if os.access(path, os.X_OK) else ["bash", str(path)]
    # 子の出力が先に出ると見出しと中身が入れ替わる (パイプに繋ぐと親は行バッファでない)
    sys.stdout.flush()
    return subprocess.run([*cmd, *args], cwd=REPO).returncode


def _docker(*args: str, quiet: bool = False) -> int:
    sys.stdout.flush()
    hush = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL} if quiet else {}
    try:
        return subprocess.run(["docker", *args], cwd=REPO, **hush).returncode
    except FileNotFoundError:
        print("docker がありません。運用系のコマンドには Docker と Compose v2 が要ります",
              file=sys.stderr)
        return 127


def _colab_exec(*args: str) -> int:
    """colab コンテナで python を動かす。**ここは compose 経由のまま。**

    google-auth と colab_cli はこのコンテナにしか入っていない。ホスト側に入れると
    トークンの置き場が2つになるので、認証まわりは常にコンテナの中で完結させる。
    """
    rc = _docker("compose", "up", "-d", "colab", quiet=True)
    if rc != 0:
        return rc
    return _docker("compose", "exec", "-T", "colab", "python", *args)


def cmd_up(argv: list[str]) -> int:
    """確保 -> 構築 -> トンネルまでを流して、セッションを残す。

    中身は colab_run.sh そのもの。**判定も待ちもここで書き直さない。** 実行する
    ものが無いので、-- のあとには何もしない python を渡す。
    """
    if "--" in argv:
        raise SystemExit("cw up は作業を取りません。まとめて1本流すなら cw run を使ってください")
    rc = _sh("colab_run.sh", *argv, "--keep", "--",
             "-c", "print('生成を受け付けられます')")
    if rc == 0:
        print("\n生成できます:  cw image \"...\" --out ./a.png")
        print("止めるとき  :  cw stop      **GPU は稼働時間で課金されます**")
    return rc


def cmd_run(argv: list[str]) -> int:
    if not argv:
        raise SystemExit(
            "実行するものを -- のあとに書いてください。例:\n"
            "  cw run --setup video --models ltx-2.5 --max 60 -- "
            "src/scripts/generate_video.py --prompt \"...\""
        )
    return _sh("colab_run.sh", *argv)


def cmd_tunnel(argv: list[str]) -> int:
    """トンネルだけを扱う。**セッションは触らない。**

    セッションは生きているのに手元へ届かない、という状態はよく起きる (ssh が
    落ちた・ランタイムの I/O が詰まった)。ここで確保し直すと、生きているものを
    捨ててもう一度 GPU を掴むことになる。
    """
    action = argv[0] if argv else "restart"
    if action not in ("up", "restart", "stop", "logs"):
        raise SystemExit("cw tunnel up | restart | stop | logs")
    if action == "logs":
        return _docker("compose", "logs", "--tail", "50", "tunnel")
    if action == "up":
        return _docker("compose", "up", "-d", "tunnel")
    return _docker("compose", action, "tunnel")


def cmd_sessions(argv: list[str]) -> int:
    return _sh("colab.sh", "sessions", *argv)


def cmd_watch(argv: list[str]) -> int:
    return _sh("colab_watch.sh", *argv)


def cmd_stop(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="cw stop", description="ランタイムとトンネルを畳む")
    parser.add_argument("-s", "--session", default="comfy")
    args = parser.parse_args(argv)

    _section("見張りを止めます")
    _sh("colab_watch.sh", "--stop")
    _section("セッションを止めます")
    rc = _sh("colab.sh", "stop", "-s", args.session)
    # **台帳から消えることと、リモートが止まることは別。** 実体が生きていれば
    # 課金が続くので、止めたあとに必ずサーバへ問い合わせる
    _section("サーバ側に残っていないか確認します")
    _sh("colab.sh", "sessions")
    _section("トンネルを畳みます")
    _docker("compose", "stop", "tunnel")
    return rc


def cmd_status(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="cw status", description="いまの状態を1画面にまとめる")
    parser.add_argument("-s", "--session", default="comfy")
    args = parser.parse_args(argv)

    endpoint = colab_link.read_endpoint()
    print(f"リポジトリ: {REPO}")
    print(f"宛先      : {endpoint}", flush=True)

    _section("compose")
    _docker("compose", "ps")

    _section("Colab セッション (サーバへ問い合わせ)")
    _sh("colab.sh", "sessions")

    _section("見張り")
    _sh("colab_watch.sh", "--status", args.session)

    _section("疎通")
    # **判定を書き直さない。** 落ちている理由の名指しは colab_link.diagnose() が持つ
    try:
        health = colab_link.health(endpoint)
    except colab_link.LinkError as exc:
        print(exc)
        return 1
    ready = health.get("video_ready") or {}
    print(f"ComfyUI     : {'応答あり' if health.get('comfy_ready') else 'まだ応答しません'}")
    # **動画ウェイトと書く。** /health が持っているのは動画モデルだけで、静止画は
    # ここに出てこない。「載っている」と書くと、z-image で生成できている最中でも
    # 「何も載っていない」と読めてしまう
    loaded = [name for name, ok in ready.items() if ok]
    print(f"動画ウェイト: {', '.join(loaded) if loaded else '(なし)'}")
    print("静止画モデルの用意は /health では分かりません (cw image が通れば載っています)")
    return 0


AUTH_USAGE = """\
cw auth                        いまの認証を見る (期限が近ければ延長する)
cw auth login                  認可 URL を出す
cw auth login --code <code>    ブラウザに出たコードで認証を通す

**対話端末に入らなくても通せます。** URL を開いて、出てきたコードを --code に渡す
だけです。認証が切れている間はランタイムの確認も停止もできないので、通ったあとは
cw sessions で**止めたつもりのものが動いていないか**を必ず見てください。
"""


def cmd_auth(argv: list[str]) -> int:
    """認証を見る・入れ直す。中身は colab コンテナの colab_auth.py。

    **`colab` コマンドを叩かせない。** colab-cli の再認証は `input()` で標準入力を
    待つので、そこへ落ちると人が端末に入るまで止まる。URL を出す側とコードを渡す側を
    分けてあるのはそのため。
    """
    if argv and argv[0] in ("-h", "--help", "help"):
        print(AUTH_USAGE, end="")
        return 0

    script = "/app/src/scripts/colab_auth.py"
    if not argv or argv[0] != "login":
        return _colab_exec(script, *argv)

    parser = argparse.ArgumentParser(prog="cw auth login", add_help=False)
    parser.add_argument("--url", action="store_true")
    parser.add_argument("--code")
    args = parser.parse_args(argv[1:])

    if not args.code:
        print("次の URL をブラウザで開き、承認後に出るコードを控えてください。\n")
        rc = _colab_exec(script, "login", "--url")
        if rc == 0:
            print("\nコードが出たら:  cw auth login --code <code>")
        return rc

    rc = _colab_exec(script, "login", "--code", args.code)
    if rc == 0:
        # **切れている間は問い合わせができない。** 止めたつもりのランタイムが
        # 動いたまま課金され続けていることがあるので、通った直後に現物を見る
        _section("ランタイムが残っていないか確認します")
        _sh("colab.sh", "sessions")
    return rc


def cmd_key(argv: list[str]) -> int:
    """アクセスキーの発行・一覧・失効と、ランタイムへの反映。

    **平文はリポジトリの .colab/ にしか置かない。** Colab へ渡るのは SHA-256 の
    ハッシュだけなので、発行と反映は別の操作になる。
    """
    parser = argparse.ArgumentParser(prog="cw key", add_help=False)
    parser.add_argument("action", nargs="?",
                        choices=["issue", "list", "revoke", "push"])
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    if not argv or argv[0] in ("-h", "--help"):
        print("cw key issue --name <用途>   発行する (平文はこの時だけ出る)\n"
              "cw key list                  発行済みを見る\n"
              "cw key revoke --id <id>      失効させる\n"
              "cw key push [-s セッション]  ハッシュをランタイムへ送る\n\n"
              f"キーストア: {colab_link.COLAB_DIR / 'comfy-keys.json'}")
        return 0
    args = parser.parse_args(argv)

    if args.action == "push":
        session = "comfy"
        if args.rest and args.rest[0] in ("-s", "--session"):
            session = args.rest[1]
        elif args.rest:
            session = args.rest[0]
        return _sh("colab_key.sh", session)

    store = colab_link.COLAB_DIR / "comfy-keys.json"
    store.parent.mkdir(parents=True, exist_ok=True)
    rc = _call_script(
        "genkey", "cw key", ["--keys", str(store), args.action, *args.rest]
    )
    if rc == 0 and args.action in ("issue", "revoke"):
        print("\nランタイムへ反映するには: cw key push")
    return rc


# --- 振り分け ---------------------------------------------------------------


COMMANDS = {
    "image": cmd_image,
    "video": cmd_video,
    "post": cmd_post,
    "measure": cmd_measure,
    "jobs": cmd_jobs,
    "models": cmd_models,
    "up": cmd_up,
    "run": cmd_run,
    "status": cmd_status,
    "sessions": cmd_sessions,
    "watch": cmd_watch,
    "auth": cmd_auth,
    "tunnel": cmd_tunnel,
    "stop": cmd_stop,
    "key": cmd_key,
}


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("comfy-wrapper")
    except Exception:  # pragma: no cover - 未インストールで直接動かしたとき
        return "(未インストール)"


def main(argv: list[str] | None = None) -> int:
    """コマンド名だけを見て、残りはそのまま渡す。

    **cw 側でオプションを解釈しない。** 既存のスクリプトと同じ引数をそのまま書ける
    ようにするため、そして `cw run ... -- <作業>` の `--` を落とさないため
    (argparse は最初の `--` を食べてしまう)。
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE, end="")
        return 0
    if argv[0] in ("-V", "--version"):
        print(f"comfy-wrapper {_version()} ({REPO})")
        return 0

    command, rest = argv[0], argv[1:]
    handler = COMMANDS.get(command)
    if handler is None:
        print(f"cw: 知らないコマンドです: {command}\n", file=sys.stderr)
        print(USAGE, end="", file=sys.stderr)
        return 2
    return handler(rest)


if __name__ == "__main__":
    raise SystemExit(main())
