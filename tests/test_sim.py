"""The bot simulator (tools/sim.py): ants play blind, games finish, results repeat, and the trail habit
helps an ant that comes in later."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import sim  # noqa: E402
from hypermaze.core import Maze  # noqa: E402

BASE = {"ants": 1, "start": "together", "walls": "none", "meet": "none", "surfaces": "wood", "pen": 200000,
        "wear": "real"}


def game(seed, **kw):
    return sim.play(Maze(2, 10, 0.2, seed), dict(BASE, **kw), seed)


class TestSim(unittest.TestCase):
    def test_solo_finishes_and_repeats(self):
        for seed in range(5):
            r = game(seed)
            self.assertTrue(r["solved"])
            self.assertEqual(r, game(seed))

    def test_every_habit_finishes(self):
        for cfg in sim.configs(quick=True):
            if cfg["pen"] != 200000 or cfg["surfaces"] != "wood":
                continue
            r = sim.play(Maze(2, 8, 0.2, 3), cfg, 3)
            self.assertTrue(r["solved"], cfg)

    def test_trail_helps_the_next_ant(self):
        plain = sum(game(s, ants=3, start="relay")["moves"] for s in range(10))
        trail = sum(game(s, ants=3, start="relay", meet="trail")["moves"] for s in range(10))
        self.assertLess(trail, plain)

    def test_a_note_on_ice_never_shows(self):
        m = Maze(2, 6, 0, 1)
        world = sim.World(m, ["ice"] * m.n)
        a = sim.Ant(0, m, {"pen_ink": 200000}, 1)
        self.assertFalse(a.write_wall(world, "X Coral Hall"))
        self.assertEqual(world.marks, {})
        self.assertIn("ice", a.bad)
        self.assertFalse(a.can_write(world))      # it learned


if __name__ == "__main__":
    unittest.main()
