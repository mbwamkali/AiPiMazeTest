"""v3 ant format and relay lessons: the screen, parsing answers, the lesson bot finishing relays and
marking the way, and lessons that a model can learn from (every answer is a legal key)."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from hypermaze.core import Maze  # noqa: E402
from ant import lessons3, score3, view3 as V  # noqa: E402
from ant.relay3 import RelayGame  # noqa: E402


class TestView3(unittest.TestCase):
    def test_screen_and_parse(self):
        text, opt = V.view("Coral Hall", ["Iron Den", "Jade Foyer", "Way Out"], {"Iron Den": "new"}, "Jade Foyer", 2,
                           ("walk", "Jade Foyer", False), walls="damp_stone",
                           marks=["X Jade Foyer", "out Ir_n Den", "(faint) went Jade Foyer"], write="out Iron Den")
        self.assertIn("Walls: damp stone", text)
        self.assertIn("1 Iron Den - new, a note here says the Way Out is this way", text)
        self.assertIn("2 Jade Foyer - done, you first came from here, a note here says done, a note here says someone went", text)
        self.assertIn("W - write on the wall: out Iron Den", text)
        self.assertNotIn("S - ", text)                       # nobody here to tell
        self.assertEqual(V.parse("GO: 1\nOff we go", opt), ("go", "Iron Den"))
        self.assertEqual(V.parse("GO: W", opt), ("write", "out Iron Den"))
        self.assertIsNone(V.parse("GO: S", opt))
        self.assertIsNone(V.parse("GO: 9", opt))
        self.assertEqual(V.parse("GO: map", opt), ("map",))

    def test_last_action_lines(self):
        self.assertIn('it came out: "out Ir_n Den"', V.last_text(("wrote", "out Ir_n Den")))
        self.assertIn("nothing showed", V.last_text(("wrote", "")))


class TestRelay(unittest.TestCase):
    def play(self, maze, ants, mix=None, seed=1):
        g = RelayGame(maze, ants=ants, mix=mix, seed=seed)
        kinds = []
        while not g.done:
            text, opt = g.screen()
            key, kind = g.teach()
            self.assertIn(key, opt, text)
            kinds.append(kind)
            g.apply(opt[key])
        return g, kinds

    def test_bot_finishes_and_marks_the_way(self):
        g, kinds = self.play(Maze(2, 16, 0.25, 7), 3)
        self.assertTrue(g.result()["all_out"])
        self.assertIn("mark", kinds)
        self.assertIn("write_out", kinds)
        self.assertIn("follow", kinds)

    def test_stops_writing_on_a_material_that_failed(self):
        g, kinds = self.play(Maze(2, 16, 0.25, 7), 3, {"ice": 1.0})
        self.assertTrue(g.result()["all_out"])
        for p in g.players:
            self.assertLessEqual(p.writes, 1)                # each ant tries ice once, then never again
        self.assertTrue(any("ice" in p.bad for p in g.players))

    def test_screen_says_nothing_about_teammates_beyond_the_briefing(self):
        g = RelayGame(Maze(2, 8, 0.2, 3), ants=3, seed=3)
        while not g.done:
            text, opt = g.screen()
            for line in text.split("\n"):
                if "ant" in line.lower() and not line.startswith(("Room:", "Last action:")) and line[:1] not in "0123456789":
                    self.assertTrue(line.startswith(("Your notes: ", "B - ")), line)
            g.apply(opt[g.teach()[0]])
        lone = RelayGame(Maze(2, 8, 0.2, 3), ants=1, seed=3)          # briefed: nobody after, no B
        while not lone.done:
            text, opt = lone.screen()
            self.assertNotIn("B", opt)
            lone.apply(opt[lone.teach()[0]])

    def test_unbriefed_offers_marking_at_every_way_out(self):
        g = RelayGame(Maze(2, 8, 0.2, 3), ants=1, seed=3, briefing="none")
        offered = False
        while not g.done:
            text, opt = g.screen()
            offered |= "B" in opt
            g.apply(opt[g.teach()[0]])
        self.assertTrue(offered)

    def test_lessons_are_legal(self):
        out = os.path.join(tempfile.mkdtemp(), "l.jsonl")
        lessons3.main(["--out", out, "--games", "12"])
        with open(out) as f:
            rows = [json.loads(l) for l in f]
        self.assertTrue(rows)
        for r in rows:
            m = r["messages"]
            self.assertEqual([x["role"] for x in m], ["system", "user", "assistant"])
            self.assertTrue(m[2]["content"].startswith("GO: "))

    def test_score_bot_agrees_with_itself(self):
        games = [score3.Game(s, False) for s in score3.heldout(12)]
        score3.run_bot(games)
        out, _ = score3.summary(games)
        self.assertEqual((out["all_out"], out["agree"], out["noparse"]), (1.0, 1.0, 0.0))


if __name__ == "__main__":
    unittest.main()
