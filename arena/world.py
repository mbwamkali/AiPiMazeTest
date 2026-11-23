"""The live maze the keeper holds: the generated maze plus every edit made since.

Rooms are grid cells (numbered as in hypermaze.core). A room's name is what a player
knows it by; doors join grid neighbours. Edits change names and doors, never the grid.

Edits (each a dict with "type"):
  remove_door  {a, b}                    close the passage a-b
  add_door     {a, b}                    open a passage between grid neighbours a and b
  swap_rooms   {a, b}                    the two rooms trade names (so a familiar door leads elsewhere)
  shift_block  {lo, hi, axis, step}      inside the box lo..hi (coordinates, inclusive) every name
                                         moves one cell along axis (step +1 or -1), wrapping round
  rename_room  {room, name}              a new name
  message      {text}                    an in-world line on the player's next screen (no map change)
Rooms may be given by name or by number.

World.plan(edit, cur) checks the invariants and returns a Plan without changing anything;
World.commit(plan) applies it. Policy limits (budget, seen rooms...) live in rulebook.py.
"""
from collections import deque

from hypermaze.core import GOAL_NAME

EDIT_TYPES = ("remove_door", "add_door", "swap_rooms", "shift_block", "rename_room", "message")
MAX_NAME = 40


class EditRefused(ValueError):
    """An edit that breaks an invariant. str() is the reason."""


class Plan:
    def __init__(self, edit, names, adj, doors_touched, renamed, summary):
        self.edit = edit
        self.names = names            # names after the edit
        self.adj = adj                # doors after the edit
        self.doors_touched = doors_touched  # set of (a, b) door changes, a < b
        self.renamed = renamed        # set of rooms whose name changes
        self.summary = summary        # one line for logs and the feed


