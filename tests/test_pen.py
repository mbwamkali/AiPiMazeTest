#!/usr/bin/env python3
"""Tests for the pen and the walls: hidden ink and tip, wall surfaces, wall writing, replay.
Run:  python3 -m unittest discover -s tests -v   (from the repo root)"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hypermaze import Maze  # noqa: E402
from arena.pen import PAPER, Pen, assign_surfaces, WALL_SURFACES  # noqa: E402
from arena.rulebook import RuleError, make, fingerprint, DEFAULTS  # noqa: E402
from arena.runs import Store, WALL_REPLY  # noqa: E402
from test_arena import Keeper, play_house  # noqa: E402


class TestPen(unittest.TestCase):
    def test_full_pen_writes_cleanly_on_paper_and_wood(self):
        p = Pen(200000)
        self.assertEqual(p.render("ROOM: Coral Hall | doors: Iron Den", PAPER, 1)[0], "ROOM: Coral Hall | doors: Iron Den")
        self.assertEqual(p.render("dead end -> Iron Den", "wood", 1)[0], "dead end -> Iron Den")
        self.assertEqual(p.tip, 1.0)

    def test_ink_costs(self):
        p = Pen(1000, wall_ink_per_letter=10)
        p.render("abcd", PAPER, 1)
        self.assertEqual(p.ink, 996)
        p.render("ab cd", "wood", 1)          # spaces are free
        self.assertEqual(p.ink, 956)
        p.render("ab", "brick", 1)            # brick: 1.5x
        self.assertEqual(p.ink, 926)

    def test_nothing_shows_on_ice_glass_metal_tile(self):
        for s in ("ice", "glass", "metal", "tile"):
            p = Pen(200000)
            line, log = p.render("way out -> Cedar Orchard", s, 3)
            self.assertEqual(line, "", s)
            self.assertLess(p.ink, 200000)    # but it still cost something
            self.assertEqual(log["rendered"], "")

    def test_damp_stone_smears(self):
        line = Pen(200000).render("a long message about the dead end to the east", "damp_stone", 4)[0]
        self.assertIn("_", line)
        self.assertNotEqual(line.replace("_", ""), "")

    def test_brick_wears_the_tip_until_it_skips_then_breaks(self):
        p = Pen(10 ** 7, wear={"brick": 0.01})
        first = p.render("x" * 40, "brick", 5)[0]
        self.assertEqual(first, "x" * 40)            # still above half: clean
        self.assertAlmostEqual(p.tip, 0.6)
        later = p.render("x" * 40, "brick", 5)[0]
        self.assertIn("_", later)                    # below half: letters drop out
        p.render("x" * 40, "brick", 5)
        self.assertEqual(p.tip, 0.0)
        self.assertEqual(p.render("still writing?", PAPER, 5)[0], "")   # a broken tip writes nothing, paper too

    def test_low_ink_is_faint_then_dry(self):
        p = Pen(1000)
        p.ink = 40                                   # 4% of a full pen
        self.assertEqual(p.render("abc", PAPER, 6)[0], "(faint) abc")
        p.ink = 5                                    # 0.5%
        self.assertEqual(p.render("abc", PAPER, 6)[0], "(very faint) abc")
        p.ink = 1                                    # runs dry mid-line
        self.assertEqual(p.render("abc", PAPER, 6)[0], "(very faint) a")
        self.assertEqual(p.render("abc", PAPER, 6)[0], "")

    def test_same_seed_same_result(self):
        a, b = Pen(5000, wear={"brick": 0.02}), Pen(5000, wear={"brick": 0.02})
        for i in range(20):
            s = ("damp_stone", "brick", PAPER)[i % 3]
            self.assertEqual(a.render(f"line {i} dead end", s, "x")[0], b.render(f"line {i} dead end", s, "x")[0])
        self.assertEqual(a.state(), b.state())

    def test_default_pen_covers_a_long_notebook(self):
        p = Pen(DEFAULTS["pen_ink"])
        line = "MOVE: Whispering Workshop -> Sunken Garden"
        for _ in range(25000 // len(line) + 1):
            self.assertEqual(p.render(line, PAPER, 7)[0], line)


class TestSurfaces(unittest.TestCase):
    def test_default_all_wood(self):
        self.assertEqual(set(assign_surfaces(256, 1, {})), {"wood"})

    def test_mix_and_determinism(self):
        mix = {"brick": 0.25, "ice": 0.1}
        a = assign_surfaces(256, 99, mix)
        self.assertEqual(a, assign_surfaces(256, 99, mix))
        self.assertEqual(a.count("brick"), 64)
        self.assertEqual(a.count("ice"), 26)
        self.assertEqual(a.count("wood"), 256 - 64 - 26)
        self.assertNotEqual(a, assign_surfaces(256, 100, mix))

    def test_surfaces_do_not_change_the_maze(self):
        m1 = Maze(2, 16, 0.25, 1999231218)
        store = Store(tempfile.mkdtemp())
        run, _ = store.create(2, 16, 0.25, 1999231218, {"surfaces": {"brick": 0.5}})
        self.assertEqual(run.world.names, m1.names)
        self.assertEqual(run.world.adj, m1.adj)


class TestRulebook(unittest.TestCase):
    def test_validation(self):
        for bad in ({"surfaces": {"lava": 0.1}}, {"surfaces": {"brick": 0.7, "ice": 0.5}}, {"surfaces": {"brick": -1}},
                    {"surfaces": []}, {"pen_ink": -1}, {"pen_ink": True}, {"wall_ink_per_letter": "x"},
                    {"wear": {"brick": 2}}, {"wear": {"lava": 0.1}}):
            with self.assertRaises(RuleError, msg=bad):
                make(bad)

    def test_wear_overrides_merge(self):
        rb = make({"wear": {"brick": 0.05}})
        self.assertEqual(rb["wear"]["brick"], 0.05)
        self.assertEqual(rb["wear"]["stone"], DEFAULTS["wear"]["stone"])

    def test_in_fingerprint(self):
        self.assertNotEqual(fingerprint(make()), fingerprint(make({"surfaces": {"ice": 0.1}})))
        self.assertNotEqual(fingerprint(make()), fingerprint(make({"pen_ink": 1000})))

    def test_old_rulebooks_play_as_before(self):
        """A run stored before the pen existed loads with its own fingerprint, wooden walls and a full pen."""
        d = tempfile.mkdtemp()
        store = Store(d)
        run, _ = store.create(2, 8, 0, 5)
        meta_path = os.path.join(run.dir, "run.json")
        with open(meta_path) as f:
            meta = json.load(f)
        for k in ("surfaces", "pen_ink", "wall_ink_per_letter", "wear"):
            del meta["rulebook"][k]
        old_fp = fingerprint(meta["rulebook"])
        meta["fingerprint"] = old_fp
        with open(meta_path, "w") as f:
            json.dump(meta, f)
        run.device_input("notebook", "write ROOM: Coral Hall | doors: Iron Den")
        again = Store(d).runs[run.id]
        self.assertEqual(again.fingerprint, old_fp)
        self.assertEqual(set(again.surfaces), {"wood"})
        self.assertIn(" 1| ROOM: Coral Hall | doors: Iron Den", again.notebook.view(1))


class TestWallsAPI(unittest.TestCase):
    def setUp(self):
        self.kp = Keeper()

    def tearDown(self):
        self.kp.close()

    def wall(self, tok, text):
        st, d, _ = self.kp.req(self.kp.cport, "POST", "/v1/wall", {"text": text}, self.kp.bearer(tok))
        return st, json.loads(d)

    def note(self, tok, line):
        st, d, _ = self.kp.req(self.kp.cport, "POST", "/v1/device/notebook", {"line": line}, self.kp.bearer(tok))
        return json.loads(d)

    def log(self, rid):
        return [json.loads(x) for x in self.kp.admin("GET", f"/admin/runs/{rid}/log")[1].decode().splitlines()]

    def test_default_walls_are_wood_and_blank(self):
        rid, tok = self.kp.new_run()
        _, v = self.kp.room(tok)
        self.assertEqual((v["walls"], v["marks"]), ("wood", []))

    def test_wall_write_costs_a_turn_and_stays_in_the_room(self):
        rid, tok = self.kp.new_run(dims=2, size=4, seed=3)
        st, v = self.wall(tok, "  dead end   -> Iron Den ")
        self.assertEqual(st, 200)
        self.assertEqual(v["reply"], WALL_REPLY)
        self.assertEqual(v["turn"], 1)
        self.assertEqual(v["marks"], ["dead end -> Iron Den"])
        here = v["room"]
        _, v = self.kp.walk(tok, v["doors"][0])
        self.assertEqual(v["marks"], [])
        _, v = self.kp.walk(tok, here)
        self.assertEqual(v["marks"], ["dead end -> Iron Den"])
        self.assertEqual(self.wall(tok, 5)[0], 400)

    def test_same_reply_whatever_the_wall(self):
        _, tw = self.kp.new_run(dims=2, size=4, seed=3)
        _, ti = self.kp.new_run(dims=2, size=4, seed=3, rulebook={"surfaces": {"ice": 1.0}})
        _, a = self.wall(tw, "hello")
        _, b = self.wall(ti, "hello")
        self.assertEqual((a["reply"], b["reply"]), (WALL_REPLY, WALL_REPLY))
        self.assertEqual((a["walls"], b["walls"]), ("wood", "ice"))
        self.assertEqual((a["marks"], b["marks"]), (["hello"], []))   # nothing shows on ice

    def test_contestant_never_sees_ink_or_tip(self):
        rid, tok = self.kp.new_run(dims=2, size=4, seed=3, rulebook={"surfaces": {"brick": 1.0}, "pen_ink": 300})
        seen = []
        for i in range(6):
            seen.append(self.wall(tok, f"note number {i} here")[1])
            seen.append(self.note(tok, f"write line {i} of my notes"))
        seen.append(self.kp.room(tok)[1])
        for r in seen:
            text = json.dumps(r).lower()
            for w in ("ink", "tip", "wear", "surface", "pen_", "intended"):
                self.assertNotIn(w, text.replace("thinking", ""), w)
        allowed = {"room", "doors", "walls", "marks", "turn", "messages", "ended", "moves", "farewell", "reply"}
        self.assertLessEqual(set(seen[0]), allowed)

    def test_keeper_logs_the_true_numbers(self):
        rid, tok = self.kp.new_run(dims=2, size=4, seed=3, rulebook={"pen_ink": 1000})
        self.wall(tok, "abc")
        self.note(tok, "write xyz")
        evs = self.log(rid)
        w = next(e for e in evs if e["t"] == "wall")
        self.assertEqual((w["surface"], w["intended"], w["rendered"], w["ink_before"], w["ink_after"]),
                         ("wood", "abc", "abc", 1000, 970))
        dev = next(e for e in evs if e["t"] == "device")
        self.assertEqual((dev["pen"][0]["surface"], dev["pen"][0]["ink_before"], dev["pen"][0]["ink_after"]),
                         ("paper", 970, 967))
        _, st = self.kp.admin("GET", f"/admin/runs/{rid}/state")
        self.assertEqual((st["pen"]["ink"], st["wall_writes"], st["walls"]), (967, 1, "wood"))
        _, mp = self.kp.admin("GET", f"/admin/runs/{rid}/map")
        self.assertTrue(all(r["walls"] == "wood" for r in mp["rooms"]))
        self.assertTrue(any(r.get("marks") == ["abc"] for r in mp["rooms"]))

    def test_small_pen_fades_in_the_notebook(self):
        rid, tok = self.kp.new_run(rulebook={"pen_ink": 200})
        replies = [self.note(tok, "write abcdefghi") for _ in range(24)]
        screen = replies[-1]["screen"]
        self.assertIn(" 1| abcdefghi", screen)
        self.assertIn("faint) ", screen)
        self.assertEqual({r["reply"].split(",")[0] for r in replies}, {"You write it on page 1"})   # same reply

    def test_replay_rebuilds_walls_notebook_and_pen(self):
        rid, tok = self.kp.new_run(dims=2, size=4, seed=3,
                                   rulebook={"surfaces": {"damp_stone": 0.5, "brick": 0.5}, "pen_ink": 3000})
        _, v = self.kp.room(tok)
        for i in range(8):
            self.wall(tok, f"mark {i} dead end that way")
            self.note(tok, f"write ROOM: {v['room']} | doors: {'; '.join(v['doors'])}")
            _, v = self.kp.walk(tok, v["doors"][i % len(v["doors"])])
        live = self.kp.k.store.runs[rid]
        again = Store(self.kp.dir).runs[rid]
        self.assertEqual(again.marks, live.marks)
        self.assertEqual(again.pen.state(), live.pen.state())
        self.assertEqual(again.notebook.view(1), live.notebook.view(1))
        self.assertEqual((again.moves, again.cur), (live.moves, live.cur))

    def test_house_shows_walls_and_writes_on_them(self):
        rid, tok = self.kp.new_run(dims=2, size=4, seed=3)
        got, _ = play_house(self.kp.url, tok, ["write on wall came in here", "look", "wall second note"], "notebook")
        self.assertIn("The walls are wood.", got)
        self.assertIn("You write on the wall.", got)
        self.assertIn("Written on the walls:\n  came in here", got)
        self.assertIn("  second note", got)
        got, _ = play_house(self.kp.url, tok, ["look"], "none")
        self.assertIn("The walls are wood.", got)

    def test_house_names_the_material_only(self):
        rid, tok = self.kp.new_run(dims=2, size=4, seed=3, rulebook={"surfaces": {"damp_stone": 1.0}})
        got, _ = play_house(self.kp.url, tok, ["look"], "notebook")
        self.assertIn("The walls are damp stone.", got)


if __name__ == "__main__":
    unittest.main()
