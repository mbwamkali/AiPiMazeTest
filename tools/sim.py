#!/usr/bin/env python3
"""Bot simulation for the pen, the walls and teams of ants (no keeper, no model, standard library only).

Each simulated ant plays blind, the way a real one must: it knows only the room it stands in, what
its own notebook still holds, what is written on the walls it reads, and what other ants told it
when they stood in the same room. Its pen is the keeper's own (arena/pen.py): hidden ink and tip,
wall materials scattered by seed, notebook lines and wall notes that can come out faint, gappy or
not at all. A notebook line that does not come out cleanly is forgotten.

Settings swept (one game = one maze, all ants start in the same room, they move in turns):
  ants      1, 2, 3
  walls     none        never writes on walls
            dead        on walking back out of a finished branch, writes "X <room>" (that way is done)
            went        before leaving a junction by a new door, writes "went <room>" (go another way)
            dead+went   both
  meet      none        ants only share what they know when they happen to meet
            report      after finishing a branch, go back to the start room to pass the news on;
                        an ant that finds the Way Out walks back to the start and waits there until
                        every other ant has heard the route (or is out), then leaves
            trail       an ant that finds the Way Out walks back to the start room writing "out <room>"
                        at every junction on its route (the next room toward the Way Out), then leaves;
                        an ant that reads such a note follows the trail
            radio       not a game option, a yardstick: the moment one ant finds the Way Out every other
                        ant knows its route, wherever it is (the most route-sharing could ever save)
  start     together    all ants start at once
            relay       each ant starts only when the one before it is out (it can still read the walls)
  surfaces  wood        every wall wood
            mixed       brick 15%, damp stone 10%, ice 5%
            hard        brick 30%, damp stone 15%, ice 10%, glass 5%
  pen       2,000 / 10,000 / 50,000 / 200,000 letters on paper

An ant that saw a wall note fail on some material stops writing on that material.
Score: team total moves (walks plus wall writes), counted when the last ant is out. Waiting is not a
move; total turns (wall-clock rounds) are reported too. A game that runs past the turn cap is a
failure and is scored at the cap.

  python3 tools/sim.py --mazes 200 --size 16 --loops 0.25 --out sim-results   (writes .csv and .md)
  python3 tools/sim.py --quick                                                 (a small smoke run)
"""
import argparse
import csv
import itertools
import os
import random
import sys
import time
from collections import deque
from multiprocessing import Pool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from arena.pen import PAPER, Pen, assign_surfaces, LOST  # noqa: E402
from hypermaze.core import Maze  # noqa: E402

SURFACE_MIXES = {
    "wood": {},
    "mixed": {"brick": 0.15, "damp_stone": 0.10, "ice": 0.05},
    "hard": {"brick": 0.30, "damp_stone": 0.15, "ice": 0.10, "glass": 0.05},
}
WEAR = {"real": {}, "gentle": {"brick": 0.002, "stone": 0.002}}   # tip lost per letter (real = pen.py defaults)
WALL_HABITS = ("none", "dead", "went", "dead+went")
MEET_HABITS = ("none", "report", "trail", "radio")
READABLE = 0.25          # a note with more than this share of letters lost can't be read


def readable(line, intended):
    """True if a written line can still be read: something came out and few letters were lost."""
    if not line:
        return False
    body = line.split(") ", 1)[1] if line.startswith("(") and ") " in line else line
    letters = [c for c in body if c != " "]
    return bool(letters) and sum(c == LOST for c in letters) <= READABLE * len([c for c in intended if c != " "])


