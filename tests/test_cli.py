"""cw の振り分け。何をどこへ渡すかだけを見る (生成そのものは既存のテストが見ている)。

**ここで確かめたいのは「呼ぶ側が3つを知らずに済んでいるか」。** リポジトリの場所・
compose のサービス名・コンテナ内のパスが漏れていないこと、生成系がサブプロセスを
挟まずに動くこと、運用系が cwd=<リポジトリ> で既存のスクリプトを呼ぶこと。
"""

from __future__ import annotations

import ast
import io
import sys
import unittest
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from unittest import mock

import _bootstrap  # noqa: F401

from cli import main as cw


class Recorder:
    """_call_script / _sh の代わりに、呼ばれ方だけを控える。"""

    def __init__(self, rc: int = 0):
        self.calls: list[tuple] = []
        self.rc = rc

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        return self.rc


class RepoTest(unittest.TestCase):
    def test_repo_is_the_source_tree(self):
        """`__file__` の2つ上。editable install でもソースツリーを指すこと。"""
        with mock.patch.dict("os.environ", {}, clear=True):
            repo = cw.repo_home()
        self.assertTrue((repo / "src" / "scripts" / "generate_image.py").exists())
        self.assertTrue((repo / "docker-compose.yml").exists())

    def test_only_8000_is_published_and_the_bind_is_written_out(self):
        """ホストへ出すポートを固定する。

        **ComfyUI(8188)はホストへ出さない。** 認証が無く、ワークフローを投げられること
        自体が任意コード実行と等価になる。手元から 8188 を使う経路も無い。

        8000 は意図して 0.0.0.0 に出す。共有ネットワークを畳んだので、コンテナから頼む側
        (host.docker.internal 経由) はここしか通り道が無い。Bearer キーが要る口。

        **ホスト IP を省かない。** 省くと 0.0.0.0 になるので、広い側を選ぶときも
        書いてあること (issue #19 は、書いていなかったせいで 8188 が LAN へ出ていた)。
        """
        with mock.patch.dict("os.environ", {}, clear=True):
            repo = cw.repo_home()
        text = (repo / "docker-compose.yml").read_text()
        published = re.findall(r'^\s*-\s*"([^"]*:\d+)"\s*$', text, re.M)
        self.assertEqual(published, ["0.0.0.0:8000:8000"])

    def test_env_override(self):
        with TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"COMFY_WRAPPER_HOME": tmp}, clear=True):
                self.assertEqual(cw.repo_home(), Path(tmp).resolve())

    def test_missing_script_names_the_fix(self):
        with mock.patch.object(cw, "SCRIPTS", Path("/nonexistent/scripts")):
            with self.assertRaises(SystemExit) as cm:
                cw._load_script("generate_image")
        self.assertIn("COMFY_WRAPPER_HOME", str(cm.exception))


