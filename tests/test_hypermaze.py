#!/usr/bin/env python3
"""Unit tests. Run:  python3 -m unittest discover -s tests -v   (from the repo root)
The byte-for-byte check against the C++ binary is tests/compare.py."""
import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from hypermaze import Maze, Game  # noqa: E402
from hypermaze.rng import MT19937, shuffle  # noqa: E402
from hypermaze.harness import pick_move  # noqa: E402
from hypermaze.inject import Inbox, apply, load_plugin  # noqa: E402
from hypermaze.replay import load_run, to_viewer  # noqa: E402


class TestRng(unittest.TestCase):
    def test_matches_cpp(self):
        # values printed by a C++ program using std::mt19937 and std::shuffle (g++ 13)
        for seed, first, last in ((1, 1791095845, 1345739871), (7, 327741615, 1053601162)):
            g = MT19937(seed)
            self.assertEqual(g(), first)
            for n in (1, 2, 3, 4, 5, 8, 13, 40, 1600, 70000):
                shuffle(list(range(n)), g)
            self.assertEqual(g(), last)


class TestMaze(unittest.TestCase):
    def test_same_maze_as_cpp_run(self):
        # header of a notes run (C++ ./maze --dims 4 --size 6 --loops 0.2 --seed 7)
        m = Maze(4, 6, 0.2, 7)
        self.assertEqual(m.header(), "# dims=4 size=6 loops=0.2 seed=7 rooms=1296 start=Granite Shrine(1,3,4,2) "
                                     "goal=(5,0,0,2) shortest=17")

    def test_perfect_and_connected(self):
        for dims, size in ((2, 5), (3, 3), (4, 3), (5, 2), (5, 3)):
            m = Maze(dims, size, 0, 11)
            self.assertEqual(len(m.links()), m.n - 1)
            self.assertNotIn(-1, m.bfs(0))
            for a, b in m.links():
                m.axis_of(a, b)  # every link joins grid neighbours

    def test_loops_add_links(self):
        self.assertGreater(len(Maze(4, 3, 0.3, 2).links()), len(Maze(4, 3, 0, 2).links()))

    def test_shortest_path(self):
        m = Maze(4, 4, 0.1, 3)
        p = m.shortest_path()
        self.assertEqual(p[0], m.start)
        self.assertEqual(p[-1], m.goal)
        self.assertEqual(len(p) - 1, m.shortest)
        for a, b in zip(p, p[1:]):
            self.assertIn(b, m.adj[a])
        q = m.shortest_path(5, 9)
        self.assertEqual(len(q) - 1, m.distance(5, 9))

    def test_export(self):
        m = Maze(4, 2, 0, 3)
        d = json.loads(m.to_json())
        self.assertEqual(len(d["rooms"]), 16)
        self.assertEqual(d["rooms"][d["goal"]]["name"], "Way Out")
        self.assertEqual(d["solution"], m.shortest_path())
        for ln in d["links"]:
            ca, cb = d["rooms"][ln["a"]]["coord"], d["rooms"][ln["b"]]["coord"]
            diff = [i for i in range(4) if ca[i] != cb[i]]
            self.assertEqual(diff, [ln["axis"]])
        s = m.slice({3: 0})
        self.assertEqual(len(s["rooms"]), 8)
        self.assertTrue(all(m.coord(c, 3) == 0 for c in s["rooms"]))

    def test_coords(self):
        m = Maze(3, 4, 0, 1)
        for c in range(m.n):
            self.assertEqual(m.room_at(m.coord(c)), c)


def walk(g, rooms):
    for r in rooms:
        g.send(g.m.names[r])