class Ant:
    def __init__(self, i, maze, rb, seed):
        self.i, self.m = i, maze
        self.pen = Pen(rb["pen_ink"], 10, rb.get("wear"))
        self.seed = f"{seed}:{i}"
        self.pos = maze.start
        self.out = False
        self.moves = 0
        self.writes = 0
        self.failed_writes = 0
        # memory: only what the notebook still holds
        self.doors = {}          # room -> doors (from its own ROOM lines)
        self.tried = set()       # (a, b) door pairs it walked, both ways
        self.came = {}           # room -> room it first came in from
        self.visited = set()
        self.dead = set()        # (room, door) known finished, no Way Out that way (own, read or heard)
        self.went = set()        # (room, door) someone else is exploring
        self.route = None        # rooms from the start room to the Way Out, once known
        self.bad = set()         # materials it saw a wall note fail on
        self.news = False        # it has something new to tell (a finished branch or the route)
        self.heading_home = False
        self.waiting = False     # found the Way Out, waiting at the start room
        self.heard_by = set()    # ants that heard its route
        self.branches = 0        # branches finished since it last went home
        self.pending_dead = None
        self.trailing = False    # walking back from the Way Out, writing the trail
        self.hints = {}          # room -> room toward the Way Out (read from "out" notes)
        self.following = False
        self.prev = None
        self.wrote_trail = set()
        self._note(self.pos, None)

    # ---- the notebook (paper through the pen) -------------------------------------
    def _paper(self, text):
        line, _ = self.pen.render(text, PAPER, self.seed)
        return readable(line, text)

    def _note(self, room, frm):
        m = self.m
        ok_move = frm is None or self._paper(f"> {m.names[room]}")
        if room not in self.doors:
            if self._paper(f"ROOM: {m.names[room]} | doors: " + "; ".join(m.names[x] for x in m.adj[room])):
                self.doors[room] = list(m.adj[room])
        if ok_move:
            if frm is not None:
                self.tried.add((frm, room))
                self.tried.add((room, frm))
            if room not in self.visited:
                self.visited.add(room)
                if frm is not None:
                    self.came[room] = frm

    # ---- the walls ----------------------------------------------------------------
    def write_wall(self, world, text):
        """Write a note on this room's walls (costs a move). Returns True if it came out readable."""
        surf = world.surfaces[self.pos]
        line, _ = self.pen.render(text, surf, self.seed)
        self.moves += 1
        self.writes += 1
        ok = readable(line, text)
        if ok:
            world.marks.setdefault(self.pos, []).append(text)
        else:
            self.failed_writes += 1
        if not ok or LOST in line:
            self.bad.add(surf)               # it saw the note fail or come out damaged: not here again
        return ok

    def can_write(self, world):
        return world.surfaces[self.pos] not in self.bad and self.pen.ink > 0 and self.pen.tip > 0

    def read_walls(self, world):
        names = {nm: c for c, nm in enumerate(self.m.names)}
        for text in world.marks.get(self.pos, []):
            kind, _, nm = text.partition(" ")
            c = names.get(nm)
            if c is None:
                continue
            if kind == "X":
                self.dead.add((self.pos, c))
            elif kind == "went":
                self.went.add((self.pos, c))
            elif kind == "out":
                self.hints[self.pos] = c

    # ---- talking (same room only) ---------------------------------------------------
    def tell(self, other):
        other.dead |= self.dead
        if self.route and not other.route:
            other.route = list(self.route)
            self.heard_by.add(other.i)
        if self.news:
            self.news = False

    # ---- exploring ----------------------------------------------------------------
    def fresh(self, room):
        """Doors out of a room it hasn't tried, nobody marked done, into rooms it doesn't know."""
        ds = self.m.adj[room] if room == self.pos else self.doors.get(room, ())
        return [x for x in ds if (room, x) not in self.tried and (room, x) not in self.dead and x not in self.visited]

    def finished(self, room):
        """Nothing left to try in this room or anywhere it first reached from here."""
        kids = {}
        for c, f in self.came.items():
            kids.setdefault(f, []).append(c)
        stack, seen = [room], set()
        while stack:
            r = stack.pop()
            if r in seen:
                continue
            seen.add(r)
            if self.fresh(r):
                return False
            stack.extend(kids.get(r, ()))
        return True

    def path_to_frontier(self):
        """Shortest path over known doors to the nearest room with a fresh door, or None."""
        prev, q = {self.pos: None}, deque([self.pos])
        while q:
            c = q.popleft()
            if c != self.pos and self.fresh(c):
                path = [c]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])
                return path[::-1]
            ds = self.m.adj[c] if c == self.pos else self.doors.get(c, ())
            for x in ds:
                if x not in prev and x in self.doors:
                    prev[x] = c
                    q.append(x)
        return None

    # ---- moving -------------------------------------------------------------------
    def path_to(self, target, extra=()):
        """Shortest path over the doors it knows (its notebook plus a heard route), or None."""
        adj = {}
        for r, ds in self.doors.items():
            adj.setdefault(r, set()).update(ds)
        for a, b in zip(extra, extra[1:]):
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
        adj.setdefault(self.pos, set()).update(self.m.adj[self.pos])     # it can always see its own doors
        prev, q = {self.pos: None}, deque([self.pos])
        while q:
            c = q.popleft()
            if c == target:
                break
            for x in adj.get(c, ()):
                if x not in prev:
                    prev[x] = c
                    q.append(x)
        if target not in prev:
            return None
        path = [target]
        while prev[path[-1]] is not None:
            path.append(prev[path[-1]])
        return path[::-1]

    def walk(self, world, to):
        frm = self.pos
        self.prev = frm
        self.pos = to
        self.moves += 1
        if to == self.m.goal:
            self.out = True
            return
        self._note(to, frm)
        self.read_walls(world)


class World:
    def __init__(self, maze, surfaces):
        self.m = maze
        self.surfaces = surfaces
        self.marks = {}


