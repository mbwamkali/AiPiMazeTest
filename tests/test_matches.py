#!/usr/bin/env python3
"""Tests for starting matches from the Colosseum: the keeper's match queue, the ant agent's pick-up,
the viewer's new-match call, and the ant-only bundle that `hm deploy ant` pushes.
Run:  python3 -m unittest discover -s tests -v   (from the repo root)"""
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from arena import auth  # noqa: E402
from arena.dispatch import Dispatch, MatchError, check_request, EXPIRE  # noqa: E402
from ant import agent  # noqa: E402
from test_arena import Keeper  # noqa: E402

MATCH = {"size": 5, "loops": 0.1, "seed": 42, "max_moves": 50, "generator": "dfs", "ants": ["mbclock", "pi-two"]}


class TestDispatch(unittest.TestCase):
    def test_check_request(self):
        r = check_request(dict(MATCH, seed=""))
        self.assertIsNone(r["seed"])
        for bad in ({"size": 1}, {"size": 61}, {"loops": 0.9}, {"max_moves": -1}, {"ants": []},
                    {"ants": ["Bad Name"]}, {"ants": "mbclock"}, {"size": "x"}, {"seed": -1}, {"size": True}):
            with self.assertRaises(MatchError, msg=bad):
                check_request(dict(MATCH, **bad))
        self.assertEqual(check_request(dict(MATCH, ants=["a", "a"]))["ants"], ["a"])

    def test_take_once_only_when_free_and_expire(self):
        now = [1000.0]
        stopped = []
        d = Dispatch(stop_run=stopped.append, clock=lambda: now[0])
        d.add("mbclock", "r1", "t" * 20, "m1")
        d.add("pi-two", "r2", "u" * 20, "m1")
        self.assertEqual(d.take("mbclock", busy=True), {"run_id": None})     # busy: nothing
        self.assertEqual(d.take("mbclock", busy=False), {"run_id": "r1", "token": "t" * 20})
        self.assertEqual(d.take("mbclock", busy=False), {"run_id": None})    # handed over once
        v = d.view()
        self.assertNotIn("token", json.dumps(v))
        self.assertEqual({q["run_id"]: q["status"] for q in v["queue"]}, {"r1": "claimed", "r2": "waiting"})
        self.assertTrue(v["agents"][0]["listening"])
        now[0] += EXPIRE + 1
        self.assertEqual(d.take("pi-two", busy=False), {"run_id": None})     # too late
        self.assertEqual(stopped, ["r2"])
        self.assertEqual([q["status"] for q in d.view()["queue"] if q["run_id"] == "r2"], ["missed"])


class TestKeeperMatches(unittest.TestCase):
    def setUp(self):
        self.k = Keeper()

    def tearDown(self):
        self.k.close()

    def take(self, name, busy=False):
        return self.k.admin("POST", f"/agents/{name}/next", {"busy": busy, "n": os.urandom(4).hex()},
                            role="harness", port=self.k.hport)

    def test_match_to_ant(self):
        st, made = self.k.admin("POST", "/admin/matches", MATCH)
        self.assertEqual(st, 200, made)
        self.assertEqual([r["agent"] for r in made["runs"]], ["mbclock", "pi-two"])
        self.assertNotIn("token", json.dumps(made))
        runs = self.k.k.store.runs
        r0, r1 = (runs[r["run_id"]] for r in made["runs"])
        self.assertEqual(r0.settings["seed"], 42)
        self.assertEqual((r0.settings["size"], r0.settings["dims"], r0.rulebook["max_moves"]), (5, 2, 50))
        self.assertEqual(r0.world.to_dict()["rooms"], r1.world.to_dict()["rooms"])      # same maze
        self.assertEqual((r0.settings["match"], r0.settings["agent"]), (made["match"], "mbclock"))
        # the agent gets the run id and token, nothing else
        st, got = self.take("mbclock")
        self.assertEqual(st, 200)
        self.assertEqual(set(got), {"run_id", "token"})
        self.assertEqual(got["run_id"], made["runs"][0]["run_id"])
        # the token plays that run on the contestant port, which shows only the room and doors
        st, room = self.k.room(got["token"])
        self.assertEqual(st, 200)
        self.assertNotIn("match", json.dumps(room))
        self.assertNotIn("mbclock", json.dumps(room))
        self.assertEqual(self.take("mbclock")[1], {"run_id": None})
        st, v = self.k.admin("GET", "/admin/agents")
        self.assertEqual(st, 200)
        self.assertEqual([a["name"] for a in v["agents"]], ["mbclock"])
        self.assertNotIn(got["token"], json.dumps(v))

    def test_random_seed_shared(self):
        st, made = self.k.admin("POST", "/admin/matches", dict(MATCH, seed=None))
        runs = [self.k.k.store.runs[r["run_id"]] for r in made["runs"]]
        self.assertEqual(runs[0].settings["seed"], runs[1].settings["seed"])
        self.assertEqual(made["seed"], runs[0].settings["seed"])

    def test_refusals(self):
        self.assertEqual(self.k.admin("POST", "/admin/matches", dict(MATCH, size=0))[0], 400)
        self.assertEqual(self.k.admin("POST", "/admin/matches", dict(MATCH, generator="nope"))[0], 400)
        # only the admin key makes matches; only the harness key asks for work; the contestant port knows neither
        self.assertEqual(self.k.admin("POST", "/admin/matches", MATCH, role="harness")[0], 404)
        self.assertEqual(self.k.admin("POST", "/agents/mbclock/next", {"busy": False}, role="admin",
                                      port=self.k.hport)[0], 404)
        self.assertEqual(self.k.admin("POST", "/agents/mbclock/next", {}, port=self.k.cport)[0], 404)
        self.assertEqual(self.k.admin("GET", "/admin/agents", role="harness")[0], 404)
        self.assertEqual(self.take("Not_OK")[0], 404)


