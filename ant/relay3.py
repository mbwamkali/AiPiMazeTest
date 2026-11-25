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

import os
import re
import sys

from arena.notebook import Notebook
from arena.pen import LOST, PAPER, Pen, assign_surfaces
from . import personas as P
from . import view3 as V

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runner"))
import house as H   # noqa: E402  the shared screen a person sees (runner/house.py)

READABLE = 0.25
NB = Notebook()                      # only for its line wrapping (60 characters), so notes look as on the keeper
GOOD_WALLS = ("wood", "plaster")   # the strict teacher only writes trail notes on these (learned from outcomes)


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
        self.nb_tried, self.nb_came, self.nb_visited = set(), {}, set()   # from its notebook only
        self.bad = set()          # materials it saw a note fail on (kept in its notes)
        self.route = None         # start ... Way Out, once it has been there
        self.trailing = False     # going back to mark the way
        self.marked = False       # it has already marked the way
        self.wrote = set()        # rooms where it already tried to write its trail note
        self.followed = set()     # (room, door) "out" notes it already followed
        self.route_note = None    # its route, if it wrote a readable ROUTE line in its notebook (strictR, human)
        self.loop_back = False    # arrived somewhere it had been, through a door it had never used
        self.following = False
        self.prev = None
        self.last = None          # for the "Last action" line
        self.rng = random.Random(self.seed)
        self.lines = []
        self.memory = 0           # how many recent rooms it remembers without notes (human mode)
        self.notebook = True      # human mode: False = no notebook at all
        self.note_rate = 1.0      # share of notes it actually writes (a sloppy note-keeper writes fewer)
        self.note_rng = random.Random(f"notes:{seed}:{i}")
        self.recent = [self.pos]  # the rooms it walked through lately, oldest first, ending here
        self.full_moves = False   # human mode writes "MOVE: A -> B" so a room's notes show both ends
        self.fair_notes = False   # fair mode: notebook lines in house.py's "A -> B" form
        self.auto_notes = True    # fair mode: False = nothing is written unless the ant writes it
        self.pending = None       # fair, own notes: the move it could write down now (from, to)
        self.pending_me = None    # fair, own notes: the briefing it could write down ("ME: ...")
        self._note(self.pos, None)

    def _paper(self, text):
        if not self.notebook:
            return False
        if self.note_rate < 1.0 and self.note_rng.random() >= self.note_rate:
            return False                       # didn't bother to write this one
        line, _ = self.pen.render(text, PAPER, self.seed)
        ok = readable(line, text)
        if ok:                               # what its notebook holds, wrapped like the keeper's notebook does
            self.lines.extend(NB._wrap(text) if self.fair_notes else [text])
        return ok

    def _note(self, room, frm):
        m = self.m
        if self.fair_notes:              # fair: "A -> B", written by the notebook (--auto-notes) or by the ant itself
            if frm is not None and not self.auto_notes:
                self.pending = (frm, room)   # it may write this down (an action it chooses), or not
                return self._note_known(room, frm, False)
            ok_move = self._paper(f"{m.names[frm]} -> {m.names[room]}") if frm is not None else True
            if room not in self.doors and ok_move:
                self.doors[room] = list(m.adj[room])
            return self._note_known(room, frm, ok_move)
        ok_move = frm is None or self._paper(f"MOVE: {m.names[frm]} -> {m.names[room]}" if self.full_moves
                                             else f"> {m.names[room]}")
        if room not in self.doors and self._paper(
                f"ROOM: {m.names[room]} | doors: " + "; ".join(m.names[x] for x in m.adj[room])):
            self.doors[room] = list(m.adj[room])
        self._note_known(room, frm, ok_move)

    def _note_known(self, room, frm, ok_move):
        if ok_move:                        # what its notebook now holds (long term)
            if frm is not None:
                self.nb_tried |= {(frm, room), (room, frm)}
            if room not in self.nb_visited:
                self.nb_visited.add(room)
                if frm is not None:
                    self.nb_came[room] = frm
        self._merge()

    def remember(self, room):
        """Short-term memory: the last `memory` rooms before this one; older rooms are forgotten unless noted."""
        self.recent = (self.recent + [room])[-(self.memory + 1):] if self.memory else [room]
        self._merge()

    def _merge(self):
        """What it knows right now = what its notebook holds + what it still remembers (nothing older)."""
        rs = self.recent
        self.tried = set(self.nb_tried) | {(a, b) for a, b in zip(rs, rs[1:])} | {(b, a) for a, b in zip(rs, rs[1:])}
        self.visited = set(self.nb_visited) | set(rs)
        self.came = dict(self.nb_came)
        for i, r in enumerate(rs[1:], 1):
            if r not in self.came and rs[i - 1] != r:
                self.came[r] = rs[i - 1]

    def chain(self):
        """Rooms from the start room to here along "first came from" (its own notebook), or None."""
        out, c = [self.pos], self.pos
        while c in self.came:
            c = self.came[c]
            if c in out:
                return None
            out.append(c)
        return out[::-1] if c == self.m.start else None

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
    def __init__(self, maze, ants=1, mix=None, seed=0, pen_ink=200000, wear=None, cap_factor=8, briefing="team",
                 teacher="tagged", memory=0, notebook=True, note_rate=1.0, auto_notes=False):
        """briefing: "team"  each ant is told once, as it comes in, how many come after it, and writes it on page 1
                     "size"  told once only the team's size ("you are one of 3"), not its place in line
                     "none"  told nothing about the team: going back to mark the way is offered at every Way Out"""
        self.m = maze
        self.surfaces = assign_surfaces(maze.n, seed, mix or {})
        self.marks = {}
        self.players = [Player(i, P.NAMES[(seed + i) % len(P.NAMES)], maze, pen_ink, wear, seed) for i in range(ants)]
        self.k = 0                 # the ant now in the house
        self.cap = cap_factor * maze.n
        self.turns = 0
        self.briefing = briefing
        # strict: every move the teacher makes is justified by the screen alone (no worked-out door tags):
        #   explore new doors, go back only through "you first came from here", mark the way back from the
        #   Way Out along that same chain with a note in every room, and walk out by following "out" notes.
        # tagged: the code tags the door toward the nearest untried room / along the known route (v3b-style).
        self.teacher = teacher
        if teacher in ("human", "fair"):
            for p in self.players:
                p.full_moves = True
                p.fair_notes = teacher == "fair"
                p.auto_notes = auto_notes
                p.memory, p.notebook, p.note_rate = memory, notebook, note_rate
                if not notebook:
                    p.nb_tried, p.nb_visited, p.nb_came, p.doors, p.lines = set(), set(), {}, {}, []
                    p._merge()
        n = len(self.players)
        for i, p in enumerate(self.players):
            p.after, p.team_size = None, None
            tag = "ME:" if teacher == "fair" else "TEAM:"
            if teacher == "fair":
                p.lines = []                          # the fair notebook starts empty, then the briefing
                if not auto_notes and briefing == "size":
                    p.pending_me = f"ME: I am one of {n} ants"     # told once; it is its to write down
                    continue
            if briefing == "team" and p._paper(f"{tag} {n - i - 1} ants come in after me"):
                p.after = n - i - 1
            elif briefing == "size" and p._paper(f"{tag} I am one of {n} ants"):
                p.team_size = n

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
        return self.briefing == "none" or bool(a.after) or (a.team_size or 0) > 1

    # ---- what the ant sees ----------------------------------------------------------
    def screen_human(self):
        """What a human player gets: the room, its walls and notes, its doors, company, and the player's own
        notebook (here: the lines that mention this room, plus page-1 notes about itself)."""
        a, m = self.cur, self.m
        cur, names = a.pos, m.names
        doors = sorted((names[x] for x in m.adj[cur]), key=lambda d: (d.lower(), d))
        lines = [f"Room: {names[cur]}", f"Walls: {self.surfaces[cur].replace('_', ' ')}"]
        options = {"map": ("map",)}
        for i, d in enumerate(doors, 1):
            lines.append(f"{i} {d}")
            options[str(i)] = ("go", d)
        if self.marks.get(cur):
            lines.append("On the walls: " + " | ".join(self.marks[cur]))
        mine = [l for l in a.lines if l.startswith(("TEAM:", "WALLS:"))]
        about = [l for l in a.lines if names[cur] in l.replace(";", " ;").split(" | ")[0] or f" {names[cur]}" in l
                 or l.endswith(names[cur])][-8:]
        if mine:
            lines.append("Your notes about yourself: " + " | ".join(mine))
        lines.append("Your notes about this room: " + (" | ".join(about) if about else "none"))
        if a.trailing and a.route and cur in a.route:
            k = a.route.index(cur)
            note = f"out {names[a.route[k + 1]]}"
            if (len(m.adj[cur]) >= 3 or cur == m.start) and note not in self.marks.get(cur, []) and cur not in a.wrote:
                lines.append(f"W - write on the wall: {note}")
                options["W"] = ("write", note)
        if a.memory and len(a.recent) > 1:
            lines.append("You remember: " + ", ".join(names[x] for x in a.recent[:-1]) + f", then here ({names[cur]})")
        if a.notebook and m.goal in m.adj[cur] and not a.marked and not a.trailing and (a.team_size or a.after):
            lines.append("B - go back and mark the way")
            options["B"] = ("mark",)
        lines.append(V.last_text(a.last))
        return "\n".join(lines), options

    def screen_fair(self):
        """Exactly what house.py shows a person with --notebook --memory N --notes-here --auto-notes, and the
        commands a person would type. Options map the typed command to the action."""
        a, m = self.cur, self.m
        cur, names = a.pos, m.names
        doors = sorted((names[x] for x in m.adj[cur]), key=lambda d: (d.lower(), d))
        view = {"room": names[cur], "doors": doors, "walls": self.surfaces[cur], "marks": self.marks.get(cur, [])}
        if a.pending_me:                              # told once, as it comes in
            view["messages"] = ["You are told: " + a.pending_me[4:].replace("I am", "you are") + "."]
        recent = [names[x] for x in a.recent[:-1]] if a.memory else ()
        pages = [" 1| " + l for l in a.lines]
        notes = H.notes_about(pages, names[cur]) if a.notebook else None
        me = H.notes_about_me(pages) if a.notebook else ()
        text = H.screen(view, recent, notes, me).rstrip("> ").rstrip()
        options = {"look": ("map",)}
        for d in doors:
            options[d] = ("go", d)
        if a.pending_me:
            options[f"write {a.pending_me}"] = ("note", a.pending_me)
        if a.pending and a.pending[1] == cur:
            line = f"{names[a.pending[0]]} -> {names[cur]}"
            if line not in a.lines:
                options[f"write {line}"] = ("note", line)
        if a.trailing and a.route and cur in a.route:
            k = a.route.index(cur)
            note = f"out {names[a.route[k + 1]]}"
            if (len(m.adj[cur]) >= 3 or cur == m.start) and note not in self.marks.get(cur, []) and cur not in a.wrote:
                options[f"write on wall {note}"] = ("write", note)
        if a.notebook and m.goal in m.adj[cur] and not a.marked and not a.trailing and (a.team_size or a.after):
            route = a.chain()
            if route:
                options["write ROUTE: " + " > ".join(names[x] for x in route + [m.goal])] = ("mark",)
        return text, options

    def screen(self):
        if self.teacher == "fair":
            return self.screen_fair()
        if self.teacher in ("human", "fair"):
            return self.screen_human()
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
            # tagged: junctions and the start. strict: every room, so the marker can walk out by its own notes
            spot = self.teacher == "strict" or len(m.adj[cur]) >= 3 or cur == m.start
            if spot and note not in self.marks.get(cur, []) \
                    and cur not in a.wrote:                      # one try per room
                write = note
        hints = {}
        if self.teacher == "strictR" and a.route_note and not a.trailing:
            k = a.route_note.index(cur) if cur in a.route_note else None
            if k is not None and k + 1 < len(a.route_note) - 1:
                hints[names[a.route_note[k + 1]]] = "your ROUTE note goes this way"   # read back from its notebook
        elif self.teacher.startswith("strict"):
            pass                                   # nothing worked out for it: the screen as it is
        elif a.trailing and a.route and cur in a.route and cur != m.start:
            hints[names[a.route[a.route.index(cur) - 1]]] = "back toward the start (marking the way)"
        elif a.route and not a.trailing:
            p = a.path(m.goal, a.route)
            if p and len(p) > 1 and p[1] != m.goal:
                hints[names[p[1]]] = "toward the Way Out (your route)"
        elif a.following:
            ahead = [x for x in m.adj[cur] if x != a.prev and (cur, x) not in a.tried]
            if len(m.adj[cur]) == 2 and ahead:
                hints[names[ahead[0]]] = "on along the trail"
        if self.teacher != "strict" and not hints and not a.fresh(cur):
            p = a.path(None, frontier=True)
            if p and len(p) > 1:
                hints[names[p[1]]] = "toward rooms with untried paths"
        text, options = V.view(names[cur], doors, untried, came, open_, a.last, walls=self.surfaces[cur],
                               marks=self.marks.get(cur, []), write=write, hints=hints)
        extra = []
        if a.bad:
            extra.append("Your notes: notes on " + ", ".join(sorted(s.replace("_", " ") for s in a.bad))
                         + " walls did not come out")
        if a.after:
            extra.append(f"Your notes: {a.after} ants come in after you")
        if a.team_size:
            extra.append(f"Your notes: you are one of {a.team_size} ants")
        if self.offer_mark():
            back = self.back_len(a)
            extra.append(f"B - go back and mark the way for the ants still to come "
                         f"(the way back to the start: {back} rooms; you took {a.moves} moves to get here)")
            options["B"] = ("mark",)
        if a.trailing and a.route and cur in a.route and cur != m.start:
            extra.append("Marking the way back to the start")
        if extra:
            lines = text.split("\n")
            text = "\n".join(lines[:-2] + extra + lines[-2:])
        return text, options

    def back_len(self, a):
        if self.teacher.startswith("strict"):
            c = a.chain()
            return len(c) - 1 if c else len(a.path(self.m.start) or [a.pos]) - 1
        return len(a.path(self.m.start) or [a.pos]) - 1

    # ---- the lesson bot --------------------------------------------------------------
    def teach(self):
        """(key, kind) the teacher answers for the current screen."""
        a, m = self.cur, self.m
        text, options = self.screen()
        by_door = {v[1]: k for k, v in options.items() if v[0] == "go"}
        names, cur = m.names, a.pos
        if self.teacher == "fair":
            for k, v in options.items():            # write the briefing, then this move, before going on
                if v[0] == "note" and v[1].startswith("ME:"):
                    return k, "note"
            for k, v in options.items():
                if v[0] == "note":
                    return k, "note"
            for k, v in list(options.items()):
                if v[0] == "write":
                    options.setdefault("W", v)
                elif v[0] == "mark":
                    options.setdefault("B", v)
            key, kind = self.teach_strict(text, options, by_door)
            if key in ("W", "B"):
                key = next(k for k, v in options.items() if v == options[key] and k not in ("W", "B"))
            return key, kind
        if self.teacher.startswith("strict") or self.teacher in ("human", "fair"):
            return self.teach_strict(text, options, by_door)
        if "B" in options:
            back = len(a.path(m.start) or [cur]) - 1
            followers = a.after if a.after is not None else \
                (a.team_size - 1) / 2 if a.team_size else 1             # size only: half may still come
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

    def teach_strict(self, text, options, by_door):
        a, m = self.cur, self.m
        names, cur = m.names, a.pos
        if "B" in options and self.teacher in ("human", "fair"):
            followers = (a.team_size - 1) / 2 if a.team_size else (a.after or 0)
            if followers * a.moves > 3 * len(a.chain() or [0]) + 2:
                return "B", "mark"
            return by_door[names[m.goal]], "goal"
        if "B" in options:
            back = self.back_len(a)
            followers = a.after if a.after is not None else \
                (a.team_size - 1) / 2 if a.team_size else 1
            if followers * (a.moves - back) > 2 * back + 2:
                return "B", "mark"
            return by_door[names[m.goal]], "goal"
        if a.trailing:
            if "W" in options and self.surfaces[cur] not in a.bad and self.surfaces[cur] in GOOD_WALLS:
                return "W", "write_out"                         # rough or wet walls: walk on, save the pen
            if cur in a.came:                                   # back the way it first came in
                return by_door[names[a.came[cur]]], "back_trail"
        if names[m.goal] in by_door:
            return by_door[names[m.goal]], "goal"
        if self.teacher in ("human", "fair") and a.route_note and not a.trailing and cur in a.route_note:
            k = a.route_note.index(cur)
            if k + 1 < len(a.route_note) - 1:
                return by_door[names[a.route_note[k + 1]]], "route"
        for d, k in by_door.items():
            if "your ROUTE note goes this way" in next((l for l in text.split("\n") if l.startswith(k + " ")), ""):
                return k, "route"
        notes = V.read_notes(self.marks.get(cur, []), list(by_door))
        for d, kinds in sorted(notes.items()):
            if "out" in kinds and m.find(d) != a.prev and (cur, m.find(d)) not in a.followed:
                return by_door[d], "follow"          # each note followed once, so two trails can't loop
        if self.teacher == "strictC" and a.last and a.last[0] == "trail" and len(m.adj[cur]) == 2:
            ahead = [x for x in m.adj[cur] if x != a.prev]
            if ahead:
                return by_door[names[ahead[0]]], "follow_on"     # "Last action: followed the trail here"
        if self.teacher in ("human", "fair") and a.loop_back and a.prev is not None and a.prev in m.adj[cur]:
            a_prev = a.prev                         # Tremaux: a new door led somewhere I'd been, so turn back
            return by_door[names[a_prev]], "back_loop"
        if self.teacher in ("human", "fair"):                 # only this room's notes: an untried door may lead somewhere known
            fresh = [x for x in m.adj[cur] if (cur, x) not in a.tried]
        else:
            fresh = a.fresh(cur)
        if fresh:
            order = sorted(fresh, key=lambda x: (names[x].lower(), names[x]))
            pi = P.NAMES.index(a.persona) if a.persona in P.NAMES else 0
            pick = order[-1] if pi == 0 else order[0] if pi == 1 else \
                random.Random(f"{a.seed}:{a.moves}:{cur}").choice(order)
            return by_door[names[pick]], "new_more" if len(fresh) > 1 else "new"
        if cur in a.came:
            return by_door[names[a.came[cur]]], "back"
        loop = [x for x in m.adj[cur] if (cur, x) not in a.tried]          # "untried, leads to a known room"
        if loop:
            return by_door[names[sorted(loop, key=lambda x: names[x])[0]]], "lost"
        return by_door[names[random.Random(f"{a.seed}:{a.moves}:{cur}").choice(m.adj[cur])]], "lost"

    # ---- doing it --------------------------------------------------------------------
    def apply(self, action):
        a, m = self.cur, self.m
        self.turns += 1
        if action is None or action[0] == "map":
            a.last = ("map",)
            return
        if action[0] == "mark":
            c = a.chain() if self.teacher.startswith("strict") or self.teacher in ("human", "fair") else None
            a.route = (c if c else (a.path(m.start) or [a.pos])[::-1]) + [m.goal]
            if self.teacher in ("strictR", "human", "fair") and a._paper("ROUTE: " + " > ".join(m.names[x] for x in a.route)):
                a.route_note = list(a.route)
            a.trailing = True
            a.last = ("mark",)
            return
        if action[0] == "note":                       # writing in its own notebook: no move, its choice
            text = action[1]
            ok = a._paper(text)
            if text.startswith("ME:"):
                a.pending_me = None
                if ok:
                    a.team_size = int(re.search(r"one of (\d+)", text).group(1))
            elif a.pending:
                frm, to = a.pending
                a.pending = None
                if ok and to not in a.doors:
                    a.doors[to] = list(m.adj[to])
                a._note_known(to, frm, ok)
            return
        if action[0] == "write":
            text = action[1]
            surf = self.surfaces[a.pos]
            line, _ = a.pen.render(text, surf, a.seed)
            a.wrote.add(a.pos)
            a.moves += 1
            a.writes += 1
            if line:
                self.marks.setdefault(a.pos, []).append(line)
            if (not readable(line, text) or LOST in line) and \
                    a._paper(("ME: " if self.teacher == "fair" else "WALLS: ") + f"{surf.replace('_', ' ')} walls did not take my note"):
                a.bad.add(surf)                      # remembered only if that notebook line came out
            a.last = ("wrote", line)
            return
        to = m.find(action[1])
        if to not in m.adj[a.pos]:
            a.last = ("invalid",)
            return
        frm = a.pos
        followed = any("out" in k for d, k in V.read_notes(self.marks.get(frm, []), [action[1]]).items())
        if followed:
            a.followed.add((frm, to))
        on_trail = followed or (self.teacher == "strictC" and a.last and a.last[0] == "trail"
                                and len(m.adj[frm]) == 2)
        a.following = followed or (a.following and len(m.adj[frm]) == 2)
        left_new = bool(a.fresh(frm)) and to in a.visited
        a.loop_back = (to in a.visited) and ((frm, to) not in a.tried) and not followed
        a.prev, a.pos = frm, to
        a.moves += 1
        a.last = ("trail", m.names[frm]) if self.teacher == "strictC" and on_trail else ("walk", m.names[frm], left_new)
        if to == m.goal:
            a.out = True
            self.k += 1
            return
        a._note(to, frm)
        a.remember(to)
        if a.trailing and (frm == m.start or (a.route and to not in a.route)):
            a.trailing, a.marked = False, True        # marked as far as the start room: now out

    def result(self):
        ps = self.players
        return {"ants": len(ps), "all_out": all(p.out for p in ps), "total_moves": sum(p.moves for p in ps),
                "moves": [p.moves for p in ps], "writes": sum(p.writes for p in ps), "shortest": self.m.shortest,
                "rooms": self.m.n}
