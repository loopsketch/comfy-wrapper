"""ランタイムの監視。compose の `colab` 常駐コンテナの中で動かす。

起動・停止は colab_watch.sh から行う。見るのは 4 つ。

1. **keep-alive デーモンの生存**。`colab new` はこれを detached な子として起こすが、
   記録を残さず消えることがある。落ちていたら起こし直す。
2. **構築の進捗**。`/content` の使用量が伸びているかを見る。
3. **課金の打ち切り**。上限時間を超えたら、あるいは何もしていない状態が続いたら
   **自分でランタイムを止める**。
4. **OAuth の生存**。切れると `colab exec` が通らなくなり、2 も 3 も判断材料を失う。
   起動直後に1回、以降は COLAB_AUTH_CHECK_MINUTES ごとに延長を試す(colab_auth.py)。

3 が要る理由: GPU の稼働時間で課金されるのに、待ちに入ると誰も止めない。
**人が見ていない前提で設計する。**

4 が要る理由: 認証が切れると probe が何も返さなくなる。**監視が盲目になったことに
気づけないのが一番まずい。** ランタイムが生きていれば課金は続く。

    COLAB_MAX_MINUTES   確保からの上限(既定 30分)。超えたら止める
    COLAB_IDLE_MINUTES  何も進んでいない状態の許容(既定 8分)。超えたら止める
    COLAB_READY_IDLE_MINUTES 準備できてから最初の1件が来るまでの許容(既定 15分)
    COLAB_WATCH_INTERVAL 確認の間隔(既定 30秒)
    COLAB_AUTH_CHECK_MINUTES 認証を確認する間隔(既定 30分)。0 で無効
    COLAB_STOP_TRIES    停止を試す回数(既定 3)。**1回で諦めない**
    COLAB_STOP_TIMEOUT  停止1回あたりの待ち(既定 120秒)
    COLAB_RESCUE_DIRS   止める前に回収する Colab 側のディレクトリ(コロン区切り)

**止める前に成果物を回収する。** ランタイムを止めると /content は消えるので、
生成済みのものが残っていれば手元へ持ち帰ってから停止する。回収先は
works/.rescue/<セッション名>/<ディレクトリ名>/。

「何も進んでいない」と見なすのは、次のどれでもないとき。

- ディスクが伸びている                   … 取得中(書きかけの分も使用量に出る)
- ディスクが減った                       … 書きかけを捨てて取り直している
- ComfyUI のキューに積まれている        … 生成中
- ComfyUI / uvicorn のプロセスがある     … 起動中(H3 は 21GB を積むので数分かかる)

この4つを見ないと、取得直後の ComfyUI 起動待ちなどで誤って止めることになる。
**減ったときに基準を取り直さないと、捨てた分を埋め直すまで進捗と見なせない。**

準備できてから最初の1件が来るまでは、手元で produce を叩く時間が要るので許容を長めに
取る(COLAB_READY_IDLE_MINUTES)。1件でも通ったあとは通常の許容に戻す。
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# 同じ scripts/ に置いてある。監視はコンテナ内で直接動かすので普通に import できる
# (`colab exec -f` で単体送信されるスクリプトとは事情が違う)
import colab_auth

SESSIONS = Path("/app/.colab/.config/colab-cli/sessions.json")
LOG = Path("/app/.colab/keepalive-watch.log")
# 機械が読む状態。**ログの日本語を grep して制御を決めない。**
# 起動行の「アイドル 8分で自動停止」が停止判定にヒットして、構築開始15秒で
# 自分を止めた。人が読む文と機械が読む状態は別物にする。
STATE = Path("/app/.colab/watch-state.json")

MAX_MINUTES = float(os.environ.get("COLAB_MAX_MINUTES", "30"))
IDLE_MINUTES = float(os.environ.get("COLAB_IDLE_MINUTES", "8"))
READY_IDLE_MINUTES = float(os.environ.get("COLAB_READY_IDLE_MINUTES", "15"))
# 起動待ちで待てる上限。**待つ理由には必ず期限を付ける。**
BOOT_MINUTES = float(os.environ.get("COLAB_BOOT_MINUTES", "10"))
INTERVAL = float(os.environ.get("COLAB_WATCH_INTERVAL", "30"))
# 進捗と見なす最小の増減(GB)。ゆらぎで誤検知しないため
MOVE_GB = 0.05
# 認証を確認する間隔。**既定の上限(30分)だと実質「起動直後の1回」になる。**
# それでも実行前の検査として効くし、--max を長く取った回では周期チェックとして働く
AUTH_CHECK_MINUTES = float(os.environ.get("COLAB_AUTH_CHECK_MINUTES", "30"))
# 停止の粘り。**課金の打ち切りは1回で諦めない**
STOP_TRIES = int(os.environ.get("COLAB_STOP_TRIES", "3"))
STOP_TIMEOUT = float(os.environ.get("COLAB_STOP_TIMEOUT", "120"))
# 止める前に回収する Colab 側のディレクトリ。生成物の置き場を並べておく
# **/content/logs を必ず入れる。** ランタイムを止めると消えるため、5回失敗しても
# 取得エラーを一度も読めなかった。失敗の理由こそ持ち帰る価値がある
RESCUE_DIRS = [
    d for d in os.environ.get(
        "COLAB_RESCUE_DIRS",
        "/content/logs:/content/jobs",
    ).split(":") if d
]
# **ここだけ /app 固定でよい。** この監視は colab コンテナの中でしか動かない
# (colab_watch.sh が docker compose exec で起こす)。手元から直接叩く経路は無い
RESCUE_ROOT = Path("/app/works/.rescue")

# Colab 側で1回だけ実行して、進捗の材料をまとめて取る。
#
# 書きかけの合計も取る。**ただし使用量に足さない。** HF のキャッシュは /content と
# 同じファイルシステムに載るので、`.incomplete` が伸びれば使用量も同じだけ伸びる。
# 足すと同じバイトを2度数えることになり、速度表示が倍に振れていた
# (2026-08-18: 使用量 +0.2GB と書きかけ +0.2GB が並んで出ている)。
# 書きかけは、何をしている最中なのかを読むために別立てで持つ。
PROBE = """
import json, os, shutil, urllib.request
from pathlib import Path
t, u, f = shutil.disk_usage('/content')