def act(a, ants, world, walls, meet):
    m = a.m
    cur = a.pos
    # 1. the Way Out is right here
    if m.goal in m.adj[cur] and meet == "trail" and a.route is None and not a.following \
            and any(not b.out for b in ants if b is not a):
        a.route = (a.path_to(m.start) or [cur])[::-1] + [m.goal]
        a.trailing = True
    if a.trailing:
        if cur not in a.route:
            a.trailing = False
        else:
            k = a.route.index(cur)
            note = f"out {m.names[a.route[k + 1]]}"
            if (len(m.adj[cur]) >= 3 or cur == m.start) and note not in world.marks.get(cur, []) \
                    and a.can_write(world) and (cur, a.route[k + 1]) not in a.wrote_trail:
                a.wrote_trail.add((cur, a.route[k + 1]))   # junctions and the start room get a note
                a.write_wall(world, note)
                return
            if cur == m.start:
                a.trailing = False
            else:
                a.walk(world, a.route[k - 1])
                return
    if m.goal in m.adj[cur] and meet == "radio" and a.route is None:
        a.route = (a.path_to(m.start) or [cur])[::-1] + [m.goal]
        for b in ants:
            if b is not a and not b.route:
                b.route = list(a.route)
    if m.goal in m.adj[cur]:
        if meet == "report" and len(ants) > 1 and not a.waiting and any(not b.out for b in ants if b is not a) \
                and a.route is None:
            a.route = (a.path_to(m.start) or [cur])[::-1] + [m.goal]      # start ... here, Way Out
            a.heading_home, a.news = True, True
        elif not a.heading_home and not a.waiting:
            a.walk(world, m.goal)
            return
    # 2. waiting at the start room with the route: leave once everyone else has heard it or is out
    if a.waiting:
        if all(b.out or b.i in a.heard_by or b.route for b in ants if b is not a):
            a.waiting = False
            a.heading_home = False
            path = a.path_to(m.goal, a.route)
            if path and len(path) > 1:
                a.walk(world, path[1])
        return
    # 3. heading home to report
    if a.heading_home:
        if cur == m.start:
            a.heading_home = False
            if a.route:
                a.waiting = True
            return
        path = a.path_to(m.start)
        if path and len(path) > 1:
            a.walk(world, path[1])
            return
        a.heading_home = False
    # 4. it knows the route: follow it
    if a.route:
        path = a.path_to(m.goal, a.route)
        if path and len(path) > 1:
            a.walk(world, path[1])
            return
    # 4b. a trail toward the Way Out: follow it, through corridors to the next note
    if cur in a.hints and (cur, a.hints[cur]) not in a.tried:     # each note is followed once
        a.following = True
        a.walk(world, a.hints[cur])
        return
    if a.following:
        ds = [x for x in m.adj[cur] if x != a.prev and (cur, x) not in a.tried]
        if len(m.adj[cur]) == 2 and ds:
            a.walk(world, ds[0])
            return
        a.following = False
    # 5. explore: a door it hasn't tried, that nobody marked done, into a room it doesn't know
    doors = m.adj[cur]
    fresh = a.fresh(cur)
    if fresh:
        free = [x for x in fresh if (cur, x) not in a.went] or fresh
        to = free[0] if a.i % 2 == 0 else free[-1]               # ants split: first door / last door
        if "went" in walls and len(doors) >= 3 and a.can_write(world):
            if not any(t == f"went {m.names[to]}" for t in world.marks.get(cur, [])):
                a.write_wall(world, f"went {m.names[to]}")
                return
        a.walk(world, to)
        return
    # 6. nothing new here: head for the nearest room it knows that still has a door to try
    path = a.path_to_frontier()
    if path and len(path) > 1:
        nxt = path[1]
        if a.came.get(cur) == nxt and (nxt, cur) not in a.dead and a.finished(cur):   # leaving a branch just finished
            a.dead.add((nxt, cur))
            a.pending_dead = cur
            a.branches += 1
            if meet == "report" and len(ants) > 1:
                a.news = True
                if a.branches >= 3:                              # enough news to be worth the walk home
                    a.branches = 0
                    a.heading_home = True
        a.walk(world, nxt)
        return
    # 7. nothing left that it knows of (its memory failed): any door not marked done
    left = [x for x in doors if (cur, x) not in a.dead] or list(doors)
    a.walk(world, random.Random(f"{a.seed}:{a.moves}").choice(left))


def act_with_marks(a, ants, world, walls, meet):
    """act(), plus: right after walking back out of a finished branch, write "X <that room>" here."""
    if "dead" in walls and getattr(a, "pending_dead", None) is not None and not a.out:
        c, a.pending_dead = a.pending_dead, None
        if a.can_write(world) and f"X {a.m.names[c]}" not in world.marks.get(a.pos, []):
            a.write_wall(world, f"X {a.m.names[c]}")
            return
    a.pending_dead = None
    act(a, ants, world, walls, meet)