class TestAgent(unittest.TestCase):
    def setUp(self):
        self.k = Keeper()
        self.dir = tempfile.mkdtemp()
        self.url = f"http://127.0.0.1:{self.k.hport}"
        self.key = self.k.k.keys["harness"]

    def tearDown(self):
        self.k.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def once(self, busy=False):
        started = []
        rid = agent.once(self.url, self.key, "mbclock", os.path.join(self.dir, "run.id"),
                         os.path.join(self.dir, "run.token"), is_busy=lambda: busy,
                         do_start=lambda: started.append(1) or True, log=lambda s: None)
        return rid, started

    def test_pick_up_and_start(self):
        self.assertEqual(self.once(), (None, []))                 # nothing waiting
        st, made = self.k.admin("POST", "/admin/matches", dict(MATCH, ants=["mbclock"]))
        self.assertEqual(self.once(busy=True), (None, []))        # busy: left waiting
        rid, started = self.once()
        self.assertEqual((rid, started), (made["runs"][0]["run_id"], [1]))
        for fn in ("run.id", "run.token"):
            p = os.path.join(self.dir, fn)
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        with open(os.path.join(self.dir, "run.id")) as f:
            self.assertEqual(f.read().strip(), rid)
        with open(os.path.join(self.dir, "run.token")) as f:
            self.assertEqual(self.k.room(f.read().strip())[0], 200)
        self.assertEqual(self.once(), (None, []))                 # handed over once


class TestViewerNewMatch(unittest.TestCase):
    def serve(self, allow):
        from http.server import ThreadingHTTPServer
        from arena import viewer
        page = os.path.join(ROOT, "viewer", "colosseum.html")
        relay = viewer.Relay(f"http://127.0.0.1:{self.k.aport}", self.k.k.keys["admin"], page, page, None, allow)
        h = type("H", (viewer.Handler,), {"relay": relay, "viewer_key": "vk", "log_message": lambda *a: None})
        srv = ThreadingHTTPServer(("127.0.0.1", 0), h)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.servers.append(srv)
        return srv.server_address[1]

    def setUp(self):
        self.k = Keeper()
        self.servers = []

    def tearDown(self):
        for s in self.servers:
            s.shutdown()
            s.server_close()
        self.k.close()

    def call(self, port, path, method="GET", body=None):
        st, data, _ = self.k.req(port, method, path, body)
        return st, json.loads(data or b"{}")

    def test_off_by_default(self):
        p = self.serve(False)
        self.assertEqual(self.call(p, "/api/agents?key=vk")[0], 404)
        self.assertEqual(self.call(p, "/api/new-match?key=vk", "POST", MATCH)[0], 405)
        self.assertEqual(self.k.k.store.runs, {})

    def test_start_match(self):
        p = self.serve(True)
        self.assertEqual(self.call(p, "/api/new-match", "POST", MATCH)[0], 401)       # needs the viewer key
        st, made = self.call(p, "/api/new-match?key=vk", "POST", MATCH)
        self.assertEqual(st, 201, made)
        self.assertEqual(len(made["runs"]), 2)
        self.assertNotIn("token", json.dumps(made))
        st, err = self.call(p, "/api/new-match?key=vk", "POST", dict(MATCH, size=999))
        self.assertEqual(st, 400)
        self.assertIn("size", err["error"])
        st, v = self.call(p, "/api/agents?key=vk")
        self.assertEqual(st, 200)
        self.assertEqual([q["status"] for q in v["queue"]], ["waiting", "waiting"])
        self.assertNotIn("token", json.dumps(v))
        st, runs = self.call(p, "/api/runs?key=vk")
        self.assertEqual({r["settings"]["agent"] for r in runs["runs"]}, {"mbclock", "pi-two"})