# HF のキャッシュは /content と同じファイルシステムに載る。別ファイルシステムを疑って使用量を足したことがあるが、
# 二重計上になるだけで進捗の判定は変わらなかったので戻した。
#
# **書きかけの置き場は2種類ある。** download_image_models.py は素の
# hf_hub_download なので /root/.cache/huggingface に落ちるが、
# download_models.py(H3) は local_dir を渡すので ComfyUI/models/.cache に落ちる。
# 片方しか見ないと、H3 の取得中ずっと「書きかけ 0.0GB」に見える。
#
# **同じ実体を2度数えない。** 上の2つは Colab では同じディレクトリになる
# (HOME=/root)。重複したまま足していたので、書きかけの合計が実際の2倍に見え、
# 速度表示も倍に振れていた(2026-08-18 に 848MB/s と出た回の実体は半分)
inc = 0
seen = set()
for base in ('/root/.cache/huggingface',
             str(Path.home() / '.cache' / 'huggingface'),
             '/content/ComfyUI/models/.cache'):
    # 例外を投げると probe が丸ごと落ち、監視は何も分からないまま待ち続ける。
    # 分からないより、その項目だけ 0 にして残りを返す方がよい
    try:
        d = Path(base)
        if not d.exists():
            continue
        for x in d.rglob('*.incomplete'):
            if not x.is_file():
                continue
            key = str(x.resolve())
            if key in seen:
                continue
            seen.add(key)
            inc += x.stat().st_size
    except OSError:
        continue
try:
    q = json.loads(urllib.request.urlopen('http://127.0.0.1:8188/queue', timeout=5).read())
    n = len(q.get('queue_running', [])) + len(q.get('queue_pending', []))
except Exception:
    n = -1
# **API(8000) も見る。** 生成は ComfyUI ではなく API を通して投げるので、
# API だけ落ちていると手元からは何も投げられないのに、監視は「ComfyUI は
# 生きている」と見て黙っている。API が上がらないまま生成を投げて
# ConnectionReset で落ちたことがある
try:
    urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5).read()
    api = True
except Exception:
    api = False
# ComfyUI や API がまだ応答しなくても、プロセスが立ち上がっている最中かもしれない。
# H3 は 21GB を VRAM へ積むので数分かかる。
#
# **pgrep は使わない。** `subprocess.run("pgrep -f '<pattern>'", shell=True)` は
# sh のコマンドラインにパターン文字列が載るため、pgrep -f が自分自身を拾って
# **常に true** を返し、アイドル検知がまるごと無効になる。
#
# /proc を見る場合も同じ罠がある。この probe 自身のコマンドラインに探す文字列が
# 載るため、**自分の pid を除く**のと、**探す文字列をソースに直接書かない**
# (連結して組む)のを両方やる。
me = os.getpid()
needles = [('ComfyUI/' 'main.py',), ('uvi' 'corn', 'app' ':app')]
booting = False
for p in Path('/proc').iterdir():
    if not p.name.isdigit() or int(p.name) == me:
        continue
    try:
        cmd = (p / 'cmdline').read_bytes().decode('utf-8', 'replace')
    except OSError:
        continue
    if any(all(n in cmd for n in group) for group in needles):
        booting = True
        break