def run_one(args):
    cfg, seed, size, loops = args
    maze = Maze(2, size, loops, seed)
    res = play(maze, cfg, seed)
    return dict(cfg, seed=seed, shortest=maze.shortest, rooms=maze.n, **res)


def play(maze, cfg, seed):
    m = maze
    world = World(m, assign_surfaces(m.n, seed, SURFACE_MIXES[cfg["surfaces"]]))
    ants = [Ant(i, m, {"pen_ink": cfg["pen"], "wear": WEAR[cfg.get("wear", "real")]}, seed) for i in range(cfg["ants"])]
    cap = 30 * m.n
    turn = 0
    for turn in range(1, cap + 1):
        active = [a for a in ants if not a.out]
        if not active:
            break
        if cfg.get("start") == "relay":
            active = active[:1]                     # the next ant waits outside until the one before is out
        for a in active:
            if a.out:
                continue
            for b in active:
                if b is not a and not b.out and b.pos == a.pos:
                    a.tell(b)
                    b.tell(a)
            act_with_marks(a, ants, world, cfg["walls"], cfg["meet"])
    total = sum(a.moves for a in ants)
    done = all(a.out for a in ants)
    return {"moves": total if done else max(total, cap * len(ants)), "solved": done, "turns": turn,
            "writes": sum(a.writes for a in ants), "failed_writes": sum(a.failed_writes for a in ants),
            "ink_used": round(sum(a.pen.full - a.pen.ink for a in ants)),
            "dead_pens": sum(1 for a in ants if a.pen.ink <= 0 or a.pen.tip <= 0),
            "first_out_moves": min((a.moves for a in ants if a.out), default=None)}


def configs(quick=False):
    ants = (1, 2, 3)
    pens = (2000, 10000, 50000, 200000)
    surfaces = tuple(SURFACE_MIXES)
    if quick:
        pens, surfaces = (10000, 200000), ("wood", "hard")
    for n, w, mt, s, p, wr, st in itertools.product(ants, WALL_HABITS, MEET_HABITS, surfaces, pens, tuple(WEAR),
                                                    ("together", "relay")):
        if n == 1 and (mt != "none" or st != "together"):
            continue                      # meeting up needs company
        if st == "relay" and mt in ("report", "radio"):
            continue                      # nobody to report to: the others haven't come in yet
        if s == "wood" and wr != "real":
            continue                      # wood doesn't wear the tip either way
        yield {"ants": n, "start": st, "walls": w, "meet": mt, "surfaces": s, "pen": p, "wear": wr}


def summarize(rows):
    groups = {}
    for r in rows:
        k = (r["ants"], r["walls"], r["meet"], r["surfaces"], r["pen"], r["wear"], r["start"])
        groups.setdefault(k, []).append(r)
    out = []
    for k, rs in sorted(groups.items()):
        n = len(rs)
        mean = lambda f: sum(r[f] for r in rs) / n
        out.append({"ants": k[0], "walls": k[1], "meet": k[2], "surfaces": k[3], "pen": k[4], "wear": k[5], "start": k[6], "games": n,
                    "moves": round(mean("moves"), 1), "per_ant": round(mean("moves") / k[0], 1),
                    "vs_shortest": round(sum(r["moves"] / k[0] / r["shortest"] for r in rs) / n, 2),
                    "solved": round(sum(r["solved"] for r in rs) / n, 3), "turns": round(mean("turns"), 1),
                    "writes": round(mean("writes"), 1), "failed": round(mean("failed_writes"), 1),
                    "ink": round(mean("ink_used")), "dead_pens": round(mean("dead_pens"), 2)})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mazes", type=int, default=200)
    ap.add_argument("--size", type=int, default=16)
    ap.add_argument("--loops", type=float, default=0.25)
    ap.add_argument("--seed-base", type=int, default=500000)
    ap.add_argument("--out", default="sim-results")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--quick", action="store_true", help="a small smoke run (20 mazes, fewer settings)")
    a = ap.parse_args(argv)
    if a.quick:
        a.mazes = min(a.mazes, 20)
    work = [(cfg, a.seed_base + i, a.size, a.loops) for cfg in configs(a.quick) for i in range(a.mazes)]
    t0 = time.time()
    with Pool(a.jobs) as pool:
        rows = pool.map(run_one, work, chunksize=20)
    summ = summarize(rows)
    with open(a.out + ".csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summ[0]))
        w.writeheader()
        w.writerows(summ)
    print(f"{len(rows)} games in {time.time() - t0:.0f}s -> {a.out}.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
