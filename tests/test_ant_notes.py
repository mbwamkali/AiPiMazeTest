"""The ant's memory lives only in its keeper-held notebook (ant/notes.py), and the notes it works out
from those lines match what the offline game's notes mode says, so the trained model sees the same thing."""
import os
import random
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hypermaze import Maze, Game  # noqa: E402
from arena.runs import Store  # noqa: E402
from ant import compact as C  # noqa: E402
from ant.notes import AntNotes  # noqa: E402


class LocalHouse:
    """The same calls as runner/house.py's House, straight into a keeper run (no HTTP)."""

    def __init__(self, run):
        self.run = run

    def room(self):
        return self.run.room_view()

    def walk(self, name):
        try:
            return self.run.walk(name)
        except LookupError:
            return {"error": "no_door"}

    def read(self, page=None):
        return self.run.device_view("notebook", page)["screen"]

    def write(self, line):
        return self.run.device_input("notebook", line)["reply"]


def parsed(screen):
    room, doors, untried, came, open_ = C.parse_screen(screen)
    return room, sorted(doors), untried, came, open_


class TestAntNotes(unittest.TestCase):
    def play(self, seed, dims, size, loops, steps):
        store = Store(tempfile.mkdtemp())
        run, _ = store.create(dims, size, loops, seed)
        house = LocalHouse(run)
        notes = AntNotes(house)
        ref = Game(Maze(dims, size, loops, seed), mode="notes")
        ref_screen = ref.intro()
        view = house.room()
        notes.room(view["room"], view["doors"])
        rng = random.Random(seed)
        for _ in range(steps):
            self.assertEqual(parsed(notes.screen(view)), parsed(ref_screen))
            if view.get("ended"):
                break
            to = rng.choice(view["doors"])
            frm = view["room"]
            view = house.walk(to)
            notes.move(frm, view["room"])
            if not view.get("ended"):
                notes.room(view["room"], view["doors"])
            ref_screen = ref.send(to)
        return run, notes

    def test_same_notes_as_offline_game(self):
        for seed, dims, size, loops in ((1, 2, 6, 0.0), (2, 2, 8, 0.25), (3, 3, 3, 0.1)):
            self.play(seed, dims, size, loops, 150)

    def test_many_pages(self):
        run, notes = self.play(4, 2, 12, 0.3, 400)
        self.assertGreater(notes.page, 3)             # the ant turned pages as they filled
        self.assertEqual(run.moves, sum(r.startswith("MOVE: ") for r in notes.records()))

    def test_only_the_notebook_is_memory(self):
        """Wipe the notebook and the ant has forgotten everything: no hidden memory elsewhere."""
        store = Store(tempfile.mkdtemp())
        run, _ = store.create(2, 6, 0.0, 9)
        house = LocalHouse(run)
        notes = AntNotes(house)
        view = house.room()
        notes.room(view["room"], view["doors"])
        frm = view["room"]
        view = house.walk(view["doors"][0])
        notes.move(frm, view["room"])
        for i in range(35, 0, -1):
            house.write(f"erase {i}")
        self.assertEqual(notes.records(), [])
        self.assertIn("Other visited rooms with untried paths: 0", notes.screen(view))
        self.assertNotIn("first came", notes.screen(view))


if __name__ == "__main__":
    unittest.main()