print(json.dumps({'used_gb': round(u / 1024 ** 3, 2),
                  'incomplete_gb': round(inc / 1024 ** 3, 2),
                  'queue': n, 'api': api, 'booting': booting}))
"""


def _set_state(state: str, **extra) -> None:
    """機械が読む状態を書く。state は building / ready / stopped。"""
    STATE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"state": state, "at": datetime.now().astimezone().isoformat(), **extra}
    STATE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        # タイムゾーンまで書く。コンテナが UTC のままだと手元と9時間ずれ、
        # 止まっているログを「3分前」と読み違える
        f.write(f"{datetime.now().astimezone():%m/%d %H:%M:%S %Z}  {msg}\n")


def _session(name: str) -> dict | None:
    try:
        return (json.loads(SESSIONS.read_text(encoding="utf-8")) or {}).get(name)
    except (OSError, json.JSONDecodeError):
        return None


def _alive(session: str) -> bool:
    r = subprocess.run(["pgrep", "-f", f"keep-alive .* {session}$"], capture_output=True)
    return r.returncode == 0


def _probe(session: str) -> tuple[dict | None, str | None]:
    """Colab 側の使用量と ComfyUI のキュー数を取る。返すのは (結果, 失敗の理由)。

    **失敗の理由を捨てない。** 認証切れとランタイム消失は、理由を残さないと
    ログから区別できない。
    """
    try:
        r = subprocess.run(
            ["colab", "exec", "-s", session],
            input=PROBE, capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return None, "colab exec が 120秒 で返らなかった"
    for line in reversed(r.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line), None
            except json.JSONDecodeError:
                continue
    # colab-cli は用件によって stderr にも stdout にも書く。両方見て最後の1行を拾う
    tail = [ln.strip() for ln in ((r.stderr or "") + "\n" + (r.stdout or "")).splitlines() if ln.strip()]
    reason = tail[-1] if tail else "出力が空"
    return None, f"colab exec が JSON を返さなかった (rc={r.returncode}): {reason}"


# 前回ログに書いた認証の状態。変わったときだけ書いて、30秒ごとの連呼を避ける
_auth_logged: str | None = None


def _check_auth(force: bool = False, reason: str = "") -> str:
    """OAuth を延長し、状態が変わったらログに出す。返すのは state の文字列。

    切れていても監視にできることは無い(`colab stop` も認証を要るため、
    ランタイムを止めることすらできない)。**できるのは大きな声で言うことだけ**なので、
    ok 以外は毎回書く。
    """
    global _auth_logged
    try:
        state = colab_auth.check_and_record(force=force)
    except Exception as e:  # 認証の確認で監視を落とさない
        _log(f"認証の確認に失敗した: {type(e).__name__}: {e}")
        return _auth_logged or "unknown"
    kind = state.get("state", "unknown")
    if kind != _auth_logged or kind != "ok":
        tail = f" <- {reason}" if reason else ""
        _log(f"認証: {colab_auth.describe(state)}{tail}")
    _auth_logged = kind
    return kind


def _rescue(session: str) -> None:
    """止める前に、Colab 側の成果物を手元へ回収する。

    ランタイムを止めると /content は消える。取得や生成に使った時間を無駄にしないため、
    残っているものは持ち帰ってから停止する。
    """
    for src in RESCUE_DIRS:
        name = Path(src).name
        dest = RESCUE_ROOT / session / name
        code = (
            "import subprocess\n"
            "from pathlib import Path\n"
            f"src = Path({src!r})\n"
            "files = [p for p in src.rglob('*') if p.is_file()] if src.exists() else []\n"
            "if files:\n"
            "    subprocess.run(['tar','czf','/content/rescue.tar.gz','-C',str(src.parent),src.name],check=True)\n"
            "    print('RESCUE_OK', len(files))\n"
            "else:\n"
            "    print('RESCUE_EMPTY')\n"
        )
        try:
            r = subprocess.run(["colab", "exec", "-s", session], input=code,
                               capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            _log(f"回収 {name}: 応答が無いので諦める")
            continue
        if "RESCUE_OK" not in r.stdout:
            continue
        n = r.stdout.split("RESCUE_OK", 1)[1].split()[0]
        dest.mkdir(parents=True, exist_ok=True)
        tar = dest / ".rescue.tar.gz"
        try:
            subprocess.run(
                ["colab", "download", "-s", session, "/content/rescue.tar.gz", str(tar)],
                capture_output=True, text=True, timeout=600, check=True,
            )
            subprocess.run(["tar", "xzf", str(tar), "-C", str(dest), "--strip-components=1"],
                           capture_output=True, timeout=300, check=True)
            tar.unlink(missing_ok=True)
            _log(f"回収 {name}: {n}件 -> works/.rescue/{session}/{name}/")
        except Exception as e:
            _log(f"回収 {name}: 失敗 ({type(e).__name__})")


def _stop(session: str, reason: str) -> None:
    """止める。**ここで諦めない。** 止め損ねると課金だけが続く。

    `colab stop` が返ってこないことがある。例外で監視ごと死ぬと、ランタイムは
    生きたまま誰も見ていない状態になる。**課金の打ち切りが存在理由である以上、
    ここだけは落ちてはいけない。**
    """
    _log(f"**自動停止**: {reason}")
    _rescue(session)

    stopped = False
    for attempt in range(1, STOP_TRIES + 1):
        try:
            r = subprocess.run(
                ["colab", "stop", "-s", session],
                capture_output=True, text=True, timeout=STOP_TIMEOUT,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            _log(f"停止が返らない ({attempt}/{STOP_TRIES}回目): {type(e).__name__}")
            continue
        _log(f"停止結果 ({attempt}/{STOP_TRIES}): "
             + (r.stdout.strip() or r.stderr.strip() or "(出力なし)"))
        if r.returncode == 0:
            stopped = True
            break
        time.sleep(5)

    if not stopped:
        _log(
            "**止められなかった。課金が続いている可能性がある。** 手で止めること: "
            f"src/scripts/colab.sh stop -s {session} / "
            f"src/scripts/colab.sh sessions で現物を確認する"
        )

    # **状態は回収と停止が終わってから書く。** 先に書くと、これを見た colab_run.sh が
    # 後片付けで監視を pkill し、回収が始まる前に殺してしまう。実際それで
    # setup.log を取り逃がした。
    #
    # 止め損ねても stopped は書く。colab_run.sh はこれを見て自分の後片付け
    # (もう一度 stop して sessions で現物を見る)へ進むので、**黙って待たせるより
    # 次の手を打たせる方が安全**。止まったかどうかは stop_failed で分かる
    _set_state("stopped", reason=reason, stop_failed=not stopped)


def _disk_move(total: float, last_used: float | None) -> str:
    """ディスク使用量の動き。"init" / "grew" / "shrank" / "flat" を返す。

    **減ったことを、伸びたことと同じ重さで見る。** 取得の途中で書きかけを捨てると
    使用量は落ちる。過去の最大だけを基準にすると、捨てた分を埋め直すまで進捗と
    見なされない。80GB 捨てて 19GB を取り直す回では二度と超えないので、素の HTTP へ
    切り替えて実際に取れていても自動停止が確定していた。
    """
    if last_used is None:
        return "init"
    if total > last_used + MOVE_GB:
        return "grew"
    if total < last_used - MOVE_GB:
        return "shrank"
    return "flat"


def main() -> None:
    session = sys.argv[1] if len(sys.argv) > 1 else "comfy"
    started = time.time()
    _set_state("building")
    _log(
        f"監視を始めた (session={session}, {INTERVAL:.0f}秒ごと, "
        f"上限 {MAX_MINUTES:.0f}分, アイドル {IDLE_MINUTES:.0f}分で自動停止)"
    )

    revived = 0
    last_used: float | None = None
    last_move = time.time()
    last_grew_at = last_move  # 速度表示の基準(ディスクが伸びた時刻)
    ready_logged = False      # ComfyUI が最初に応答した
    served = False            # 生成が1件でもキューに載った
    last_auth = 0.0           # 0 にして起動直後の1回目を必ず走らせる
    probe_fail = 0            # probe が続けて失敗した回数
    last_probe_err: str | None = None
    last_api: bool | None = None  # API(8000) の生死。変わったときだけ書く

    while True:
        if AUTH_CHECK_MINUTES > 0 and time.time() - last_auth >= AUTH_CHECK_MINUTES * 60:
            last_auth = time.time()
            _check_auth()

        state = _session(session)
        if not state:
            _log("セッションが無くなったので監視を終える")
            _set_state("stopped", reason="セッションが無くなった")
            return

        elapsed_min = (time.time() - started) / 60
        if elapsed_min > MAX_MINUTES:
            _stop(session, f"確保から {elapsed_min:.0f}分 経った(上限 {MAX_MINUTES:.0f}分)")
            return

        if not _alive(session):
            revived += 1
            _log(f"keep-alive が落ちていたので起こし直す ({state['endpoint']}) / 通算 {revived}回")
            subprocess.Popen(
                ["colab", "keep-alive", state["endpoint"], session],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

        probe, probe_err = _probe(session)
        if probe is None:
            probe_fail += 1
            # 同じ理由を30秒ごとに書くと読めなくなる。理由が変わったときと、
            # 10回(5分)ごとだけ書く
            if probe_err != last_probe_err or probe_fail % 10 == 0:
                _log(f"様子が取れない ({probe_fail}回連続): {probe_err}")
                last_probe_err = probe_err
            # 3回続くなら一時的なつまずきではない。**認証切れなら probe は永久に
            # 戻らない。** 上限まで無言で回らないよう、ここで疑って確かめる
            if probe_fail == 3:
                last_auth = time.time()
                _check_auth(force=True, reason="様子が3回続けて取れない")
        elif probe_fail:
            _log(f"様子が取れるようになった ({probe_fail}回連続の失敗のあと)")
            probe_fail = 0
            last_probe_err = None

        if probe is not None:
            queue = probe["queue"]
            inc = probe.get("incomplete_gb", 0.0)
            # 書きかけは使用量に含まれている(同じファイルシステム)。足さない
            total = probe["used_gb"]

            if queue >= 0 and not ready_logged:
                # ここから手元で produce を投げられる
                ready_logged = True
                _set_state("ready")
                _log(f"準備できた (ComfyUI 応答。{READY_IDLE_MINUTES:.0f}分 待つ)")
                last_move = time.time()
            # **ComfyUI が生きていても API が死んでいたら投げられない。**
            # 手元は API 経由でしか生成できないので、ここを黙って見過ごすと
            # 「準備できた」と言いながら何も受け付けない時間が課金され続ける
            api_up = probe.get("api")
            if api_up is not None and api_up != last_api:
                if last_api is not None or not api_up:
                    _log("API(8000) が応答した" if api_up else "API(8000) が応答しない")
                last_api = api_up
            if queue > 0:
                served = True

            move = _disk_move(total, last_used)
            if move == "shrank":
                # 書きかけを捨てた(取り直し、あるいは Xet から素の HTTP への
                # 切り替え)。**捨てたのは動いた証拠なので、基準を取り直して
                # 待ち直す。** 取り直しはファイルごとに数回までなので、
                # ここで待ち直しても上限時間(MAX_MINUTES)は超えない
                now = time.time()
                _log(f"ディスクが減った {last_used:.1f}GB -> {total:.1f}GB "
                     f"(書きかけを捨てたと見て基準を取り直す。書きかけ {inc:.1f}GB)")
                last_used, last_move, last_grew_at = total, now, now
            elif move in ("init", "grew"):
                now = time.time()
                if last_used is not None:
                    # 速度の基準は last_move と別にする。last_move は「準備できた」でも
                    # 動くので、共用すると 0 秒で割ることになる
                    rate = (total - last_used) * 1024 / max(now - last_grew_at, 1e-6)
                    tail = f" / 取得中 {inc:.1f}GB" if inc > 0.05 else ""
                    _log(f"進捗 {total:.1f}GB ({rate:.0f}MB/s){tail}")
                last_used, last_move, last_grew_at = total, now, now
            elif queue > 0:
                # 生成中はディスクが伸びない。キューがあるうちは待つ
                last_move = time.time()
            elif (
                queue < 0
                and probe.get("booting")
                and (time.time() - last_move) / 60 <= BOOT_MINUTES
            ):
                # ComfyUI / API が応答しないがプロセスは立ち上がっている。
                # H3 は 21GB を VRAM へ積むので数分かかる。ただし待つのは
                # BOOT_MINUTES まで。無期限に待つとアイドル検知が死ぬ
                pass
            else:
                # 準備できてから最初の1件が来るまでは、手元で produce を叩く時間を見る
                allowed = (
                    READY_IDLE_MINUTES
                    if ready_logged and not served
                    else IDLE_MINUTES
                )
                idle_min = (time.time() - last_move) / 60
                if idle_min > allowed:
                    _stop(
                        session,
                        f"{idle_min:.0f}分 何も進んでいない "
                        f"(ディスク {total:.1f}GB のまま、書きかけ {inc:.1f}GB、"
                        f"キュー {queue}、許容 {allowed:.0f}分)",
                    )
                    return

        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
