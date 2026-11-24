#!/usr/bin/env python3
"""Tests for the TOML settings files (hypermaze/config.py), --config on each entry point, and scripts/hm.
Run:  python3 -m unittest discover -s tests -v   (from the repo root)"""
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from hypermaze import config  # noqa: E402
from test_arena import Keeper, HOUSE  # noqa: E402

HAVE_TOML = sys.version_info >= (3, 11)
HM = os.path.join(ROOT, "scripts", "hm")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return path


def clean_env(home, **extra):
    """An environment with a throwaway HOME and none of the variables that would override settings."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("HOUSE_TOKEN", "HOUSE_SERVER", "HARNESS_KEY", "SSL_CERT_FILE", "MODEL",
                        "ARENA_ADMIN_KEY", "ARENA_VIEWER_KEY", "XDG_CONFIG_HOME", "HM_HOME", "HM_REPO", "HOUSE_CONFIG")}
    env.update(HOME=home, PYTHONPATH=ROOT, **extra)
    return env


def wait_port(port, timeout=10):
    t = time.time() + timeout
    while time.time() < t:
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            return True
        except OSError:
            time.sleep(0.05)
    return False


def stop(p):
    p.kill()
    p.wait(5)
    for s in (p.stdout, p.stderr):
        if s:
            s.close()


@unittest.skipUnless(HAVE_TOML, "settings files need Python 3.11+")
class TestLoad(unittest.TestCase):
    def test_examples_parse_and_set_nothing(self):
        for c in config.COMPONENTS:
            self.assertEqual(config.load(c, os.path.join(ROOT, "config", f"{c}.example.toml")), {}, c)

    def test_examples_uncommented_parse(self):
        # every commented "# key = value" line in the examples is a valid setting
        for c in config.COMPONENTS:
            with open(os.path.join(ROOT, "config", f"{c}.example.toml")) as f:
                lines = [ln[2:] for ln in f.read().splitlines() if ln.startswith("# ") and " = " in ln
                         and ln[2:].split(" = ")[0].isidentifier()]
            got = config.parse("\n".join(lines), c)
            self.assertTrue(got, c)
            self.assertLessEqual(set(got), set(config.SCHEMA[c]), c)

    def test_types_paths_and_errors(self):
        got = config.parse('data = "~/kd"\nadmin = "127.0.0.1:1"', "keeper")
        self.assertEqual(got["data"], os.path.expanduser("~/kd"))
        self.assertEqual(config.parse("temperature = 1", "ant")["temperature"], 1.0)
        for bad in ('nope = 1', 'threads = "4"', 'threads = true', 'persona = "Bob"', 'data = [', ):
            comp = "keeper" if bad.startswith(("nope", "data")) else "ant"
            with self.assertRaises(config.ConfigError, msg=bad):
                config.parse(bad, comp)
        with self.assertRaises(config.ConfigError):
            config.parse('mode = "loud"', "runner")

    def test_default_path_only_if_present(self):
        home = tempfile.mkdtemp()
        old = os.environ.get("HOME")
        os.environ["HOME"] = home
        try:
            self.assertEqual(config.load("viewer"), {})
            write(os.path.join(home, "hypermaze", "config", "viewer.toml"), 'listen = "127.0.0.1:9"\n')
            self.assertEqual(config.load("viewer"), {"listen": "127.0.0.1:9"})
            with self.assertRaises(config.ConfigError):
                config.load("viewer", os.path.join(home, "missing.toml"))
        finally:
            os.environ["HOME"] = old

    def test_pick_precedence(self):
        os.environ["HM_TEST_VAR"] = "env"
        try:
            self.assertEqual(config.pick("cli", "HM_TEST_VAR", {"k": "file"}, "k", "def"), "cli")
            self.assertEqual(config.pick(None, "HM_TEST_VAR", {"k": "file"}, "k", "def"), "env")
            os.environ["HM_TEST_VAR"] = ""
            self.assertEqual(config.pick(None, "HM_TEST_VAR", {"k": "file"}, "k", "def"), "file")
            self.assertEqual(config.pick(None, "HM_TEST_VAR", {}, "k", "def"), "def")
            self.assertEqual(config.pick(0, None, {"k": 5}, "k", 9), 0)   # a given 0 is not "unset"
        finally:
            del os.environ["HM_TEST_VAR"]


@unittest.skipUnless(HAVE_TOML, "settings files need Python 3.11+")
class TestLlmServerCommand(unittest.TestCase):
    """ant.llm_server --dry-run shows the precedence plainly: flag > $MODEL > file > default."""

    def run_it(self, cfg_text, *args, **env):
        home = tempfile.mkdtemp()
        p = write(os.path.join(home, "ant.toml"), cfg_text)
        r = subprocess.run([sys.executable, "-m", "ant.llm_server", "--config", p, "--dry-run", *args],
                           cwd=ROOT, env=clean_env(home, **env), capture_output=True, text=True, timeout=30)
        return r.returncode, r.stdout.split(), r.stderr

    def test_precedence(self):
        cfg = 'model = "/m/file.gguf"\nthreads = 2\nllama_server = "/opt/ls"\n'
        code, cmd, _ = self.run_it(cfg)
        self.assertEqual(code, 0)
        self.assertEqual(cmd[:3], ["/opt/ls", "-m", "/m/file.gguf"])
        self.assertEqual(cmd[cmd.index("-t") + 1], "2")
        self.assertEqual(cmd[cmd.index("--port") + 1], "8095")         # built-in default
        self.assertEqual(cmd[cmd.index("--host") + 1], "127.0.0.1")
        _, cmd, _ = self.run_it(cfg, MODEL="/env/m.gguf")
        self.assertEqual(cmd[2], "/env/m.gguf")                          # env beats file
        _, cmd, _ = self.run_it(cfg, "--model", "/cli/m.gguf", "--threads", "3", MODEL="/env/m.gguf")
        self.assertEqual((cmd[2], cmd[cmd.index("-t") + 1]), ("/cli/m.gguf", "3"))   # flag beats both

    def test_bad_file_stops_early(self):
        code, _, err = self.run_it('model = 3\n')
        self.assertNotEqual(code, 0)
        self.assertIn("model should be a str", err)


@unittest.skipUnless(HAVE_TOML, "settings files need Python 3.11+")
class TestKeeperAndViewer(unittest.TestCase):
    def test_keeper_from_file_and_flag_override(self):
        home = tempfile.mkdtemp()
        data = os.path.join(home, "kd")
        c, a, h, a2 = free_port(), free_port(), free_port(), free_port()
        cfg = write(os.path.join(home, "keeper.toml"),
                    f'data = "{data}"\ncontestant = "127.0.0.1:{c}"\nadmin = "127.0.0.1:{a}"\n'
                    f'harness = "127.0.0.1:{h}"\nkeys_file = "{home}/secret/keys.json"\n')
        r = subprocess.run([sys.executable, "-m", "arena", "--config", cfg, "keys"], cwd=ROOT,
                           env=clean_env(home), capture_output=True, text=True, timeout=30)
        self.assertEqual(r.stdout.strip(), f"{home}/secret/keys.json")
        self.assertEqual(stat.S_IMODE(os.stat(f"{home}/secret/keys.json").st_mode), 0o600)
        # --config after the subcommand works too; the --admin flag beats the file
        p = subprocess.Popen([sys.executable, "-m", "arena", "serve", "--config", cfg, "--admin", f"127.0.0.1:{a2}"],
                             cwd=ROOT, env=clean_env(home), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            line = p.stdout.readline()
            self.assertIn(f"contestant on 127.0.0.1:{c}, admin on 127.0.0.1:{a2}, harness on 127.0.0.1:{h}", line)
            self.assertIn(f"data in {data}", line)
            self.assertFalse(os.path.exists(os.path.join(data, "keys.json")))   # keys_file was used instead
        finally:
            stop(p)

    def test_viewer_from_file(self):
        home = tempfile.mkdtemp()
        kp = Keeper()
        try:
            keys = dict(kp.k.keys, viewer="v" * 16)
            kf = write(os.path.join(home, "keys.json"), json.dumps(keys))
            port = free_port()
            cfg = write(os.path.join(home, "viewer.toml"),
                        f'listen = "127.0.0.1:{port}"\nkeeper = "http://127.0.0.1:{kp.aport}"\nkeys_file = "{kf}"\n')
            p = subprocess.Popen([sys.executable, "-m", "arena.viewer", "--config", cfg], cwd=ROOT,
                                 env=clean_env(home), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                self.assertTrue(wait_port(port))
                kp.new_run(dims=2, size=3)
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/runs?key={'v' * 16}", timeout=10) as r:
                    self.assertEqual(len(json.load(r)["runs"]), 1)
            finally:
                stop(p)
        finally:
            kp.close()


@unittest.skipUnless(HAVE_TOML, "settings files need Python 3.11+")
class TestRunnerSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kp = Keeper()

    @classmethod
    def tearDownClass(cls):
        cls.kp.close()

    def play(self, args, env, lines):
        p = subprocess.run([sys.executable, HOUSE, *args], input="".join(ln + "\n" for ln in lines).encode("latin-1"),
                           capture_output=True, env=env, timeout=60)
        return p.stdout.decode("latin-1"), p.stderr.decode("latin-1"), p.returncode

    def test_same_screens_from_settings_file(self):
        """The game the contestant sees is byte-identical whether set up by flags/env or by the settings file,
        and reading the settings adds nothing to stdout or stderr."""
        home = tempfile.mkdtemp()
        lines = ["look", "write hello", "read", "next", "back", "Nowhere", "quit"]
        rid1, tok1 = self.kp.new_run(dims=2, size=4, seed=11)
        a, aerr, acode = self.play(["--notebook"], clean_env(home, HOUSE_TOKEN=tok1, HOUSE_SERVER=self.kp.url), lines)
        time.sleep(1.1)  # an identical admin request in the same second would reuse a single-use signature
        rid2, tok2 = self.kp.new_run(dims=2, size=4, seed=11)
        tf = write(os.path.join(home, "run.token"), tok2 + "\n")
        cfgf = write(os.path.join(home, "player", "runner.toml"),
                     f'server = "{self.kp.url}"\ntoken_file = "{tf}"\nmode = "notebook"\n')
        b, berr, bcode = self.play([], clean_env(home, HOUSE_CONFIG=cfgf), lines)
        self.assertEqual(a, b)
        self.assertEqual((aerr, berr), ("", ""))
        self.assertEqual(acode, bcode)

    def test_env_and_flags_beat_settings(self):
        home = tempfile.mkdtemp()
        _, tok = self.kp.new_run(dims=2, size=3, seed=5)
        plain, _, _ = self.play(["--notebook"], clean_env(home, HOUSE_TOKEN=tok, HOUSE_SERVER=self.kp.url), ["read", "quit"])
        time.sleep(1.1)
        _, tok = self.kp.new_run(dims=2, size=3, seed=5)
        cfg = write(os.path.join(home, "r.toml"), 'server = "http://127.0.0.1:9"\ntoken_file = "/nonexistent"\n'
                    'mode = "none"\n')
        out, err, code = self.play(["--config", cfg, "--notebook"],
                                   clean_env(home, HOUSE_TOKEN=tok, HOUSE_SERVER=self.kp.url), ["read", "quit"])
        self.assertIn("You are in the", out)
        self.assertEqual(out, plain)
        self.assertEqual(err, "")

    def test_bad_settings_go_to_stderr_only(self):
        home = tempfile.mkdtemp()
        cfg = write(os.path.join(home, "r.toml"), 'mode = "loud"\n')
        out, err, code = self.play(["--config", cfg], clean_env(home, HOUSE_TOKEN="x"), [])
        self.assertEqual(out, "")
        self.assertNotEqual(code, 0)
        self.assertIn("settings", err)


@unittest.skipUnless(HAVE_TOML, "settings files need Python 3.11+")
class TestAntSettings(unittest.TestCase):
    def test_pi_runner_reads_secrets_by_path(self):
        """token_file, run_file and harness_key_file from ant.toml; turns reach the keeper's harness port."""
        home = tempfile.mkdtemp()
        kp = Keeper()
        llm_port = free_port()
        fake = subprocess.Popen([sys.executable, os.path.join(ROOT, "tests", "fake_llm.py"), str(llm_port)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.assertTrue(wait_port(llm_port))
            rid, tok = kp.new_run(dims=2, size=3, seed=3)
            tf = write(os.path.join(home, "run.token"), tok + "\n")
            rf = write(os.path.join(home, "run.id"), rid + "\n")
            hf = write(os.path.join(home, "harness.key"), kp.k.keys["harness"] + "\n")
            cfg = write(os.path.join(home, "ant.toml"),
                        f'llm = "http://127.0.0.1:{llm_port}"\npersona = "Harry"\nmax_turns = 3\n'
                        f'house = "{kp.url}"\nreport = "http://127.0.0.1:{kp.hport}"\n'
                        f'token_file = "{tf}"\nrun_file = "{rf}"\nharness_key_file = "{hf}"\n')
            r = subprocess.run([sys.executable, "-m", "ant.pi_runner", "--config", cfg], cwd=ROOT,
                               env=clean_env(home), capture_output=True, text=True, timeout=60)
            self.assertIn("turns=3", r.stdout, r.stderr)
            self.assertNotIn("report failed", r.stdout)
            st, log = kp.admin("GET", f"/admin/runs/{rid}/log")
            text = log.decode() if isinstance(log, bytes) else json.dumps(log)
            evs = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
            turns = [e for e in evs if e.get("t") == "harness"]
            self.assertEqual(len(turns), 3)
            self.assertEqual(turns[0]["where"], "Harry")
        finally:
            stop(fake)
            kp.close()


FAKE_LLAMA = """#!/usr/bin/env python3
# Stand-in for llama-server: answers /slots with the state in a file next to it ("busy" or "idle").
import http.server, json, os, sys
port = int(sys.argv[sys.argv.index("--port") + 1])
state = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        busy = open(state).read().strip() == "busy" if os.path.exists(state) else False
        body = json.dumps([{"id": 0, "is_processing": busy}]).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
"""


class TestIdleDecision(unittest.TestCase):
    """ant.llm_server's idle logic, with fake /slots answers and timestamps."""

    def setUp(self):
        from ant import llm_server
        self.L = llm_server

    def test_slots_busy(self):
        L = self.L
        self.assertTrue(L.slots_busy([{"id": 0, "is_processing": True}]))
        self.assertFalse(L.slots_busy([{"id": 0, "is_processing": False}, {"id": 1, "is_processing": False}]))
        self.assertTrue(L.slots_busy([{"id": 0, "state": 1}]))           # older llama.cpp
        self.assertFalse(L.slots_busy([{"id": 0, "state": 0}]))
        for bad in (None, [], {"error": "loading"}, [{"id": 0}], ["x"]):
            self.assertIsNone(L.slots_busy(bad), bad)

    def test_is_busy_unknown_counts_as_busy(self):
        L = self.L
        self.assertTrue(L.is_busy(True, False))
        self.assertFalse(L.is_busy(False, True))     # /slots says idle: that wins
        self.assertTrue(L.is_busy(None, True))       # loading, a run is on
        self.assertFalse(L.is_busy(None, False))     # can't read /slots, no run
        self.assertTrue(L.is_busy(None, None))       # can't tell at all: keep it

    def test_should_stop(self):
        L = self.L
        t0 = 1000.0
        self.assertFalse(L.should_stop(t0, t0 + 299, 5))
        self.assertTrue(L.should_stop(t0, t0 + 300, 5))
        self.assertTrue(L.should_stop(t0, t0 + 3600, 1))
        self.assertFalse(L.should_stop(t0, t0 + 10 ** 6, 0))   # 0 = never

    @unittest.skipUnless(HAVE_TOML, "settings files need Python 3.11+")
    def test_supervisor_stops_idle_server_and_keeps_busy_one(self):
        home = tempfile.mkdtemp()
        fake = write(os.path.join(home, "llama-server"), FAKE_LLAMA.replace("/usr/bin/env python3", sys.executable, 1))
        os.chmod(fake, 0o755)
        port = free_port()
        cfg = write(os.path.join(home, "ant.toml"), f'model = "{home}/m.gguf"\nllama_server = "{fake}"\n'
                    f'llm_port = {port}\nidle_minutes = 5\n')
        env = clean_env(home, PATH=os.path.join(home, "nobin"))  # no systemctl: unknown counts as busy
        args = [sys.executable, "-m", "ant.llm_server", "--config", cfg, "--poll", "0.2", "--idle-seconds", "1.5"]

        write(os.path.join(home, "state"), "busy")
        p = subprocess.Popen(args, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(wait_port(port))
            time.sleep(3)
            self.assertIsNone(p.poll(), "a busy server must keep running")
            write(os.path.join(home, "state"), "idle")
            code = p.wait(timeout=15)
            self.assertEqual(code, 0)                    # clean exit: Restart=on-failure leaves it down
            self.assertIn("idle", p.stderr.read())
            self.assertFalse(wait_port(port, timeout=1))  # llama-server went with it
        finally:
            if p.poll() is None:
                stop(p)
            else:
                p.stdout.close(); p.stderr.close()

    @unittest.skipUnless(HAVE_TOML, "settings files need Python 3.11+")
    def test_dry_run_shows_idle_setting(self):
        home = tempfile.mkdtemp()
        cfg = write(os.path.join(home, "ant.toml"), 'model = "/m.gguf"\nidle_minutes = 0\n')
        r = subprocess.run([sys.executable, "-m", "ant.llm_server", "--config", cfg, "--dry-run"], cwd=ROOT,
                           env=clean_env(home), capture_output=True, text=True, timeout=30)
        self.assertIn("idle stop: never", r.stdout)
        r = subprocess.run([sys.executable, "-m", "ant.llm_server", "--config", cfg, "--dry-run", "--idle-minutes", "7"],
                           cwd=ROOT, env=clean_env(home), capture_output=True, text=True, timeout=30)
        self.assertIn("idle stop: after 7 min", r.stdout)


@unittest.skipUnless(HAVE_TOML, "settings files need Python 3.11+")
class TestHm(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        # stand-ins for systemctl/journalctl so nothing touches the real user manager
        self.bin = os.path.join(self.home, "bin")
        self.calls = os.path.join(self.home, "calls")
        for name in ("systemctl", "journalctl"):
            p = write(os.path.join(self.bin, name), f'#!/bin/sh\necho "{name} $*" >> "{self.calls}"\n')
            os.chmod(p, 0o755)
        self.env = clean_env(self.home, PATH=self.bin + os.pathsep + os.environ["PATH"],
                             EDITOR="true", XDG_CONFIG_HOME=os.path.join(self.home, ".config"))

    def hm(self, *args):
        return subprocess.run([sys.executable, HM, *args], env=self.env, capture_output=True, text=True, timeout=120)

    def test_dry_run_changes_nothing(self):
        r = self.hm("--dry-run", "install", "ant")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("git clone https://github.com/mbwamkali/AiPiMazeTest", r.stdout)
        self.assertIn("hypermaze-llm.service", r.stdout)
        self.assertIn("hypermaze-ant.service", r.stdout)
        self.assertEqual(sorted(os.listdir(self.home)), ["bin"])

    def test_install_keeps_existing_config_and_writes_units(self):
        if not shutil.which("git") or not os.path.isdir(os.path.join(ROOT, ".git")):
            self.skipTest("needs git and a git checkout")
        r = self.hm("--repo", ROOT, "install", "keeper")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        cfg = os.path.join(self.home, "hypermaze", "config", "keeper.toml")
        with open(cfg) as f, open(os.path.join(ROOT, "config", "keeper.example.toml")) as g:
            self.assertEqual(f.read(), g.read())
        with open(cfg, "a") as f:
            f.write('admin = "127.0.0.1:7342"\n')
        r = self.hm("--repo", ROOT, "install", "keeper")    # second time: pull, keep the edited file
        self.assertIn("not overwritten", r.stdout)
        with open(cfg) as f:
            self.assertIn('admin = "127.0.0.1:7342"', f.read())
        unit = os.path.join(self.home, ".config", "systemd", "user", "hypermaze-keeper.service")
        with open(unit) as f:
            text = f.read()
        self.assertIn(f"-m arena serve --config {cfg}", text)
        self.assertIn("UMask=0077", text)
        self.assertIn("WantedBy=default.target", text)
        self.assertIn(f"WorkingDirectory={os.path.join(self.home, 'hypermaze', 'app')}", text)
        r = self.hm("install", "runner")
        self.assertIn("settings only, no service", r.stdout)
        self.hm("start", "keeper")
        self.hm("update")
        with open(self.calls) as f:
            calls = f.read()
        self.assertIn("systemctl --user enable hypermaze-keeper", calls)
        self.assertIn("systemctl --user start hypermaze-keeper", calls)
        self.assertIn("systemctl --user try-restart hypermaze-keeper", calls)
        r = self.hm("config", "edit", "keeper")
        self.assertIn("OK:", r.stdout)

    def test_ant_units_on_demand(self):
        r = self.hm("--repo", ROOT, "install", "ant")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        d = os.path.join(self.home, ".config", "systemd", "user")
        with open(os.path.join(d, "hypermaze-llm.service")) as f:
            llm = f.read()
        with open(os.path.join(d, "hypermaze-ant.service")) as f:
            ant = f.read()
        self.assertNotIn("[Install]", llm)                 # not started at boot
        self.assertIn("Restart=on-failure", llm)           # a clean idle exit stays down
        self.assertIn("Wants=hypermaze-llm.service", ant)
        self.assertNotIn("Requires=", ant)
        self.assertTrue(all("UMask=0077" in t for t in (llm, ant)))
        with open(self.calls) as f:
            calls = f.read()
        self.assertNotIn("enable", calls)                  # nothing of the ant is enabled
        self.hm("start", "ant")
        self.hm("stop", "ant")
        self.hm("update")
        with open(self.calls) as f:
            calls = f.read().splitlines()
        self.assertIn("systemctl --user start hypermaze-ant", calls)
        self.assertIn("systemctl --user stop hypermaze-ant hypermaze-llm", calls)
        self.assertFalse([c for c in calls if "restart" in c and ("llm" in c or "hypermaze-ant" in c)])

    def test_config_masks_and_checks(self):
        d = os.path.join(self.home, "hypermaze", "config")
        write(os.path.join(d, "ant.toml"), 'harness_key_file = "/k/harness.key"\ntoken_file = "/k/t"\n'
              'llm = "http://127.0.0.1:8095"\n# leaked = "0123456789abcdef0123456789abcdef"\n')
        r = self.hm("config")
        self.assertNotIn("/k/harness.key", r.stdout)
        self.assertNotIn("/k/t\"", r.stdout)
        self.assertNotIn("0123456789abcdef0123456789abcdef", r.stdout)
        self.assertIn('llm = "http://127.0.0.1:8095"', r.stdout)
        r = self.hm("config", "edit", "ant")     # EDITOR=true leaves it as is; model is required
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("model must be set", r.stdout)
        write(os.path.join(d, "viewer.toml"), "listen = [\n")
        r = self.hm("config", "check", "viewer")
        self.assertIn("NOT OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