class DispatchTest(unittest.TestCase):
    def setUp(self):
        self.script = Recorder()
        patcher = mock.patch.object(cw, "_call_script", self.script)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_image_goes_to_submit(self):
        cw.main(["image", "a pug", "--model", "z-image", "--out", "./hero.png"])
        self.assertEqual(
            self.script.calls,
            [("generate_image", "cw image",
              ["submit", "a pug", "--model", "z-image", "--out", "./hero.png"])],
        )

    def test_image_tolerates_an_explicit_submit(self):
        """--help の usage が `cw image submit ...` と出るので、そう打たれても通す。"""
        cw.main(["image", "submit", "a pug"])
        self.assertEqual(self.script.calls[0][2], ["submit", "a pug"])

    def test_video_takes_the_still_as_a_positional(self):
        cw.main(["video", "./hero.png", "--model", "ltx-2.5"])
        self.assertEqual(
            self.script.calls[0][2],
            ["--first-frame", "./hero.png", "--model", "ltx-2.5"],
        )

    def test_video_without_a_still_is_t2v(self):
        cw.main(["video", "--prompt", "rain on neon streets"])
        self.assertEqual(self.script.calls[0][2], ["--prompt", "rain on neon streets"])

    def test_post_goes_to_submit(self):
        cw.main(["post", "./clip.mp4", "--size", "4k"])
        self.assertEqual(self.script.calls[0][2], ["submit", "./clip.mp4", "--size", "4k"])

    def test_measure_passes_through(self):
        cw.main(["measure", "status", "--model", "wan2.2"])
        self.assertEqual(self.script.calls[0][2], ["status", "--model", "wan2.2"])

    def test_options_are_not_eaten_by_cw(self):
        """**cw 側でオプションを解釈しない。** 既存の引数がそのまま書けること。"""
        cw.main(["image", "--negative", "blurry", "a pug"])
        self.assertEqual(self.script.calls[0][2], ["submit", "--negative", "blurry", "a pug"])

    def test_unknown_command_is_rejected(self):
        with mock.patch("builtins.print") as out:
            self.assertEqual(cw.main(["genrate"]), 2)
        self.assertIn("知らないコマンド", out.call_args_list[0][0][0])

    def test_the_double_dash_survives(self):
        """**`cw run ... -- <作業>` の `--` を落とさないこと。** argparse に通すと
        最初の `--` が消え、colab_run.sh が作業をオプションと読んで落ちる。"""
        sh = Recorder()
        with mock.patch.object(cw, "_sh", sh):
            cw.main(["run", "--setup", "video", "--", "src/scripts/generate_video.py"])
        self.assertEqual(
            sh.calls,
            [("colab_run.sh", "--setup", "video", "--", "src/scripts/generate_video.py")],
        )

    def test_no_command_prints_usage(self):
        with mock.patch("builtins.print") as out:
            self.assertEqual(cw.main([]), 0)
        self.assertIn("cw image", out.call_args[0][0])


class JobsTest(unittest.TestCase):
    def test_collects_stills_and_skips_an_unused_ledger(self):
        script = Recorder()
        with TemporaryDirectory() as tmp, \
             mock.patch.object(cw, "_call_script", script), \
             mock.patch.object(cw.colab_link, "JOBS_DIR", Path(tmp)), \
             mock.patch("builtins.print"):
            cw.main(["jobs"])
        self.assertEqual([c[0] for c in script.calls], ["generate_image"])

    def test_collects_postprocess_when_it_has_a_ledger(self):
        script = Recorder()
        with TemporaryDirectory() as tmp, \
             mock.patch.object(cw, "_call_script", script), \
             mock.patch.object(cw.colab_link, "JOBS_DIR", Path(tmp)), \
             mock.patch("builtins.print"):
            (Path(tmp) / "postprocess.json").write_text("{}")
            cw.main(["jobs"])
        self.assertEqual([c[0] for c in script.calls], ["generate_image", "postprocess"])


class FakeRuntime:
    """`colab exec` の代わり。送られたコードを控え、決めた中身を返す。

    **前後に colab-cli の行を混ぜて返す。** 本文を区切りの間だけから拾えているかを
    見るため (混ざると保存したログの先頭に接続メッセージが入る)。
    """

    def __init__(self, files: dict[str, str] | None = None, reason: str = ""):
        self.files = {} if files is None else files
        self.reason = reason
        self.calls: list[tuple[str, str]] = []

    def __call__(self, code, session, timeout=None):
        self.calls.append((code, session))
        if self.reason:
            return None, self.reason
        token = re.search(r"CWLOG[0-9a-f]+", code).group(0)
        out = ["colab: connecting to the runtime..."]
        if "'FILE'" in code:
            for name, text in self.files.items():
                out.append(f"{token} FILE {name}.log {len(text.encode())} 08/18 19:00:00")
        else:
            head = re.search(r"names, tail, limit, token = (\[.*?\]), (\d+),", code)
            for name in ast.literal_eval(head.group(1)):
                text = self.files.get(name)
                if text is None:
                    out.append(f"{token} MISSING {name}")
                    continue
                tail = int(head.group(2))
                lines = text.splitlines()
                out.append(f"{token} BEGIN {name} {len(text.encode())}")
                out.extend(lines[-tail:] if tail else lines)
                out.append(f"{token} END {name}")
        out.append("colab: done")
        return "\n".join(out) + "\n", ""


