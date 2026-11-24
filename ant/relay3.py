"""Relay games for the v3 ant lessons and scoring (training side; not part of an ant machine's bundle).

Ants come into one maze one after another. Each plays blind with its own pen and notebook memory
(a notebook line that doesn't come out readable is forgotten). The walls are shared and keep every
note as it came out. When an ant reaches the Way Out while ants are still to come, it may go back
and mark the way: it walks back along its route toward the start room, writing "out <room>" at every
junction and in the start room, then turns round and leaves. A later ant follows "out" notes.
An ant that sees one of its wall notes fail (nothing showed, or letters lost) writes that in its notebook
("WALLS: ice walls did not take my note") and stops writing on that material.

Team briefing (the hard rule allows nothing about teammates outside the room): as each ant comes in it is
told once how many ants come in after it, and writes "TEAM: 2 ants come in after me" on page 1. The
screen shows that from its notebook, and going back to mark the way is offered at the Way Out only when
its notebook says ants come after.

The screen each turn is ant/view3.py's, built only from what the ant could see or remembers.
Teacher (the lesson bot): RelayGame.teach() gives the key it would answer, and a kind for comments.

    g = RelayGame(maze, ants=3, mix={"brick": 0.2}, seed=7)
    while not g.done:
        text, options = g.screen()
        key, kind = g.teach()
        g.apply(options[key])
"""
import random
from collections import deque

from arena.pen import LOST, PAPER, Pen, assign_surfaces
from . import personas as P
from . import view3 as V

READABLE = 0.25


def readable(line, intended):
    if not line:
        return False
    body = line.split(") ", 1)[1] if line.startswith("(") and ") " in line else line
    letters = [c for c in body if c != " "]
    return bool(letters) and sum(c == LOST for c in letters) <= READABLE * len(intended.replace(" ", ""))


class Player:
    def __init__(self, i, persona, maze, pen_ink, wear, seed):
        self.i, self.persona, self.m = i, persona, maze
        self.pen = Pen(pen_ink, 10, wear)
        self.seed = f"{seed}:{i}"
        self.pos = maze.start
        self.out = False
        self.moves = 0
        self.writes = 0
        self.doors, self.tried, self.came, self.visited = {}, set(), {}, set()
        self.bad = set()          # materials it saw a note fail on (kept in its notes)
        self.route = None         # start ... Way Out, once it has been there
        self.trailing = False     # going back to mark the way
        self.marked = False       # it has already marked the way
        self.following = False
        self.prev = None
        self.last = None          # for the "Last action" line
        self.rng = random.Random(self.seed)
        self._note(self.pos, None)

    def _paper(self, text):
        line, _ = self.pen.render(text, PAPER, self.seed)
        return readable(line, text)

    def _note(self, room, frm):
        m = self.m
        ok_move = frm is None or self._paper(f"> {m.names[room]}")
        if room not in self.doors and self._paper(
                f"ROOM: {m.names[room]} | doors: " + "; ".join(m.names[x] for x in m.adj[room])):
            self.doors[room] = list(m.adj[room])
        if ok_move:
            if frm is not None:
                self.tried |= {(frm, room), (room, frm)}
            if room not in self.visited:
                self.visited.add(room)
                if frm is not None:
                    self.came[room] = frm

    def known(self, room):
        return self.m.adj[room] if room == self.pos else self.doors.get(room, ())

    def fresh(self, room):
        return [x for x in self.known(room) if (room, x) not in self.tried and x not in self.visited]

    def path(self, target, extra=(), frontier=False):
        adj = {}
        for a, b in zip(extra, extra[1:]):
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
        prev, q = {self.pos: None}, deque([self.pos])
        while q:
            c = q.popleft()
            if (frontier and c != self.pos and self.fresh(c)) or (not frontier and c == target):
                out = [c]
                while prev[out[-1]] is not None:
                    out.append(prev[out[-1]])
                return out[::-1]
            for x in set(self.known(c)) | adj.get(c, set()):
                if x not in prev and (x in self.doors or x in adj or x == target):
                    if x == self.m.goal and target != self.m.goal:
                        continue
                    prev[x] = c
                    q.append(x)
        return None