class TestGame(unittest.TestCase):
    def test_notes_two_way(self):
        m = Maze(3, 3, 0, 4)
        a = m.start
        b = m.adj[a][0]
        for both, expect_untried in ((True, False), (False, True)):
            g = Game(m, mode="notes", notes_both_ways=both)
            g.intro()
            g.send(m.names[b])
            self.assertEqual(a in g.untried(b), expect_untried)

    def test_solve_by_shortest_path(self):
        m = Maze(4, 3, 0, 9)
        log, moves = io.StringIO(), io.StringIO()
        g = Game(m, maze_log=log, moves_log=moves)
        g.intro()
        walk(g, m.shortest_path()[1:])
        self.assertEqual(g.result, "SOLVED")
        self.assertIn(f"# SOLVED moves={m.shortest} shortest={m.shortest} invalid=0", log.getvalue())
        ev = [json.loads(x) for x in moves.getvalue().splitlines()]
        self.assertEqual([e["t"] for e in ev], ["start"] + ["move"] * m.shortest + ["end"])

    def test_invalid_and_prefix(self):
        m = Maze(2, 3, 0, 1)
        g = Game(m)
        g.intro()
        out = g.send("xyzzy")
        self.assertTrue(out.startswith('No path here leads to "xyzzy".'))
        self.assertEqual(g.bad, 1)


class TestHarness(unittest.TestCase):
    def test_pick_move(self):
        cases = [
            ("Thinking.\nGO: Coral Study", "", ("Coral Study", "visible")),
            ("GO: <command or room name>", "", ("look", "fallback")),
            ("<command or room name>. So final: GO: Echoing Observatory", "", ("Echoing Observatory", "visible")),
            ("GO: **Iron Kitchen**.", "", ("Iron Kitchen", "visible")),
            ("", "hmm... GO: Brass Larder", ("Brass Larder", "thinking")),
            ("I have been going round in circles for a while now, so I am unsure.", "", ("look", "fallback")),
            ("GO: <command or room name>. If we type map, is that a command? It says Type map to list", "",
             ("look", "fallback")),
            ("GO: write page 1: CONTENTS. Remember to GO: back to Coral Study via the long corridor next time", "",
             ("write page 1: CONTENTS. Remember to GO: back to Coral Study via the long corridor next time", "visible")),
            ("GO: Coral Study\nActually no.\nGO: map", "", ("map", "visible")),
        ]
        for vis, think, want in cases:
            self.assertEqual(pick_move(vis, think, vis), want, vis)