class LogsTest(unittest.TestCase):
    """止める前にログを読む口 (issue #20)。

    **ランタイムを止めると /content ごと消える。** 読む手段が無いと、その回に分かった
    ことがそのまま失われる。cw の中で完結していること、保存先が監視の回収と同じ場所で
    あることを見る。
    """

    LOGS = {"setup": "\n".join(f"setup {i}" for i in range(1, 6)),
            "api": "uvicorn running", "comfyui": "got prompt"}

    def setUp(self):
        self.runtime = FakeRuntime(dict(self.LOGS))
        patcher = mock.patch.object(cw, "_colab_code", self.runtime)
        patcher.start()
        self.addCleanup(patcher.stop)
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.rescue = Path(tmp.name)
        patcher = mock.patch.object(cw, "RESCUE_ROOT", self.rescue)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, *argv) -> tuple[int, str]:
        with mock.patch("sys.stdout", io.StringIO()) as out:
            rc = cw.main(list(argv))
        return rc, out.getvalue()

    def test_the_tail_comes_back_without_the_cli_chatter(self):
        rc, printed = self._run("logs", "setup", "--tail", "2")
        self.assertEqual(rc, 0)
        self.assertIn("setup 5", printed)
        self.assertNotIn("setup 1", printed)
        # 区切りの外は本文ではない
        self.assertNotIn("colab: connecting", printed)
        self.assertIn("/content/logs/setup.log", printed)

    def test_the_session_and_the_remote_path_stay_inside_cw(self):
        """呼ぶ側に colab exec もコンテナ内のパスも書かせない。"""
        self._run("logs", "api", "-s", "other")
        code, session = self.runtime.calls[0]
        self.assertEqual(session, "other")
        self.assertIn("/content/logs", code)

    def test_without_a_name_it_lists_what_is_there(self):
        rc, printed = self._run("logs")
        self.assertEqual(rc, 0)
        for name in self.LOGS:
            self.assertIn(f"{name}.log", printed)

    def test_save_takes_the_whole_file_to_the_rescue_directory(self):
        """--save は末尾ではなく全文。**回収は監視の自動停止と同じ置き場へ。**"""
        rc, printed = self._run("logs", "--save", "-s", "comfy")
        self.assertEqual(rc, 0)
        saved = self.rescue / "comfy" / "logs" / "setup.log"
        self.assertIn("setup 1", saved.read_text())
        self.assertIn("setup 5", saved.read_text())
        self.assertIn("logs/setup.log", printed.replace("\\", "/"))
        # 全文を頼んでいること (tail=0)
        code = self.runtime.calls[0][0]
        self.assertRegex(code, r"names, tail, limit, token = \[.*?\], 0,")

    def test_a_missing_log_is_named_and_fails(self):
        self.runtime.files.pop("api")
        rc, printed = self._run("logs", "api")
        self.assertEqual(rc, 1)
        self.assertIn("api.log", printed)

    def test_an_unknown_name_is_rejected(self):
        with mock.patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                cw.main(["logs", "keepalive"])

    def test_a_runtime_that_does_not_answer_says_why(self):
        """**監視と取り合いになる。** 返らなかった回に理由を捨てない。"""
        self.runtime.reason = "colab exec が 120秒 で返りませんでした (監視が同じ口を使っています)"
        rc, printed = self._run("logs", "setup")
        self.assertEqual(rc, 1)
        self.assertIn("監視", printed)


