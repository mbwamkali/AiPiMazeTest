"""Explainability test: every move the fair teacher makes must be explainable from its screen alone.

A separate 'reader' sees only the screen text (room, doors, walls, wall notes, "You came here from",
"You remember", its notes) plus its own persona, and must arrive at the same command the teacher typed.
If the teacher used anything it can't see, the reader disagrees and the test says which kind of move.
"""
import os
import random
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from hypermaze.core import Maze  # noqa: E402
from ant import personas as P  # noqa: E402
from ant.relay3 import RelayGame, GOOD_WALLS  # noqa: E402


def parse(screen):
    room = re.search(r"You are in the (.+?)\.\n", screen).group(1)
    doors = re.search(r"Paths: (.*)", screen).group(1).split(", ")
    walls_mat = re.search(r"The walls are (.+?)\.", screen).group(1)
    m = re.search(r"You came here from (.+?)\.\n", screen)
    came = m.group(1) if m else None
    m = re.search(r"You remember: (.*), then here\.", screen)
    memory = m.group(1).split(", ") if m else []
    m = re.search(r"Written on the walls:\n((?:  .*\n?)*)", screen)
    marks = [l.strip() for l in m.group(1).splitlines()] if m else []
    m = re.search(r"Your notes about this room:\n((?:  .*\n?)*)", screen)
    notes = [l.strip() for l in m.group(1).splitlines()] if m else []
    m = re.search(r"Your notes about yourself:\n((?:  .*\n?)*)", screen)
    me = [l.strip() for l in m.group(1).splitlines()] if m else []
    return room, doors, walls_mat, came, memory, marks, notes, me


def route_steps(notes, room):
    """From its own ROUTE notes about this room: (the step before it, the step after it, newest is a ROUTE note)."""
    prev = nxt = None
    for n in notes:
        m = re.match(r"^ROUTE: (.+) > (.+)$", n)
        if m:
            if m.group(2) == room:
                prev = m.group(1)
            if m.group(1) == room:
                nxt = m.group(2)
    return prev, nxt, bool(notes) and notes[-1].startswith("ROUTE: ")


def reader(screen, options, persona, seed_key):
    """What the screen alone says to do. Returns the command, or None if it can't tell."""
    room, doors, mat, came, memory, marks, notes, me = parse(screen)
    opts = list(options)
    for o in opts:
        if o.startswith("write ME:"):
            return o
    for o in opts:
        if came and o == f"write {came} -> {room}":
            return o
    for o in opts:
        if o.startswith("write ROUTE:"):
            return o
    bad = " ".join(me)
    prev, nxt, just_wrote = route_steps(notes, room)
    trailing = nxt is not None and (came == nxt or (just_wrote and nxt == "Way Out"))
    write_wall = [o for o in opts if o.startswith("write on wall ")]
    if trailing:
        if write_wall and mat.replace(" ", "_") in GOOD_WALLS and f"{mat} walls did not take" not in bad:
            return write_wall[0]
        if prev in doors:
            return prev
    if "Way Out" in doors:
        return "Way Out"
    if nxt in doors and (came == prev or prev is None):
        return nxt
    for mk in sorted(marks):
        if mk.startswith("out ") and mk[4:] in doors and mk[4:] != came:
            return mk[4:]
    return None   # exploring and backtracking: checked separately


def tried(room, doors, came, memory, notes):
    t = set()
    for n in notes:
        m = re.match(r"^(.+) -> (.+)$", n)
        if m:
            a, b = m.group(1), m.group(2)
            if a == room:
                t.add(b)
            if b == room:
                t.add(a)
    path = memory + [room]
    for a, b in zip(path, path[1:]):
        if b == room:
            t.add(a)
        if a == room:
            t.add(b)
    return t


class TestFairExplainable(unittest.TestCase):
    def test_teacher_moves_follow_from_the_screen(self):
        rng = random.Random(4)
        agree = total = 0
        misses = {}
        for i in range(40):
            size = rng.choice([6, 8, 10, 12])
            g = RelayGame(Maze(2, size, rng.choice([0.0, 0.2]), seed=880000 + i), ants=1 + i % 3, seed=880000 + i,
                          briefing="size", teacher="fair", memory=10)
            steps = 0
            while not g.done and steps < 3000:
                steps += 1
                screen, options = g.screen()
                key, kind = g.teach()
                guess = reader(screen, options, g.cur.persona, None)
                if guess is not None or kind in ("note", "mark", "write_out", "goal", "follow", "route", "back_trail"):
                    total += 1
                    if guess == key:
                        agree += 1
                    else:
                        misses[kind] = misses.get(kind, 0) + 1
                elif kind in ("back", "back_loop", "unstick", "new", "new_more"):
                    # Tremaux from the screen: count each door in this room's notes and in memory
                    room, doors, mat, came, memory, marks, notes, me = parse(screen)
                    path = memory + [room]
                    uses = {}
                    for d in doors:
                        n_notes = sum(1 for n in notes if n in (f"{room} -> {d}", f"{d} -> {room}"))
                        n_mem = sum(1 for p_, q in zip(path, path[1:]) if {p_, q} == {room, d})
                        uses[d] = max(n_notes, n_mem)
                    if kind == "back_loop":
                        ok = key == came and uses.get(came) == 1
                    elif kind in ("new", "new_more"):
                        ok = uses.get(key) == 0
                    elif kind == "back":
                        ok = uses.get(key) == 1 and (key == came or uses.get(came) != 1) and \
                            not any(v == 0 for v in uses.values())
                    else:
                        ok = uses.get(key) == min(uses.values()) and min(uses.values()) >= 2
                    total += 1
                    agree += ok
                    if not ok:
                        misses[kind] = misses.get(kind, 0) + 1
                g.apply(options[key])
        rate = agree / max(1, total)
        print(f"\nfair teacher explainable from the screen: {rate:.3f} of {total} moves; misses {misses}")
        self.assertGreater(rate, 0.97, misses)


if __name__ == "__main__":
    unittest.main()