class RelayGame:
    def __init__(self, maze, ants=1, mix=None, seed=0, pen_ink=200000, wear=None, cap_factor=8, briefing="team"):
        """briefing: "team"  each ant is told once, as it comes in, how many come after it, and writes it on page 1
                     "none"  told nothing about the team: going back to mark the way is offered at every Way Out"""
        self.m = maze
        self.surfaces = assign_surfaces(maze.n, seed, mix or {})
        self.marks = {}
        self.players = [Player(i, P.NAMES[(seed + i) % len(P.NAMES)], maze, pen_ink, wear, seed) for i in range(ants)]
        self.k = 0                 # the ant now in the house
        self.cap = cap_factor * maze.n
        self.turns = 0
        self.briefing = briefing
        for i, p in enumerate(self.players):
            after = len(self.players) - i - 1
            p.after = after if briefing == "team" and p._paper(f"TEAM: {after} ants come in after me") else None

    @property
    def done(self):
        return self.k >= len(self.players) or self.turns >= self.cap * len(self.players)

    @property
    def cur(self):
        return self.players[self.k]

    def to_come(self):
        """What the ant knows of the ants coming after it: its own briefing note (None if it has none)."""
        return self.cur.after

    def offer_mark(self):
        a = self.cur
        if a.marked or a.trailing or self.m.goal not in self.m.adj[a.pos]:
            return False
        return self.briefing == "none" or bool(a.after)

    # ---- what the ant sees ----------------------------------------------------------
    def screen(self):
        a, m = self.cur, self.m
        cur = a.pos
        names = m.names
        doors_i = sorted(m.adj[cur], key=lambda x: (names[x].lower(), names[x]))
        doors = [names[x] for x in doors_i]
        untried = {}
        for x in doors_i:
            if (cur, x) not in a.tried:
                untried[names[x]] = "visited" if x in a.visited else "new"
        came = names[a.came[cur]] if cur in a.came else None
        open_ = sum(1 for r in a.visited if r != cur and any((r, x) not in a.tried for x in a.doors.get(r, ())))
        write = None
        if a.trailing and a.route and cur in a.route:
            k = a.route.index(cur)
            note = f"out {names[a.route[k + 1]]}"
            if (len(m.adj[cur]) >= 3 or cur == m.start) and note not in self.marks.get(cur, []):
                write = note
        text, options = V.view(names[cur], doors, untried, came, open_, a.last, walls=self.surfaces[cur],
                               marks=self.marks.get(cur, []), write=write)
        extra = []
        if a.bad:
            extra.append("Your notes: notes on " + ", ".join(sorted(s.replace("_", " ") for s in a.bad))
                         + " walls did not come out")
        if a.after:
            extra.append(f"Your notes: {a.after} ants come in after you")
        if self.offer_mark():
            back = len(a.path(m.start) or [cur]) - 1
            extra.append(f"B - go back and mark the way for the ants still to come "
                         f"(the way back to the start: {back} rooms; you took {a.moves} moves to get here)")
            options["B"] = ("mark",)
        if a.trailing and a.route and cur in a.route and cur != m.start:
            k = a.route.index(cur)
            extra.append(f"Marking the way: the start room is back through {names[a.route[k - 1]]}")
        if extra:
            lines = text.split("\n")
            text = "\n".join(lines[:-2] + extra + lines[-2:])
        return text, options

    # ---- the lesson bot --------------------------------------------------------------
    def teach(self):
        """(key, kind) the teacher answers for the current screen."""
        a, m = self.cur, self.m
        text, options = self.screen()
        by_door = {v[1]: k for k, v in options.items() if v[0] == "go"}
        names, cur = m.names, a.pos
        if "B" in options:
            back = len(a.path(m.start) or [cur]) - 1
            followers = a.after if a.after is not None else 1          # unbriefed: hope for one
            if followers * (a.moves - back) > 2 * back + 2:           # worth the walk for the ants to come
                return "B", "mark"
            return by_door[names[m.goal]], "goal"
        if a.trailing:
            k = a.route.index(cur) if cur in a.route else None
            if "W" in options and self.surfaces[cur] not in a.bad:
                return "W", "write_out"
            if k is not None and cur != m.start:
                return by_door[names[a.route[k - 1]]], "back_trail"
        if names[m.goal] in by_door and not a.trailing:
            return by_door[names[m.goal]], "goal"
        if a.route:                                            # it knows the way: go
            p = a.path(m.goal, a.route)
            if p and len(p) > 1:
                return by_door[names[p[1]]], "route"
        notes = V.read_notes(self.marks.get(cur, []), list(by_door))
        for d, kinds in notes.items():
            x = m.find(d)
            if "out" in kinds and (cur, x) not in a.tried:
                return by_door[d], "follow"
        if a.following:
            ahead = [x for x in m.adj[cur] if x != a.prev and (cur, x) not in a.tried]
            if len(m.adj[cur]) == 2 and ahead:
                return by_door[names[ahead[0]]], "follow_on"
        fresh = a.fresh(cur)
        if fresh:
            order = sorted(fresh, key=lambda x: (names[x].lower(), names[x]))
            pi = P.NAMES.index(a.persona) if a.persona in P.NAMES else 0
            pick = order[-1] if pi == 0 else order[0] if pi == 1 else \
                random.Random(f"{a.seed}:{a.moves}:{cur}").choice(order)
            return by_door[names[pick]], "new_more" if len(fresh) > 1 else "new"
        p = a.path(None, frontier=True)
        if p and len(p) > 1:
            return by_door[names[p[1]]], "back"
        return by_door[names[random.Random(f"{a.seed}:{a.moves}:{cur}").choice(m.adj[cur])]], "lost"

    # ---- doing it --------------------------------------------------------------------
    def apply(self, action):
        a, m = self.cur, self.m
        self.turns += 1
        if action is None or action[0] == "map":
            a.last = ("map",)
            return
        if action[0] == "mark":
            a.route = (a.path(m.start) or [a.pos])[::-1] + [m.goal]
            a.trailing = True
            a.last = ("mark",)
            return
        if action[0] == "write":
            text = action[1]
            surf = self.surfaces[a.pos]
            line, _ = a.pen.render(text, surf, a.seed)
            a.moves += 1
            a.writes += 1
            if line:
                self.marks.setdefault(a.pos, []).append(line)
            if (not readable(line, text) or LOST in line) and \
                    a._paper(f"WALLS: {surf.replace('_', ' ')} walls did not take my note"):
                a.bad.add(surf)                      # remembered only if that notebook line came out
            a.last = ("wrote", line)
            return
        to = m.find(action[1])
        if to not in m.adj[a.pos]:
            a.last = ("invalid",)
            return
        frm = a.pos
        followed = any("out" in k for d, k in V.read_notes(self.marks.get(frm, []), [action[1]]).items())
        a.following = followed or (a.following and len(m.adj[frm]) == 2)
        left_new = bool(a.fresh(frm)) and to in a.visited
        a.prev, a.pos = frm, to
        a.moves += 1
        a.last = ("walk", m.names[frm], left_new)
        if to == m.goal:
            a.out = True
            self.k += 1
            return
        a._note(to, frm)
        if a.trailing and (frm == m.start or (a.route and to not in a.route)):
            a.trailing, a.marked = False, True        # marked as far as the start room: now out

    def result(self):
        ps = self.players
        return {"ants": len(ps), "all_out": all(p.out for p in ps), "total_moves": sum(p.moves for p in ps),
                "moves": [p.moves for p in ps], "writes": sum(p.writes for p in ps), "shortest": self.m.shortest,
                "rooms": self.m.n}