class OpsTest(unittest.TestCase):
    """運用系。**docker compose を呼ぶ側に見せない**ことを確かめる。"""

    def setUp(self):
        self.sh = Recorder()
        self.docker = Recorder()
        for name, value in (("_sh", self.sh), ("_docker", self.docker)):
            patcher = mock.patch.object(cw, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # 疎通の待ちはテストでは要らない
        patcher = mock.patch.object(cw, "REACH_WAIT", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        # 止める前のログ回収も cw の中を通る。ここで実物の colab exec を叩かせない
        self.runtime = FakeRuntime({"setup": "setup done"})
        patcher = mock.patch.object(cw, "_colab_code", self.runtime)
        patcher.start()
        self.addCleanup(patcher.stop)
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.rescue = Path(tmp.name)
        patcher = mock.patch.object(cw, "RESCUE_ROOT", self.rescue)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _health(self, *outcomes):
        """/health の返り方を順に並べる。LinkError なら届かなかった回。"""
        health = mock.patch.object(cw.colab_link, "health", side_effect=list(outcomes))
        self.addCleanup(health.stop)
        return health.start()

    def test_up_keeps_the_session(self):
        self._health({"comfy_ready": True})
        with mock.patch("builtins.print"):
            cw.main(["up", "--setup", "image", "--models", "z-image", "--max", "45"])
        args = self.sh.calls[0]
        self.assertEqual(args[0], "colab_run.sh")
        self.assertEqual(args[1:6], ("--setup", "image", "--models", "z-image", "--max"))
        # 確保したものを残すのが up。実行するものが無いので -- のあとは何もしない
        self.assertIn("--keep", args)
        self.assertEqual(args[-3], "--")

    def test_up_checks_from_the_host_before_returning(self):
        """**構築側の確認はコンテナから見ている。** 手元からの経路は返る前に見ること。

        「準備できた」の直後の1投目が Connection refused で落ちた (issue #14)。
        届いているなら張り直さない。
        """
        health = self._health({"comfy_ready": True})
        with mock.patch("builtins.print"):
            self.assertEqual(cw.main(["up"]), 0)
        self.assertEqual(health.call_count, 1)
        self.assertEqual(health.call_args[0][0], cw.colab_link.read_endpoint())
        self.assertEqual(self.docker.calls, [])

    def test_up_restrings_the_tunnel_when_the_host_cannot_reach(self):
        """落ちていたら張り直してから返す。**セッションは触らない。**"""
        self._health(cw.colab_link.LinkError("接続できません"), {"comfy_ready": True})
        with mock.patch("builtins.print"):
            self.assertEqual(cw.main(["up"]), 0)
        self.assertEqual(self.docker.calls, [("compose", "restart", "tunnel")])
        self.assertEqual([c[0] for c in self.sh.calls], ["colab_run.sh"])

    def test_up_sends_you_to_tunnel_restart_when_it_cannot_be_fixed(self):
        """直らないときも確保し直させない。生きているランタイムを捨てることになる。"""
        boom = cw.colab_link.LinkError("接続できません")
        self._health(*[boom] * (cw.REACH_TRIES + 1))
        with mock.patch("builtins.print") as out:
            self.assertEqual(cw.main(["up"]), 1)
        printed = " ".join(str(c[0][0]) for c in out.call_args_list if c[0])
        self.assertIn("cw tunnel restart", printed)
        self.assertIn("確保し直さないでください", printed)
        # 生成できるかのように締めない
        self.assertNotIn("生成できます", printed)

    def test_up_does_not_probe_when_the_setup_failed(self):
        """構築が落ちた回に疎通の話をしない。理由は colab_run.sh が出している。"""
        self.sh.rc = 1
        health = self._health({"comfy_ready": True})
        with mock.patch("builtins.print"):
            self.assertEqual(cw.main(["up"]), 1)
        self.assertEqual(health.call_count, 0)

    def test_up_refuses_to_take_work(self):
        with self.assertRaises(SystemExit) as cm:
            cw.main(["up", "--", "src/scripts/generate_image.py"])
        self.assertIn("cw run", str(cm.exception))

    def test_run_needs_something_to_run(self):
        with self.assertRaises(SystemExit):
            cw.main(["run"])

    def test_stop_checks_the_server_and_folds_the_tunnel(self):
        """**一覧から消えることと、リモートが止まることは別。** 止めたあと現物を見ること。"""
        with mock.patch("builtins.print"):
            cw.main(["stop"])
        self.assertEqual(
            [c for c in self.sh.calls],
            [("colab_watch.sh", "--stop"),
             ("colab.sh", "stop", "-s", "comfy"),
             ("colab.sh", "sessions")],
        )
        self.assertEqual(self.docker.calls, [("compose", "stop", "tunnel")])

    def test_stop_rescues_the_logs_before_it_folds_the_runtime(self):
        """**止めると /content ごと消える。** 畳む前に持ち帰ること (issue #20)。"""
        with mock.patch("builtins.print"):
            cw.main(["stop"])
        self.assertEqual((self.rescue / "comfy" / "logs" / "setup.log").read_text().strip(),
                         "setup done")
        self.assertEqual(len(self.runtime.calls), 1)

    def test_stop_can_skip_the_rescue(self):
        with mock.patch("builtins.print"):
            cw.main(["stop", "--no-logs"])
        self.assertEqual(self.runtime.calls, [])

    def test_a_failed_rescue_does_not_stop_the_stopping(self):
        """回収に失敗しても止めるほうは続ける。止め損ねると課金だけが続く。"""
        self.runtime.reason = "認証が切れています"
        with mock.patch("builtins.print"):
            cw.main(["stop"])
        self.assertIn(("colab.sh", "stop", "-s", "comfy"), self.sh.calls)

    def test_stop_releases_orphans_only_when_asked(self):
        """名前の無い割り当ては stop -s では引けない。**明示したときだけ解放する。**"""
        exec_ = Recorder()
        with mock.patch.object(cw, "_colab_exec", exec_), mock.patch("builtins.print"):
            cw.main(["stop"])
            self.assertEqual(exec_.calls, [])
            cw.main(["stop", "--orphans"])
        self.assertEqual(exec_.calls, [("/app/src/scripts/colab_unassign.py",)])

    def test_status_names_the_reason_when_it_cannot_reach(self):
        """疎通の判定を書き直さない。落ちている理由は colab_link が名指しする。"""
        boom = cw.colab_link.LinkError("トンネルが切れています")
        with mock.patch.object(cw.colab_link, "health", side_effect=boom), \
             mock.patch("builtins.print") as out:
            self.assertEqual(cw.main(["status"]), 1)
        printed = " ".join(str(c[0][0]) for c in out.call_args_list if c[0])
        self.assertIn("トンネルが切れています", printed)

    def test_tunnel_restart_does_not_touch_the_session(self):
        """セッションが生きていてトンネルだけ落ちた状態で、確保し直させないこと。"""
        cw.main(["tunnel", "restart"])
        self.assertEqual(self.docker.calls, [("compose", "restart", "tunnel")])
        self.assertEqual(self.sh.calls, [])

    def test_tunnel_defaults_to_restart(self):
        cw.main(["tunnel"])
        self.assertEqual(self.docker.calls, [("compose", "restart", "tunnel")])

    def test_key_push_sends_the_hashes(self):
        cw.main(["key", "push"])
        self.assertEqual(self.sh.calls, [("colab_key.sh", "comfy")])


class AuthTest(unittest.TestCase):
    """認証。**呼ぶ側に `colab` コマンドも compose も見せない**ことを確かめる。

    colab-cli の再認証は `input()` で標準入力を待つので、そこへ落ちると人が対話端末に
    入るまで止まる。URL を出す側とコードを渡す側が別のコマンドになっていること、
    通ったあとに現物を問い合わせていることを見る。
    """

    SCRIPT = "/app/src/scripts/colab_auth.py"

    def setUp(self):
        self.exec = Recorder()
        self.sh = Recorder()
        for name, value in (("_colab_exec", self.exec), ("_sh", self.sh)):
            patcher = mock.patch.object(cw, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch("builtins.print")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_bare_auth_just_checks(self):
        cw.main(["auth"])
        self.assertEqual(self.exec.calls, [(self.SCRIPT,)])

    def test_check_flags_pass_through(self):
        cw.main(["auth", "--force", "--json"])
        self.assertEqual(self.exec.calls, [(self.SCRIPT, "--force", "--json")])

    def test_login_asks_for_the_url_only(self):
        """URL を出す段でトークンを触らない。ここは人がブラウザへ行くだけ。"""
        cw.main(["auth", "login"])
        self.assertEqual(self.exec.calls, [(self.SCRIPT, "login", "--url")])
        self.assertEqual(self.sh.calls, [])

    def test_login_with_a_code_is_not_interactive(self):
        cw.main(["auth", "login", "--code", "4/0AX-code"])
        self.assertEqual(
            self.exec.calls, [(self.SCRIPT, "login", "--code", "4/0AX-code")]
        )

    def test_a_fresh_login_looks_for_leftover_runtimes(self):
        """**切れている間は問い合わせができない。** 止めたつもりのものが動いている。"""
        cw.main(["auth", "login", "--code", "4/0AX-code"])
        self.assertEqual(self.sh.calls, [("colab.sh", "sessions")])

    def test_a_failed_login_does_not_claim_to_have_checked(self):
        self.exec.rc = 3
        self.assertEqual(cw.main(["auth", "login", "--code", "bad"]), 3)
        self.assertEqual(self.sh.calls, [])


class ModelsTest(unittest.TestCase):
    CATALOG = [
        {"id": "ltx-2.5", "kind": "video", "tasks": ["t2v", "i2v"], "last_frame": True,
         "audio_out": True, "ref_images": 0, "fps": {"default": 24}, "weights_gb": 39.7,
         "seconds_per_output_second": {"L4": 20.8, "measured": True}, "ready": True},
        {"id": "z-image", "kind": "image", "ref_images": 0, "weights_gb": 11.3,
         "seconds_per_image": {"L4": 15.0}, "notes": "8step で速い", "ready": False},
    ]

    def _render(self, ready_known: bool, argv=()):
        with mock.patch.object(cw, "_fetch_catalog",
                               return_value=(self.CATALOG, ready_known, "")), \
             mock.patch("builtins.print") as out:
            rc = cw.main(["models", *argv])
        return rc, "\n".join(str(c[0][0]) for c in out.call_args_list if c[0])

    def test_table_has_both_kinds_and_a_cost(self):
        rc, text = self._render(True)
        self.assertEqual(rc, 0)
        self.assertIn("ltx-2.5", text)
        self.assertIn("z-image", text)
        self.assertIn("実測", text)
        # 20.8秒 x 18.1566円/時 / 3600 = 0.10円
        self.assertIn("0.10", text)

    def test_ready_is_blank_when_the_runtime_cannot_answer(self):
        """**カタログはランタイムが無くても答えられること。** 見積もりは確保の前に要る。"""
        rc, text = self._render(False)
        self.assertEqual(rc, 0)
        self.assertIn("ltx-2.5", text)
        self.assertIn("問い合わせられませんでした", text)
        row = next(line for line in text.splitlines() if line.startswith("ltx-2.5"))
        self.assertEqual(row.split()[1], "-")

    def test_ready_is_marked_when_the_runtime_answered(self):
        _, text = self._render(True)
        row = next(line for line in text.splitlines() if line.startswith("ltx-2.5"))
        self.assertEqual(row.split()[1], "○")

    def test_unknown_ready_is_explained(self):
        """/health は動画ウェイトしか持たない。○ でも × でもない理由を書くこと。"""
        catalog = [dict(self.CATALOG[1], ready=None)]
        with mock.patch.object(cw, "_fetch_catalog", return_value=(catalog, True, "")), \
             mock.patch("builtins.print") as out:
            cw.main(["models"])
        text = "\n".join(str(c[0][0]) for c in out.call_args_list if c[0])
        self.assertIn("静止画モデルの用意", text)

    def test_json_passes_the_entries_through(self):
        rc, text = self._render(True, ["--json"])
        self.assertEqual(rc, 0)
        self.assertIn('"seconds_per_output_second"', text)


class TableTest(unittest.TestCase):
    """日本語の見出しは2桁ぶんの幅を取る。数えないと右の列がずれる。"""

    def test_width_counts_wide_characters_as_two(self):
        self.assertEqual(cw._width("モデル"), 6)
        self.assertEqual(cw._width("z-image"), 7)

    def test_pad_fills_to_the_display_width(self):
        self.assertEqual(cw._width(cw._pad("モデル", 10)), 10)

    def test_columns_line_up(self):
        text = cw._table(["モデル", "用意"], [["z-image", "○"], ["日本語のモデル", "×"]])
        offsets = set()
        for line, cell in zip(text.splitlines(), ("用意", "○", "×"), strict=False):
            if cell in line:
                offsets.add(cw._width(line[:line.rindex(cell)]))
        self.assertEqual(len(offsets), 1)


class InitTest(unittest.TestCase):
    """初回の用意。**鍵とトークンの置き場を呼ぶ側に覚えさせない**ことを確かめる。"""

    def test_status_names_what_is_missing_and_fails(self):
        with TemporaryDirectory() as tmp:
            colab = Path(tmp) / ".colab"
            with mock.patch.object(cw, "SSH_KEY", colab / ".ssh" / "id_ed25519"), \
                 mock.patch.object(cw, "HF_TOKEN", colab / "hf-token"), \
                 mock.patch.object(cw.colab_link, "COLAB_DIR", colab), \
                 mock.patch.object(cw.colab_link, "API_KEY_FILE", colab / "colab-api-key"), \
                 mock.patch.object(cw.colab_link, "LEGACY_API_KEY_FILE", colab / "h3-api-key"), \
                 mock.patch.object(cw.colab_link, "AUTH_STATE", colab / "auth-state.json"), \
                 mock.patch("builtins.print") as out:
                # 揃っていなければ 0 を返さない。無人の手順から気づけるようにする
                self.assertEqual(cw.main(["init"]), 1)
        printed = " ".join(str(c[0][0]) for c in out.call_args_list if c[0])
        self.assertIn("cw init ssh", printed)
        self.assertIn("cw key issue", printed)

    def test_hf_token_is_saved_600_and_never_printed(self):
        with TemporaryDirectory() as tmp:
            token = Path(tmp) / ".colab" / "hf-token"
            with mock.patch.object(cw, "HF_TOKEN", token), \
                 mock.patch("builtins.print") as out:
                self.assertEqual(cw.main(["init", "hf", "--token", "hf_secret"]), 0)
            self.assertEqual(token.read_text().strip(), "hf_secret")
            self.assertEqual(token.stat().st_mode & 0o777, 0o600)
        printed = " ".join(str(c[0][0]) for c in out.call_args_list if c[0])
        self.assertNotIn("hf_secret", printed)

    def test_hf_token_is_read_from_stdin(self):
        with TemporaryDirectory() as tmp:
            token = Path(tmp) / ".colab" / "hf-token"
            with mock.patch.object(cw, "HF_TOKEN", token), \
                 mock.patch.object(cw.sys, "stdin", io.StringIO("hf_piped\n")), \
                 mock.patch("builtins.print"):
                self.assertEqual(cw.main(["init", "hf"]), 0)
            self.assertEqual(token.read_text().strip(), "hf_piped")

    def test_hf_rejects_an_empty_token(self):
        with TemporaryDirectory() as tmp:
            token = Path(tmp) / ".colab" / "hf-token"
            with mock.patch.object(cw, "HF_TOKEN", token):
                with self.assertRaises(SystemExit):
                    cw.main(["init", "hf", "--token", "  "])
            self.assertFalse(token.exists())

    def test_skills_come_from_the_clone_not_github(self):
        """**CLI とスキルの版をずらさない。** 入れ先は clone、GitHub ではない。"""
        npx = Recorder()
        with mock.patch.object(cw, "_npx", npx):
            self.assertEqual(cw.main(["init", "skills"]), 0)
        args = npx.calls[0]
        self.assertEqual(args[:2], ("add", str(cw.REPO)))
        self.assertIn("colab-comfy", args)
        self.assertIn("ltx-prompt", args)
        # 既定は呼ぶ側のプロジェクト。黙って ~/.claude を触らない
        self.assertIn("-p", args)
        self.assertNotIn("-g", args)

    def test_skills_scope_is_chosen_explicitly(self):
        npx = Recorder()
        with mock.patch.object(cw, "_npx", npx), mock.patch("builtins.print"):
            cw.main(["init", "skills", "--global", "--no-h3"])
            cw.main(["init", "skills", "--project", "--no-h3"])
        self.assertIn("-g", npx.calls[0])
        self.assertNotIn("-p", npx.calls[0])
        self.assertIn("-p", npx.calls[1])

    def test_skills_scope_flags_cannot_be_combined(self):
        with self.assertRaises(SystemExit):
            cw.main(["init", "skills", "--project", "--global"])

    def test_h3_comes_from_the_official_repository_by_default(self):
        """H3 の書き方は MiniMax 公式に譲った。こちらから配らず、公式を取りに行く。"""
        npx = Recorder()
        with mock.patch.object(cw, "_npx", npx), mock.patch("builtins.print"):
            cw.main(["init", "skills"])
        self.assertEqual(len(npx.calls), 2)
        self.assertIn("https://github.com/MiniMax-AI/MiniMax-H3", npx.calls[1])
        self.assertIn("h3-prompt-writing", npx.calls[1])
        # 入れ先はうちの2つと同じ。片方だけ ~/.claude に散らない
        self.assertIn("-p", npx.calls[1])

    def test_h3_can_be_skipped(self):
        npx = Recorder()
        with mock.patch.object(cw, "_npx", npx):
            cw.main(["init", "skills", "--no-h3"])
        self.assertEqual(len(npx.calls), 1)

    def test_without_npx_all_three_are_shown_by_hand(self):
        """npx が無いときの案内も3つ揃える。H3 だけ手元に無い状態にしない。"""
        err = io.StringIO()
        with mock.patch("subprocess.run", side_effect=FileNotFoundError), \
             mock.patch.object(sys, "stderr", err):
            self.assertEqual(cw._npx("add", str(cw.REPO)), 127)
        printed = err.getvalue()
        self.assertIn("colab-comfy", printed)
        self.assertIn("ltx-prompt", printed)
        self.assertIn("h3-prompt-writing", printed)
        self.assertIn("https://github.com/MiniMax-AI/MiniMax-H3", printed)

    def test_a_failed_h3_does_not_hide_that_ours_are_in(self):
        """**黙らせない。** うちの2つが入った状態で公式だけ落ちることがある。"""
        npx = Recorder(rc=1)
        ours = Recorder()

        def install(*args):
            return ours(*args) if str(cw.REPO) in args else npx(*args)

        with mock.patch.object(cw, "_npx", install), \
             mock.patch("builtins.print") as out:
            self.assertEqual(cw.main(["init", "skills"]), 1)
        printed = " ".join(str(c[0][0]) for c in out.call_args_list if c[0])
        self.assertIn("うちの2つは入っています", printed)

    def test_ssh_keeps_an_existing_key(self):
        """**作り直すと張り直しになる。** 黙って上書きしないこと。"""
        run = Recorder()
        with TemporaryDirectory() as tmp:
            key = Path(tmp) / "id_ed25519"
            key.write_text("existing")
            with mock.patch.object(cw, "SSH_KEY", key), \
                 mock.patch.object(cw, "_colab_run", run), \
                 mock.patch("builtins.print"):
                self.assertEqual(cw.main(["init", "ssh"]), 0)
            self.assertEqual(key.read_text(), "existing")
        self.assertEqual(run.calls, [])

    def test_ssh_generates_in_the_container(self):
        """鍵は colab コンテナで作る。呼ぶ側にコンテナ内のパスを書かせない。"""
        run = Recorder()
        with TemporaryDirectory() as tmp:
            with mock.patch.object(cw, "SSH_KEY", Path(tmp) / "id_ed25519"), \
                 mock.patch.object(cw, "_colab_run", run), \
                 mock.patch("builtins.print"):
                self.assertEqual(cw.main(["init", "ssh"]), 0)
        command = " ".join(run.calls[0])
        self.assertIn("ssh-keygen -t ed25519", command)
        self.assertIn("/app/.colab/.ssh/id_ed25519", command)


class CallScriptTest(unittest.TestCase):
    def test_argv_is_restored(self):
        """cw jobs は2本続けて呼ぶ。sys.argv を戻さないと2本目が壊れる。"""
        before = list(sys.argv)
        seen = {}

        module = mock.MagicMock()
        module.main.side_effect = lambda: seen.update(argv=list(sys.argv)) or 0
        with mock.patch.object(cw, "_load_script", return_value=module):
            self.assertEqual(cw._call_script("generate_image", "cw jobs", ["status"]), 0)

        self.assertEqual(seen["argv"], ["cw jobs", "status"])
        self.assertEqual(sys.argv, before)

    def test_argv_is_restored_after_a_failure(self):
        module = mock.MagicMock()
        module.main.side_effect = RuntimeError("boom")
        before = list(sys.argv)
        with mock.patch.object(cw, "_load_script", return_value=module):
            with self.assertRaises(RuntimeError):
                cw._call_script("generate_image", "cw image", ["submit", "x"])
        self.assertEqual(sys.argv, before)


if __name__ == "__main__":
    unittest.main()