class World:
    def __init__(self, maze):
        self.m = maze
        self.names = list(maze.names)
        self.adj = [list(v) for v in maze.adj]
        self.start, self.goal = maze.start, maze.goal
        self.version = 0
        self._index()

    # ---- reading ----------------------------------------------------------------------
    def _index(self):
        self.by_name = {nm.lower(): i for i, nm in enumerate(self.names)}
        self.dist = self._bfs(self.goal, self.adj)

    def _bfs(self, src, adj):
        d = [-1] * self.m.n
        d[src] = 0
        q = deque([src])
        while q:
            c = q.popleft()
            for x in adj[c]:
                if d[x] < 0:
                    d[x] = d[c] + 1
                    q.append(x)
        return d

    def room(self, ref):
        """Room number from a name or a number."""
        if isinstance(ref, bool):
            raise EditRefused(f"no room {ref!r}")
        if isinstance(ref, int):
            if 0 <= ref < self.m.n:
                return ref
            raise EditRefused(f"no room {ref}")
        c = self.by_name.get(str(ref).strip().lower())
        if c is None:
            raise EditRefused(f"no room called {ref!r}")
        return c

    def view(self, c):
        """What a player standing in room c is told."""
        return {"room": self.names[c], "doors": [self.names[x] for x in self.adj[c]], "way_out": c == self.goal}

    def shortest_path(self, a, b=None):
        b = self.goal if b is None else b
        d = self.dist if b == self.goal else self._bfs(b, self.adj)
        if d[a] < 0:
            return None
        path = [a]
        while path[-1] != b:
            c = path[-1]
            path.append(next(x for x in self.adj[c] if d[x] == d[c] - 1))
        return path

    def move_name(self, a, b):
        axis, d = self.m.axis_of(a, b)
        return "xyzwvutsrq"[axis] + ("+" if d > 0 else "-") if axis < 10 else f"a{axis}{'+' if d > 0 else '-'}"

    def to_dict(self):
        """The whole maze as it stands, in the Python port's map shape plus names and version."""
        m = self.m
        rooms = []
        for c in range(m.n):
            rooms.append({"id": c, "name": self.names[c], "coord": list(m.coord(c)),
                          "exits": list(self.adj[c]), "exit_moves": [self.move_name(c, x) for x in self.adj[c]],
                          "dist": self.dist[c]})
        links = sorted({(min(a, b), max(a, b)) for a in range(m.n) for b in self.adj[a]})
        return {"format": "hypermaze-maze", "version": 1, "map_version": self.version,
                "dims": m.dims, "size": m.size, "loops": m.loops, "seed": m.seed, "generator": m.generator,
                "rooms_count": m.n, "start": self.start, "goal": self.goal, "shortest": self.dist[self.start],
                "solution": self.shortest_path(self.start), "rooms": rooms,
                "links": [{"a": a, "b": b, "axis": m.axis_of(a, b)[0]} for a, b in links]}

    # ---- editing ----------------------------------------------------------------------
    def plan(self, edit, cur):
        """Check an edit against the invariants. Returns a Plan, or raises EditRefused."""
        if not isinstance(edit, dict):
            raise EditRefused("an edit is an object with a type")
        t = edit.get("type")
        if t not in EDIT_TYPES:
            raise EditRefused(f"unknown edit type {t!r}")
        names, adj = list(self.names), [list(v) for v in self.adj]
        protected = {cur} | set(self.adj[cur])  # the room the player stands in and the doors it shows
        doors, renamed = set(), set()
        try:
            if t in ("remove_door", "add_door"):
                a, b = self.room(edit["a"]), self.room(edit["b"])
                try:
                    self.m.axis_of(a, b)
                except ValueError:
                    raise EditRefused("doors only join rooms that are grid neighbours")
                if cur in (a, b):
                    raise EditRefused("the room the player is standing in can't change")
                if t == "remove_door":
                    if b not in adj[a]:
                        raise EditRefused("there is no door there")
                    adj[a].remove(b)
                    adj[b].remove(a)
                else:
                    if b in adj[a]:
                        raise EditRefused("there is already a door there")
                    adj[a].append(b)
                    adj[b].append(a)
                doors.add((min(a, b), max(a, b)))
                summary = f"{t} {names[a]} <-> {names[b]}"
            elif t == "swap_rooms":
                a, b = self.room(edit["a"]), self.room(edit["b"])
                if a == b:
                    raise EditRefused("swap needs two different rooms")
                names[a], names[b] = names[b], names[a]
                renamed = {a, b}
                summary = f"swap_rooms {self.names[a]} <-> {self.names[b]}"
            elif t == "shift_block":
                lo, hi = [int(v) for v in edit["lo"]], [int(v) for v in edit["hi"]]
                axis, step = int(edit["axis"]), int(edit.get("step", 1))
                dims, size = self.m.dims, self.m.size
                if len(lo) != dims or len(hi) != dims or not (0 <= axis < dims) or step not in (-1, 1):
                    raise EditRefused(f"shift_block needs lo and hi with {dims} coordinates, an axis 0..{dims - 1}, step 1 or -1")
                if any(not (0 <= lo[i] <= hi[i] < size) for i in range(dims)):
                    raise EditRefused("the block must lie inside the maze, lo <= hi")
                span = hi[axis] - lo[axis] + 1
                if span < 2:
                    raise EditRefused("the block must be at least 2 rooms long along the axis")
                cells = [c for c in range(self.m.n) if all(lo[i] <= self.m.coord(c, i) <= hi[i] for i in range(dims))]
                stride = size ** axis
                for c in cells:
                    v = self.m.coord(c, axis)
                    nv = lo[axis] + (v - lo[axis] + step) % span
                    names[c + (nv - v) * stride] = self.names[c]
                renamed = set(cells)
                summary = f"shift_block lo={lo} hi={hi} axis={axis} step={step} ({len(cells)} rooms)"
            elif t == "rename_room":
                c = self.room(edit["room"])
                new = str(edit["name"]).strip()
                if not new or len(new) > MAX_NAME:
                    raise EditRefused(f"a name is 1 to {MAX_NAME} characters")
                if new.lower() in self.by_name and self.by_name[new.lower()] != c:
                    raise EditRefused(f"a room is already called {new}")
                names[c] = new
                renamed = {c}
                summary = f"rename_room {self.names[c]} -> {new}"
            else:  # message
                text = str(edit.get("text", "")).strip()
                if not text:
                    raise EditRefused("a message needs text")
                summary = f"message {text}"
        except (KeyError, TypeError) as e:
            raise EditRefused(f"missing or bad field {e}")

        if renamed:
            if self.goal in renamed:
                raise EditRefused("the Way Out keeps its name and place")
            if renamed & protected:
                raise EditRefused("the room the player is standing in, and the doors it shows, can't change")
            renamed = {c for c in renamed if names[c] != self.names[c]}
            if GOAL_NAME.lower() in {names[c].lower() for c in renamed}:
                raise EditRefused("only the Way Out is called Way Out")
        if doors and min(self._bfs(self.goal, adj)) < 0:
            raise EditRefused("that would cut rooms off from the Way Out")
        return Plan(edit, names, adj, doors, renamed, summary)

    def commit(self, plan):
        self.names, self.adj = plan.names, plan.adj
        if plan.doors_touched or plan.renamed:
            self.version += 1
        self._index()