class TestInject(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inbox_path = os.path.join(self.tmp.name, "inbox.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def push(self, **kw):
        with open(self.inbox_path, "a") as f:
            f.write(json.dumps(kw) + "\n")

    def test_message_teleport_links_stop(self):
        m = Maze(3, 3, 0, 4)
        log = io.StringIO()
        g = Game(m, mode="notes", maze_log=log)
        inbox = Inbox(self.inbox_path)
        g.intro()
        self.push(kind="message", text="A bell rings.")
        inbox.apply_new(g)
        out = g.send("look")
        self.assertTrue(out.endswith("\nA bell rings.\n> "))
        self.assertNotIn("A bell rings.", g.send("look"))  # shown once

        far = max(range(m.n), key=lambda c: (c != m.goal, m.distance(m.start, c)) if c != m.goal else (False, 0))
        self.push(kind="teleport", room=m.names[far])
        inbox.apply_new(g)
        self.assertEqual(g.cur, far)
        self.assertIn(f"You are in the {m.names[far]}.", g.send("look"))

        # open a passage to a grid neighbour that isn't linked yet
        nb = next(x for a in range(m.dims) for d in (-1, 1)
                  for x in [m.grid_neighbor(far, a, d)] if x >= 0 and x not in m.adj[far])
        self.push(kind="open_link", a=far, b=m.names[nb])
        inbox.apply_new(g)
        self.assertIn(nb, m.adj[far])
        # closing it again is fine (maze stays connected); closing a tree link is refused
        self.push(kind="close_link", a=far, b=nb)
        inbox.apply_new(g)
        self.assertNotIn(nb, m.adj[far])
        tree_link = m.links()[0]
        self.push(kind="close_link", a=tree_link[0], b=tree_link[1])
        inbox.apply_new(g)
        self.assertIn(tree_link[1], m.adj[tree_link[0]])

        self.push(kind="bogus")
        with open(self.inbox_path, "a") as f:
            f.write("not json\n# comment\n")
        inbox.apply_new(g)  # both skipped, game carries on

        self.push(kind="stop", reason="The audience has seen enough.")
        inbox.apply_new(g)
        self.assertEqual(g.stop_requested, "The audience has seen enough.")
        self.assertIn("The audience has seen enough.", g.stop(g.stop_requested))
        self.assertEqual(g.result, "STOPPED")
        self.assertIn("INJECT teleport", log.getvalue())
        self.assertIn("# STOPPED", log.getvalue())

    def test_inbox_ignores_old_lines_and_partial(self):
        self.push(kind="message", text="old")
        inbox = Inbox(self.inbox_path)
        self.assertEqual(inbox.poll(), [])
        with open(self.inbox_path, "a") as f:
            f.write('{"kind": "message", ')
        self.assertEqual(inbox.poll(), [])
        with open(self.inbox_path, "a") as f:
            f.write('"text": "new"}\n')
        self.assertEqual(inbox.poll(), [{"kind": "message", "text": "new"}])

    def test_plugin(self):
        m = Maze(4, 3, 0, 9)
        g = Game(m)
        load_plugin(g, os.path.join(ROOT, "plugins", "example_signposts.py"))
        g.intro()
        self.assertIn("You pray.", g.send("pray"))
        seen = []
        g.on("move", lambda game, a, b: seen.append(b))
        g.send(m.names[m.adj[m.start][0]])
        self.assertEqual(seen, [m.adj[m.start][0]])


class TestReplay(unittest.TestCase):
    def test_jsonl_roundtrip(self):
        m = Maze(4, 3, 0.2, 5)
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "r-moves.jsonl")
            with open(p, "w") as f:
                g = Game(m, mode="notes", moves_log=f)
                g.intro()
                for _ in range(30):
                    g.send(m.names[m.adj[g.cur][-1]])
                    if g.done:
                        break
                g.send("nowhere")
            run = load_run(p)
            self.assertEqual(run["path"], g.path)
            v = json.loads(json.dumps(to_viewer(run)))
            self.assertEqual(v["replay"]["path"], g.path)

    CPP_LOG = os.path.join(ROOT, "tests", "data", "cpp-run1-maze.log")

    @unittest.skipUnless(os.path.exists(CPP_LOG), "sample C++ run log not included in this repo")
    def test_old_cpp_maze_log(self):
        # a log written by the C++ ./maze (older tab-separated format)
        run = load_run(self.CPP_LOG)
        m = run["maze"]
        for a, b in zip(run["path"], run["path"][1:]):
            self.assertIn(b, m.adj[a])


class TestDefaults2D(unittest.TestCase):
    """The project is 2D now: every default builds a 2D 8x8 maze."""

    def test_maze_default_is_2d(self):
        m = Maze(seed=7)
        self.assertEqual((m.dims, m.size, m.n), (2, 8, 64))
        self.assertEqual(len(m.links()), m.n - 1)
        self.assertTrue(all(len(r["coord"]) == 2 for r in m.to_dict()["rooms"]))

    def test_cli_defaults_are_2d(self):
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import maze as cli
        o = cli.parse([])
        self.assertEqual((o["dims"], o["size"]), (2, 8))

    def test_2d_game_reaches_the_way_out(self):
        m = Maze(seed=3)
        g = Game(m, mode="notes")
        g.intro()
        for r in m.shortest_path()[1:]:
            g.send(m.names[r])
        self.assertTrue(g.done)
        self.assertEqual(len(g.path) - 1, m.shortest)


if __name__ == "__main__":
    unittest.main()