def load_hm():
    path = os.path.join(ROOT, "scripts", "hm")
    loader = importlib.machinery.SourceFileLoader("hm_script", path)
    spec = importlib.util.spec_from_loader("hm_script", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class TestAntBundle(unittest.TestCase):
    """What `hm deploy ant` puts on an ant machine: only what the ant needs, nothing that tells it about
    the keeper, the Colosseum or being watched, and it still runs on its own."""
    WORDS = re.compile(r"keeper|colosseum|viewer|watch(?!dog)|admin|director|audience|arena|spectat|edit_|"
                       r"rulebook|matches_dir|github|AiPiMazeTest", re.I)

    @classmethod
    def setUpClass(cls):
        hm = load_hm()
        h = hm.HM(tempfile.mkdtemp(), hm.REPO_URL, dry=True)
        cls.bundle = hm.ant_bundle(h, python="/usr/bin/python3", app="/home/ant/hypermaze/app",
                                   cfg="/home/ant/hypermaze/config/ant.toml")
        cls.dir = tempfile.mkdtemp()
        for rel, data in cls.bundle.items():
            p = os.path.join(cls.dir, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_only_ant_files(self):
        tops = {p.split("/")[0] for p in self.bundle}
        self.assertEqual(tops, {"ant", "runner", "hypermaze", "config", "units", "LICENSE"})
        for p in self.bundle:
            self.assertFalse(p.startswith(("arena/", "viewer/", "tools/", "scripts/", ".git")), p)
        self.assertNotIn("ant/bots.py", self.bundle)
        self.assertNotIn("ant/lessons.py", self.bundle)
        self.assertNotIn("hypermaze/core.py", self.bundle)

    def test_no_telltale_words(self):
        for rel, data in self.bundle.items():
            if rel.startswith("units/") or rel == "LICENSE":
                continue
            hit = self.WORDS.search(data.decode("utf-8"))
            self.assertIsNone(hit, f"{rel}: {hit and hit.group(0)!r}")

    def test_runs_on_its_own(self):
        code = ("import sys; sys.path[:0] = [%r, %r]\n"
                "import ant.pi_runner, ant.agent, ant.llm_server, ant.notes\n"
                "from hypermaze import config\n"
                "assert set(config.COMPONENTS) == {'runner', 'ant'}, config.COMPONENTS\n"
                "assert 'agent_name' in config.SCHEMA['ant']\n"
                "assert not any(m.startswith(('arena', 'hypermaze.core', 'hypermaze.game', 'ant.lessons')) "
                "for m in sys.modules), sorted(sys.modules)\n" % (self.dir, os.path.join(self.dir, "runner")))
        r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_units(self):
        units = {p: d.decode() for p, d in self.bundle.items() if p.startswith("units/")}
        self.assertEqual(set(units), {"units/hypermaze-ant.service", "units/hypermaze-llm.service",
                                      "units/hypermaze-ant-agent.service"})
        a = units["units/hypermaze-ant-agent.service"]
        self.assertIn("ExecStart=/usr/bin/python3 -m ant.agent --config /home/ant/hypermaze/config/ant.toml", a)
        self.assertIn("WorkingDirectory=/home/ant/hypermaze/app", a)
        self.assertIn("WantedBy=default.target", a)
        self.assertNotIn("WantedBy", units["units/hypermaze-ant.service"])


if __name__ == "__main__":
    unittest.main()
