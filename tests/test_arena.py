#!/usr/bin/env python3
"""Tests for the arena (keeper servers, edits, rulebook, persistence) and the runner.
Run:  python3 -m unittest discover -s tests -v   (from the repo root)"""
import http.client
import json
import os
import random
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from hypermaze import Maze, Game  # noqa: E402
from arena import auth, server  # noqa: E402
from arena.runs import Store  # noqa: E402
from arena.rulebook import RuleError, make  # noqa: E402


def abc(names):
    """Door names as the player gets them: alphabetical."""
    return sorted(names, key=lambda d: (d.lower(), d))

HOUSE = os.path.join(ROOT, "runner", "house.py")


class Keeper:
    """A keeper on free local ports with a throwaway data folder."""

    def __init__(self, tls=None):
        self.dir = tempfile.mkdtemp()
        self.k, self.s1, self.s2, self.s3 = server.serve(self.dir, ("127.0.0.1", 0), ("127.0.0.1", 0), ("127.0.0.1", 0),
                                                         tls=tls)
        self.cport, self.aport, self.hport = (s.server_address[1] for s in (self.s1, self.s2, self.s3))
        self.url = f"http://127.0.0.1:{self.cport}"

    def close(self):
        for s in (self.s1, self.s2, self.s3):
            s.shutdown()
            s.server_close()

    def req(self, port, method, path, body=None, headers=None, raw=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        b = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
        c.request(method, path, body=b, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data, dict(r.getheaders())

    def admin(self, method, path, body=None, role="admin", ts=None, port=None):
        b = json.dumps(body).encode() if body is not None else b""
        ts, sig = auth.sign(self.k.keys[role], method, path, b, ts)
        st, data, _ = self.req(port or self.aport, method, path, raw=b, headers={"X-Time": ts, "X-Signature": sig})
        try:
            return st, json.loads(data)
        except ValueError:
            return st, data

    @staticmethod
    def bearer(tok):
        return {"Authorization": "Bearer " + tok}

    def new_run(self, **kw):
        kw.setdefault("dims", 3)
        kw.setdefault("size", 3)
        kw.setdefault("seed", 7)
        st, r = self.admin("POST", "/admin/runs", kw)
        assert st == 200, r
        return r["run_id"], r["token"]

    def room(self, tok):
        st, d, _ = self.req(self.cport, "GET", "/v1/room", headers={"Authorization": "Bearer " + tok})
        return st, json.loads(d)

    def walk(self, tok, name):
        st, d, _ = self.req(self.cport, "POST", "/v1/walk", {"to": name}, {"Authorization": "Bearer " + tok})
        return st, json.loads(d)


class TestDefaults2D(unittest.TestCase):
    def test_new_run_defaults_to_2d(self):
        store = Store(tempfile.mkdtemp())
        run, _tok = store.create(seed=5)
        self.assertEqual((run.settings["dims"], run.settings["size"], run.settings["rooms"]), (2, 8, 64))


class TestKeeperAPI(unittest.TestCase):
    def setUp(self):
        self.kp = Keeper()
        self.rid, self.tok = self.kp.new_run()

    def tearDown(self):
        self.kp.close()

    def test_room_shows_only_the_current_room(self):
        st, v = self.kp.room(self.tok)
        self.assertEqual(st, 200)
        self.assertEqual(set(v), {"room", "doors", "walls", "marks", "turn", "messages", "ended"})
        m = Maze(3, 3, 0, 7)
        self.assertEqual(v["room"], m.names[m.start])
        self.assertEqual(v["doors"], abc(m.names[x] for x in m.adj[m.start]))  # alphabetical: no direction given away

    def test_walk_and_no_door(self):
        _, v = self.kp.room(self.tok)
        st, v2 = self.kp.walk(self.tok, v["doors"][0])
        self.assertEqual((st, v2["room"], v2["turn"]), (200, v["doors"][0], 1))
        st, e = self.kp.walk(self.tok, "Nowhere At All")
        self.assertEqual((st, e), (400, {"error": "no_door"}))
        st, e = self.kp.walk(self.tok, 5)
        self.assertEqual((st, e), (400, {"error": "bad_request"}))

    def test_uniform_404(self):
        kp, nf = self.kp, b'{"error":"not found"}'
        cases = [
            kp.req(kp.cport, "GET", "/v1/room"),
            kp.req(kp.cport, "GET", "/v1/room", headers={"Authorization": "Bearer nope"}),
            kp.req(kp.cport, "GET", "/v1/map", headers={"Authorization": "Bearer " + self.tok}),
            kp.req(kp.cport, "DELETE", "/v1/room", headers={"Authorization": "Bearer " + self.tok}),
            kp.req(kp.cport, "GET", "/admin/runs"),
            kp.req(kp.aport, "GET", "/admin/runs"),
            kp.req(kp.aport, "GET", "/v1/room", headers={"Authorization": "Bearer " + self.tok}),
        ]
        for st, data, h in cases:
            self.assertEqual((st, data), (404, nf))
            self.assertEqual(h["Server"], "house")

    def test_signatures(self):
        kp = self.kp
        b = b""
        ts, sig = auth.sign(kp.k.keys["admin"], "GET", "/admin/runs", b)
        hd = {"X-Time": ts, "X-Signature": sig}
        self.assertEqual(kp.req(kp.aport, "GET", "/admin/runs", headers=hd)[0], 200)
        self.assertEqual(kp.req(kp.aport, "GET", "/admin/runs", headers=hd)[0], 404)  # replayed
        self.assertEqual(kp.admin("GET", "/admin/runs", ts=int(time.time()) - 120)[0], 404)  # expired
        ts, sig = auth.sign("00" * 32, "GET", "/admin/runs", b)
        self.assertEqual(kp.req(kp.aport, "GET", "/admin/runs", headers={"X-Time": ts, "X-Signature": sig})[0], 404)
        # signed for one path, used on another
        ts, sig = auth.sign(kp.k.keys["admin"], "GET", "/admin/runs", b)
        self.assertEqual(kp.req(kp.aport, "GET", f"/admin/runs/{self.rid}/map",
                                headers={"X-Time": ts, "X-Signature": sig})[0], 404)
        # body tampering
        body = json.dumps({"farewell": "a"}).encode()
        ts, sig = auth.sign(kp.k.keys["admin"], "POST", f"/admin/runs/{self.rid}/stop", body)
        self.assertEqual(kp.req(kp.aport, "POST", f"/admin/runs/{self.rid}/stop", raw=b'{"farewell": "b"}',
                                headers={"X-Time": ts, "X-Signature": sig})[0], 404)

    def test_each_port_takes_only_its_own_key(self):
        kp, h = self.kp, self.kp.hport
        self.assertEqual(kp.admin("GET", "/admin/runs", role="harness")[0], 404)
        self.assertEqual(kp.admin("POST", f"/admin/runs/{self.rid}/stop", {}, role="harness")[0], 404)
        self.assertEqual(kp.admin("POST", f"/admin/runs/{self.rid}/edits", {"edit": {"type": "message", "text": "x"}},
                                  role="harness")[0], 404)
        self.assertEqual(kp.admin("GET", "/admin/runs", port=h)[0], 404)
        self.assertEqual(kp.admin("GET", "/admin/runs", role="harness", port=h)[0], 404)
        self.assertEqual(kp.admin("POST", f"/admin/runs/{self.rid}/stop", {}, role="harness", port=h)[0], 404)
        self.assertEqual(kp.admin("POST", f"/runs/{self.rid}/turn", {"turn": 1}, role="admin", port=h)[0], 404)
        self.assertEqual(kp.admin("POST", "/runs/nope/turn", {"turn": 1}, role="harness", port=h)[0], 404)
        st, r = kp.admin("POST", f"/runs/{self.rid}/turn", {"turn": 1, "sent": "Coral", "junk": 1}, role="harness", port=h)
        self.assertEqual((st, r["status"]), (200, "ok"))
        log = kp.admin("GET", f"/admin/runs/{self.rid}/log")[1].decode().splitlines()
        ev = json.loads(log[-1])
        self.assertEqual((ev["t"], ev["sent"]), ("harness", "Coral"))
        self.assertNotIn("junk", ev)

    def test_bad_create(self):
        self.assertEqual(self.kp.admin("POST", "/admin/runs", {"rulebook": {"nope": 1}})[0], 400)
        self.assertEqual(self.kp.admin("POST", "/admin/runs", {"rulebook": {"generator": "x"}})[0], 400)

    def test_feed(self):
        kp = self.kp
        p = f"/admin/runs/{self.rid}/feed?since=0"
        ts, sig = auth.sign(kp.k.keys["admin"], "GET", p)
        c = http.client.HTTPConnection("127.0.0.1", kp.aport, timeout=10)
        c.request("GET", p + f"&ts={ts}&sig={sig}")
        r = c.getresponse()
        self.assertEqual(r.status, 200)
        got = []

        def read():
            buf = b""
            while buf.count(b"\n\n") < 2:
                buf += r.fp.read1(4096)
            got.append(buf)

        t = threading.Thread(target=read, daemon=True)
        t.start()
        _, v = kp.room(self.tok)
        kp.walk(self.tok, v["doors"][0])
        t.join(5)
        self.assertTrue(got)
        self.assertIn(b"event: start", got[0])
        self.assertIn(b"event: walk", got[0])
        c.close()

    def test_stop_and_end(self):
        st, r = self.kp.admin("POST", f"/admin/runs/{self.rid}/stop", {"farewell": "Goodnight."})
        self.assertEqual(r["ended"], "stopped")
        _, v = self.kp.room(self.tok)
        self.assertEqual((v["ended"], v["farewell"]), ("stopped", "Goodnight."))
        _, v = self.kp.walk(self.tok, v["doors"][0])
        self.assertEqual(v["turn"], 0)  # no moves after the end

    def test_way_out(self):
        m = Maze(3, 3, 0, 7)
        for c in m.shortest_path()[1:]:
            st, v = self.kp.walk(self.tok, m.names[c])
        self.assertEqual((v["ended"], v["moves"]), ("way_out", m.shortest))
        self.assertEqual(set(v), {"room", "doors", "walls", "marks", "turn", "messages", "ended", "moves"})  # no shortest, no flags

    def test_notebook_device(self):
        st, v, _ = self.kp.req(self.kp.cport, "GET", "/v1/device/notebook", headers=self.kp.bearer(self.tok))
        self.assertEqual(st, 200)
        self.assertIn("Notebook, page 1 of", json.loads(v)["screen"])
        st, r, _ = self.kp.req(self.kp.cport, "POST", "/v1/device/notebook", {"line": "write Coral Hall is cold"},
                            headers=self.kp.bearer(self.tok))
        r = json.loads(r)
        self.assertIn("You write it on page 1, line 1", r["reply"])
        self.assertIn(" 1| Coral Hall is cold", r["screen"])
        st, r, _ = self.kp.req(self.kp.cport, "POST", "/v1/device/notebook", {"line": "page 2"}, headers=self.kp.bearer(self.tok))
        st, v, _ = self.kp.req(self.kp.cport, "GET", "/v1/device/notebook?page=1", headers=self.kp.bearer(self.tok))
        self.assertIn("Coral Hall is cold", json.loads(v)["screen"])
        st, v, _ = self.kp.req(self.kp.cport, "GET", "/v1/device/notebook", headers=self.kp.bearer(self.tok))
        self.assertIn("page 2 of", json.loads(v)["screen"])
        st, _, _ = self.kp.req(self.kp.cport, "POST", "/v1/device/notebook", {"line": 5}, headers=self.kp.bearer(self.tok))
        self.assertEqual(st, 400)
        st, _, _ = self.kp.req(self.kp.cport, "GET", "/v1/device/calculator", headers=self.kp.bearer(self.tok))
        self.assertEqual(st, 404)
        st, _, _ = self.kp.req(self.kp.cport, "GET", "/v1/device/notebook", headers=self.kp.bearer("wrong"))
        self.assertEqual(st, 404)
        _, v = self.kp.room(self.tok)
        self.assertEqual(v["turn"], 0)  # the notebook never costs a move
        evs = [json.loads(x) for x in self.kp.admin("GET", f"/admin/runs/{self.rid}/log")[1].decode().splitlines()]
        dev = [e for e in evs if e["t"] == "device"]
        self.assertEqual([e["input"] for e in dev], ["write Coral Hall is cold", "page 2"])  # reads aren't logged
        self.assertIn("Coral Hall is cold", dev[0]["screen"])

    def test_notebook_same_answer_for_any_text(self):
        """Writing a real room's name or nonsense gets the same treatment: writing can't probe the house."""
        _, v = self.kp.room(self.tok)
        real, fake = v["doors"][0], "Zz Nowhere"
        h = self.kp.bearer(self.tok)
        a = json.loads(self.kp.req(self.kp.cport, "POST", "/v1/device/notebook", {"line": "write " + real}, headers=h)[1])
        self.kp.req(self.kp.cport, "POST", "/v1/device/notebook", {"line": "erase 1"}, headers=h)
        b = json.loads(self.kp.req(self.kp.cport, "POST", "/v1/device/notebook", {"line": "write " + fake}, headers=h)[1])
        self.assertEqual(a["reply"], b["reply"])
        self.assertEqual(a["screen"].replace(real, "X"), b["screen"].replace(fake, "X"))

    def test_notebook_survives_restart(self):
        h = self.kp.bearer(self.tok)
        self.kp.req(self.kp.cport, "POST", "/v1/device/notebook", {"line": "write remember me"}, headers=h)
        run = Store(self.kp.dir).runs[self.rid]
        self.assertIn("remember me", run.notebook.view(1))


class TestEdits(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.store = Store(self.d)

    def run_(self, **rb):
        run, tok = self.store.create(3, 3, 0, 7, rb or None)
        return run

    def far_room(self, run):
        """A room that is not the current one, not next to it, and not the goal."""
        w = run.world
        bad = {run.cur, w.goal} | set(w.adj[run.cur])
        return next(c for c in range(w.m.n) if c not in bad and not (set(w.adj[c]) & {run.cur}))

    def test_protected_room(self):
        run = self.run_()
        w = run.world
        r = run.try_edit({"type": "rename_room", "room": w.adj[run.cur][0], "name": "Zed"})
        self.assertEqual(r["status"], "refused")
        a = w.adj[run.cur][0]
        r = run.try_edit({"type": "remove_door", "a": run.cur, "b": a})
        self.assertIn("standing", r["reason"])

    def test_goal_and_names(self):
        run = self.run_()
        w = run.world
        self.assertIn("Way Out", run.try_edit({"type": "rename_room", "room": w.goal, "name": "X"})["reason"])
        c = self.far_room(run)
        self.assertEqual(run.try_edit({"type": "rename_room", "room": c, "name": "way out"})["status"], "refused")
        other = next(x for x in range(w.m.n) if x not in (c, run.cur, w.goal))
        self.assertIn("already", run.try_edit({"type": "rename_room", "room": c, "name": w.names[other]})["reason"])
        self.assertEqual(run.try_edit({"type": "rename_room", "room": c, "name": "Blue Attic"})["status"], "applied")
        self.assertEqual(w.names[c], "Blue Attic")
        self.assertEqual(w.room("blue attic"), c)

    def test_connectivity(self):
        run = self.run_()
        w = run.world
        # in a perfect maze every door is a bridge: removing one away from the player must be refused
        a, b = next((a, b) for a in range(w.m.n) for b in w.adj[a] if run.cur not in (a, b))
        self.assertIn("cut rooms off", run.try_edit({"type": "remove_door", "a": a, "b": b})["reason"])
        # adding a door then removing it is fine
        x, y = next((x, y) for x in range(w.m.n) for y in range(w.m.n)
                    if x < y and run.cur not in (x, y) and y not in w.adj[x] and _neighbours(w, x, y))
        self.assertEqual(run.try_edit({"type": "add_door", "a": x, "b": y})["status"], "applied")
        self.assertEqual(run.try_edit({"type": "remove_door", "a": x, "b": y})["status"], "applied")
        self.assertIn("grid neighbours", run.try_edit({"type": "add_door", "a": 0, "b": 26})["reason"])

    def test_swap_and_shift(self):
        run = self.run_()
        w = run.world
        rooms = [c for c in range(w.m.n) if c not in ({run.cur, w.goal} | set(w.adj[run.cur]))]
        a, b = rooms[0], rooms[1]
        na, nb = w.names[a], w.names[b]
        self.assertEqual(run.try_edit({"type": "swap_rooms", "a": a, "b": b})["status"], "applied")
        self.assertEqual((w.names[a], w.names[b]), (nb, na))
        # shift a 1x1xN line that avoids the player and the goal
        for x in range(3):
            for y in range(3):
                line = [x + 3 * y + 9 * z for z in range(3)]
                if not set(line) & ({run.cur, w.goal} | set(w.adj[run.cur])):
                    before = [w.names[c] for c in line]
                    r = run.try_edit({"type": "shift_block", "lo": [x, y, 0], "hi": [x, y, 2], "axis": 2, "step": 1})
                    self.assertEqual(r["status"], "applied", r)
                    self.assertEqual([w.names[c] for c in line], [before[2], before[0], before[1]])
                    return
        self.skipTest("no free line")

    def test_policy(self):
        run = self.run_(max_edits=1, allowed_edits=["rename_room", "message"])
        c = self.far_room(run)
        self.assertIn("not allowed", run.try_edit({"type": "swap_rooms", "a": c, "b": c + 1})["reason"])
        self.assertIn("may not edit", run.try_edit({"type": "message", "text": "hi"}, by="audience")["reason"])
        self.assertEqual(run.try_edit({"type": "message", "text": "hi"})["status"], "applied")
        self.assertIn("budget", run.try_edit({"type": "message", "text": "again"})["reason"])

    def test_cooldown(self):
        run = self.run_(min_turns_between_edits=2)
        self.assertEqual(run.try_edit({"type": "message", "text": "one"})["status"], "applied")
        self.assertIn("too soon", run.try_edit({"type": "message", "text": "two"})["reason"])
        run.walk(run.world.names[run.world.adj[run.cur][0]])
        run.walk(run.world.names[run.world.adj[run.cur][0]])
        self.assertEqual(run.try_edit({"type": "message", "text": "three"})["status"], "applied")

    def test_seen_rooms_forbidden(self):
        run = self.run_(seen_rooms="forbidden")
        w = run.world
        first = run.cur
        run.walk(w.names[w.adj[run.cur][0]])
        run.walk(w.names[next(x for x in w.adj[run.cur] if x != first)] if len(w.adj[run.cur]) > 1 else w.names[first])
        # the start room is now seen but not protected (unless it's next to us)
        if first in w.adj[run.cur] or first == run.cur:
            self.skipTest("start still in view")
        r = run.try_edit({"type": "rename_room", "room": first, "name": "Zed"})
        self.assertIn("seen", r["reason"])

    def test_preview_changes_nothing(self):
        run = self.run_()
        c = self.far_room(run)
        name, v = run.world.names[c], run.world.version
        r = run.try_edit({"type": "rename_room", "room": c, "name": "Blue Attic"}, apply=False)
        self.assertEqual(r["status"], "ok")
        self.assertEqual((run.world.names[c], run.world.version, run.edits_used), (name, v, 0))

    def test_rulebook_validation(self):
        with self.assertRaises(RuleError):
            make({"max_edits": -1})
        with self.assertRaises(RuleError):
            make({"seen_rooms": "maybe"})
        self.assertEqual(make()["generator"], "dfs")

    def test_resume_after_restart(self):
        run = self.run_()
        w = run.world
        for _ in range(5):
            run.walk(w.names[w.adj[run.cur][-1]])
        c = self.far_room(run)
        run.try_edit({"type": "rename_room", "room": c, "name": "Blue Attic"})
        run.walk(w.names[w.adj[run.cur][0]])
        run2 = Store(self.d).runs[run.id]
        self.assertEqual((run2.cur, run2.moves, run2.world.names, run2.world.adj, run2.edits_used, len(run2.events)),
                         (run.cur, run.moves, w.names, w.adj, run.edits_used, len(run.events)))
        run2.walk(run2.world.names[run2.world.adj[run2.cur][0]])  # keeps appending after a restart
        self.assertEqual(Store(self.d).runs[run.id].moves, run.moves + 1)


def _neighbours(w, a, b):
    try:
        w.m.axis_of(a, b)
        return True
    except ValueError:
        return False


# ---- the runner ------------------------------------------------------------------
def play_house(url, tok, lines, mode="none"):
    flags = {"none": [], "notebook": ["--notebook"]}[mode]
    env = dict(os.environ, HOUSE_TOKEN=tok, HOUSE_SERVER=url)
    p = subprocess.run([sys.executable, HOUSE] + flags, input="".join(ln + "\n" for ln in lines).encode("latin-1"),
                       capture_output=True, env=env, timeout=60)
    return p.stdout.decode("latin-1"), p.returncode


SCREEN = re.compile(r"You are in the (.+?)\.\nPaths: (.*)\n")


class TestRunner(unittest.TestCase):
    """The interface shows the room you stand in and its doors, and nothing else."""

    @classmethod
    def setUpClass(cls):
        cls.kp = Keeper()

    @classmethod
    def tearDownClass(cls):
        cls.kp.close()

    def wander(self, seed, mode, steps=60):
        m = Maze(3, 3, 0, seed)
        rng = random.Random(seed)
        cur = m.start
        lines = ["write here we go", "tab start", "page 200"] if mode == "notebook" else []
        extra = ["look", "", "Nowhere", "go the ", "LOOK"]
        if mode == "notebook":
            extra += ["write here we go " * 3, "read", "tab start", "tabs", "next", "back", "erase 1", "read page 3", "page 200"]
        for _ in range(steps):
            if cur == m.goal:
                break
            if rng.random() < 0.25:
                lines.append(rng.choice(extra))
                continue
            nxt = rng.choice(m.adj[cur])
            nm = m.names[nxt]
            lines.append(rng.choice([nm, nm.lower(), "go " + nm, "the " + nm]))
            cur = nxt
        rid, tok = self.kp.new_run(seed=seed)
        got, code = play_house(self.kp.url, tok, lines, mode)
        return m, got, code

    def check_screens(self, m, got):
        name = {m.names[c]: c for c in range(m.n)}
        screens = SCREEN.findall(got)
        self.assertTrue(screens)
        for room, paths in screens:
            doors = paths.split(", ")
            self.assertEqual(doors, abc(m.names[x] for x in m.adj[name[room]]))
        for w in ("Notes:", "untried", "visited", "first came", "map", "shortest"):
            self.assertNotIn(w, got, w)

    def test_screens_none(self):
        for seed in (1, 2, 3):
            m, got, _ = self.wander(seed, "none")
            self.check_screens(m, got)

    def test_screens_notebook(self):
        for seed in (4, 5):
            m, got, _ = self.wander(seed, "notebook")
            self.check_screens(m, got.replace("map out", ""))
            self.assertIn("You write it on page 1, line 1", got)
            self.assertIn("The notebook has pages 1 to", got)
            self.assertIn('You stick a tab on page 1 and label it "start".', got)

    def test_notebook_off_by_default(self):
        _, got, _ = self.wander(6, "none")
        self.assertNotIn("notebook", got.lower())

    def test_solves(self):
        m = Maze(3, 3, 0, 11)
        lines = [m.names[c] for c in m.shortest_path()[1:]]
        rid, tok = self.kp.new_run(seed=11)
        got, code = play_house(self.kp.url, tok, lines)
        self.assertTrue(got.endswith(f"\nYou step through into daylight. You found the Way Out in {m.shortest} moves.\n"))
        self.assertEqual(code, 0)

    def test_messages_and_stop(self):
        rid, tok = self.kp.new_run(seed=12)
        self.kp.admin("POST", f"/admin/runs/{rid}/edits", {"edit": {"type": "message", "text": "A cold draft."}})
        self.kp.admin("POST", f"/admin/runs/{rid}/stop", {"farewell": "Goodnight."})
        got, code = play_house(self.kp.url, tok, ["look"])
        self.assertIn("A cold draft.", got)
        self.assertTrue(got.endswith("\nGoodnight.\n"))
        self.assertEqual(code, 1)

    def test_exhausted(self):
        rid, tok = self.kp.new_run(seed=13, rulebook={"max_moves": 3})
        m = Maze(3, 3, 0, 13)
        a, b = m.start, m.adj[m.start][0]
        got, _ = play_house(self.kp.url, tok, [m.names[b], m.names[a], m.names[b], m.names[a]])
        self.assertTrue(got.endswith("\nYou are exhausted after 3 moves.\n"))

    def test_bad_key(self):
        got, code = play_house(self.kp.url, "wrong", ["look"])
        self.assertEqual(code, 3)
        self.assertIn("gone quiet", got)

    def test_no_hint_words(self):
        """Nothing the contestant can read mentions watching, editing, the server's insides or the house's shape."""
        with open(HOUSE) as f:
            text = f.read().lower()
        for w in ("keeper", "admin", "director", "inject", "edit", "watch", "monitor", "observ", "audience",
                  "colosseum", "spectat", "seed", "harness", "logging", "/log", "feed", "cheat", "unfair",
                  "manipul", "rulebook", "7342",
                  # the house's shape: nothing in the interface may know or hint at it
                  "shortest", "dims", "dimension", "size", "coord", "dist", "grid", "axis", "north", "south",
                  "east", "west", "floor", "layer", "storey", "level", "generator", "loops", "rooms in",
                  "untried", "visited", "came from", "map", "hypermaze", "maze",
                  # the pen and the walls: the material is named, never what it does to the pen
                  "ink", "tip", "wear", "worn", "smear", "faint", "skip", "surface", "damage", "durab"):
            self.assertNotIn(w, text, w)
        for w in ("brick", "ice", "glass", "damp", "metal", "wood", "stone", "plaster", "tile"):   # whole words
            self.assertIsNone(re.search(r"\b" + w + r"\b", text), w)

    def test_interface_stands_alone(self):
        """The interface imports nothing from this project: a player machine needs only this one file."""
        with open(HOUSE) as f:
            imports = {ln.split()[1].split(".")[0] for ln in f if ln.startswith(("import ", "from "))}
        self.assertLessEqual(imports, {"json", "os", "re", "sys", "time", "urllib", "argparse", "tomllib"})

    def test_contestant_sees_only_room_doors_and_turn(self):
        rid, tok = self.kp.new_run(seed=14)
        m = Maze(3, 3, 0, 14)
        _, v = self.kp.room(tok)
        allowed = {"room", "doors", "walls", "marks", "turn", "messages", "ended", "moves", "farewell"}
        cur = m.start
        for c in m.shortest_path(m.start)[1:]:
            self.assertLessEqual(set(v), allowed)
            self.assertEqual(v["doors"], abc(m.names[x] for x in m.adj[cur]))
            _, v = self.kp.walk(tok, m.names[c])
            cur = c
        self.assertLessEqual(set(v), allowed)


def make_test_ca(d):
    """A throwaway CA and a 127.0.0.1 server certificate signed by it (needs the openssl command)."""
    run = lambda *a: subprocess.run(["openssl", *a], cwd=d, check=True, capture_output=True)
    run("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-days", "1",
        "-subj", "/CN=test ca", "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-keyout", "ca.key", "-out", "ca.crt")
    run("req", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-subj", "/CN=127.0.0.1",
        "-keyout", "srv.key", "-out", "srv.csr")
    with open(os.path.join(d, "ext.cnf"), "w") as f:
        f.write("subjectAltName=IP:127.0.0.1,DNS:localhost\nbasicConstraints=critical,CA:FALSE\n"
                "keyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n")
    run("x509", "-req", "-in", "srv.csr", "-CA", "ca.crt", "-CAkey", "ca.key", "-CAcreateserial", "-days", "1",
        "-extfile", "ext.cnf", "-out", "srv.crt")
    return os.path.join(d, "ca.crt"), os.path.join(d, "srv.crt"), os.path.join(d, "srv.key")


@unittest.skipUnless(shutil.which("openssl"), "needs openssl")
class TestTLS(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.ca, crt, key = make_test_ca(cls.tmp)
        cls.kp = Keeper(tls=server.tls_context(crt, key))
        cls.ctx = ssl.create_default_context(cafile=cls.ca)

    @classmethod
    def tearDownClass(cls):
        cls.kp.close()

    def https(self, port, method, path, body=b"", headers=None):
        c = http.client.HTTPSConnection("127.0.0.1", port, timeout=10, context=self.ctx)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def test_contestant_over_https(self):
        rid, tok = self.kp.new_run(seed=31)   # admin port: still plain HTTP
        st, d = self.https(self.kp.cport, "GET", "/v1/room", headers={"Authorization": "Bearer " + tok})
        self.assertEqual(st, 200)
        self.assertIn("doors", json.loads(d))

    def test_plain_http_refused_on_tls_ports(self):
        for port in (self.kp.cport, self.kp.hport):
            with self.assertRaises((http.client.HTTPException, OSError)):
                self.kp.req(port, "GET", "/v1/room")

    def test_untrusted_cert_refused(self):
        c = http.client.HTTPSConnection("127.0.0.1", self.kp.cport, timeout=10, context=ssl.create_default_context())
        with self.assertRaises(ssl.SSLError):
            c.request("GET", "/v1/room")
        c.close()

    def test_harness_over_https(self):
        rid, tok = self.kp.new_run(seed=32)
        path, body = f"/runs/{rid}/turn", json.dumps({"turn": 1, "sent": "look"}).encode()
        ts, sig = auth.sign(self.kp.k.keys["harness"], "POST", path, body)
        st, d = self.https(self.kp.hport, "POST", path, body, {"X-Time": ts, "X-Signature": sig})
        self.assertEqual(st, 200, d)

    def test_house_runner_with_ssl_cert_file(self):
        """runner/house.py needs no change: urllib trusts the home CA through $SSL_CERT_FILE."""
        rid, tok = self.kp.new_run(seed=33)
        url = f"https://127.0.0.1:{self.kp.cport}"
        os.environ["SSL_CERT_FILE"] = self.ca
        try:
            got, code = play_house(url, tok, ["look"], "none")
        finally:
            del os.environ["SSL_CERT_FILE"]
        self.assertNotIn("gone quiet", got)
        self.assertIn("You are in the", got)


class TestEndToEnd(unittest.TestCase):
    def test_play_llm_through_the_house(self):
        kp = Keeper()
        try:
            rid, tok = kp.new_run(seed=21, rulebook={"max_moves": 25})
            port = 18000 + os.getpid() % 1000
            fake = subprocess.Popen([sys.executable, os.path.join(ROOT, "tests", "fake_llm.py"), str(port)])
            d = tempfile.mkdtemp()
            try:
                time.sleep(0.5)
                env = dict(os.environ, HOUSE_TOKEN=tok, HARNESS_KEY=kp.k.keys["harness"])
                p = subprocess.run([sys.executable, os.path.join(ROOT, "play_llm.py"),
                                    "--url", f"http://127.0.0.1:{port}/v1/chat/completions", "--house", kp.url,
                                    "--report", f"http://127.0.0.1:{kp.hport}", "--run", rid, "--mode", "none",
                                    "--llm-log", os.path.join(d, "llm.log"), "--speed-log", os.path.join(d, "speed.log")],
                                   env=env, capture_output=True, text=True, timeout=120)
            finally:
                fake.kill()
                fake.wait()
            self.assertIn("You are exhausted after 25 moves.", p.stdout)
            evs = [json.loads(x) for x in kp.admin("GET", f"/admin/runs/{rid}/log")[1].decode().splitlines()]
            turns = [e for e in evs if e["t"] == "harness"]
            self.assertEqual(sum(e["t"] == "walk" for e in evs), 25)
            self.assertGreaterEqual(len(turns), 25)
            self.assertEqual(turns[0]["turn"], 1)
            self.assertIn("You are in the", turns[0]["screen"])
        finally:
            kp.close()



class TestViewer(unittest.TestCase):
    """The viewer relay: pages, run logs, saved matches, and the viewer key."""

    def setUp(self):
        from http.server import ThreadingHTTPServer
        from arena import viewer
        self.keeper = Keeper()
        self.mdir = tempfile.mkdtemp()
        page = os.path.join(ROOT, "viewer", "colosseum.html")
        flat = os.path.join(ROOT, "viewer", "index.html")
        relay = viewer.Relay(f"http://127.0.0.1:{self.keeper.aport}", self.keeper.k.keys["admin"], page, flat, self.mdir)
        h = type("H", (viewer.Handler,), {"relay": relay, "viewer_key": "vk", "log_message": lambda *a: None})
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), h)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.keeper.close()
        shutil.rmtree(self.mdir, ignore_errors=True)

    def get(self, path, method="GET", body=None):
        st, data, hd = self.keeper.req(self.port, method, path, body)
        return st, data, hd

    def race(self, n=2, seed=11):
        ids = []
        for i in range(n):  # same maze; a different rulebook keeps the signed requests apart
            rid, tok = self.keeper.new_run(dims=2, size=4, seed=seed, rulebook={"max_moves": 100 + i})
            st, room = self.keeper.room(tok)
            self.keeper.walk(tok, room["doors"][0])
            ids.append(rid)
        return ids

    def test_pages_and_key(self):
        st, data, _ = self.get("/?key=vk")
        self.assertEqual(st, 200)
        self.assertIn(b"Colosseum", data)
        st, data, _ = self.get("/flat?key=vk")
        self.assertEqual(st, 200)
        self.assertNotEqual(data, self.get("/?key=vk")[1])
        self.assertEqual(self.get("/api/runs")[0], 401)
        self.assertEqual(self.get("/api/matches?key=wrong")[0], 401)
        self.assertEqual(self.get("/api/matches", "POST", {"runs": ["x"]})[0], 401)

    def test_log(self):
        rid = self.race(1)[0]
        st, data, _ = self.get(f"/api/runs/{rid}/log?key=vk")
        self.assertEqual(st, 200)
        kinds = [e["t"] for e in json.loads(data)["events"]]
        self.assertEqual(kinds[:2], ["start", "walk"])

    def test_save_list_open(self):
        ids = self.race(2)
        st, data, _ = self.get("/api/matches?key=vk", "POST", {"name": "Test race!", "runs": ids})
        self.assertEqual(st, 201, data)
        saved = json.loads(data)
        self.assertTrue(saved["id"].endswith("test-race"))
        self.assertEqual([c["run"] for c in saved["contestants"]], ids)
        st, data, _ = self.get("/api/matches?key=vk")
        self.assertEqual([m["id"] for m in json.loads(data)["matches"]], [saved["id"]])
        st, data, _ = self.get(f"/api/matches/{saved['id']}?key=vk")
        doc = json.loads(data)
        self.assertEqual(doc["format"], "hypermaze-match")
        self.assertEqual(len(doc["runs"]), 2)
        self.assertEqual(doc["runs"][0]["events"][1]["t"], "walk")
        self.assertEqual(doc["map"]["seed"], 11)
        # the file sits in the viewer's own folder; nothing in the browser, nothing at the keeper
        self.assertEqual(os.listdir(self.mdir), [saved["id"] + ".json"])
        # saved matches outlive the keeper's data
        shutil.rmtree(os.path.join(self.keeper.dir, "runs"))
        self.assertEqual(self.get(f"/api/matches/{saved['id']}?key=vk")[0], 200)

    def test_bad_saves(self):
        ids = self.race(1)
        other, _ = self.keeper.new_run(dims=2, size=4, seed=99)
        self.assertEqual(self.get("/api/matches?key=vk", "POST", {"runs": []})[0], 400)
        self.assertEqual(self.get("/api/matches?key=vk", "POST", {"runs": ["../etc"]})[0], 400)
        self.assertEqual(self.get("/api/matches?key=vk", "POST", {"runs": ["a"] * 9})[0], 400)
        self.assertEqual(self.get("/api/matches?key=vk", "POST", {"runs": ["no-such-run"]})[0], 404)
        st, data, _ = self.get("/api/matches?key=vk", "POST", {"runs": ids + [other]})
        self.assertEqual(st, 400)
        self.assertIn(b"different mazes", data)
        self.assertEqual(self.get("/api/matches/../../x?key=vk")[0], 404)
        self.assertEqual(self.get("/api/runs?key=vk", "POST", {})[0], 405)
        self.assertEqual(os.listdir(self.mdir), [])

if __name__ == "__main__":
    unittest.main()
