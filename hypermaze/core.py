"""The hypermaze itself: an n-dimensional grid maze with named rooms.

Rooms are numbered 0..n-1. Room c sits at coordinates (c0, c1, ...) where
c = c0 + c1*size + c2*size^2 + ... (axis 0 changes fastest), as in maze.cpp.

Generation is bit-exact with maze.cpp: the same dims/size/loops/seed give the
same links, the same exit order, the same names, start and goal.

Everything here is plain data and functions, so the text game, bots, tests and
the Colosseum viewer all share one maze model.
"""
from collections import deque
import json

from .rng import MT19937, shuffle

ADJ = ["Copper", "Amber", "Salt", "Iron", "Velvet", "Hollow", "Silent", "Crimson", "Ashen", "Gilded",
       "Mossy", "Frozen", "Sunken", "Painted", "Broken", "Whispering", "Cobalt", "Ivory", "Rusted", "Lantern",
       "Marble", "Ember", "Dusty", "Glass", "Willow", "Thorn", "Pale", "Echoing", "Jade", "Smoky",
       "Granite", "Coral", "Bitter", "Quiet", "Tilted", "Saffron", "Obsidian", "Brass", "Misty", "Cedar"]
NOUN = ["Hall", "Gallery", "Cellar", "Chapel", "Vault", "Library", "Kitchen", "Stair", "Garden", "Forge",
        "Attic", "Crypt", "Parlor", "Tower", "Cistern", "Workshop", "Pantry", "Observatory", "Armory", "Nursery",
        "Study", "Ballroom", "Bathhouse", "Loft", "Grotto", "Archive", "Larder", "Belfry", "Courtyard", "Cloister",
        "Den", "Foyer", "Mill", "Orchard", "Shrine", "Stable", "Theatre", "Well", "Chamber", "Rookery"]

GOAL_NAME = "Way Out"
FORMAT_VERSION = 1
GENERATORS = ("dfs", "wilson", "prim")  # dfs = randomized depth-first, bit-exact with maze.cpp


class Maze:
    """A generated maze. Build one with Maze(dims, size, loops, seed)."""

    def __init__(self, dims=2, size=8, loops=0.0, seed=1, generator="dfs"):
        if dims < 1 or size < 2:
            raise ValueError("need dims>=1, size>=2")
        if generator not in GENERATORS:
            raise ValueError(f"generator must be one of {GENERATORS}")
        self.dims, self.size, self.loops, self.seed = dims, size, loops, seed & 0xFFFFFFFF
        self.generator = generator
        self.n = size ** dims
        self._stride = [size ** a for a in range(dims)]
        self._build()

    # ---- geometry -------------------------------------------------------
    def coord(self, c, axis=None):
        """Coordinates of room c as a tuple, or one axis of it."""
        if axis is not None:
            return (c // self._stride[axis]) % self.size
        return tuple((c // s) % self.size for s in self._stride)

    def room_at(self, coords):
        """Room number at the given coordinates."""
        return sum(int(v) * s for v, s in zip(coords, self._stride))

    def grid_neighbor(self, c, axis, d):
        """The grid cell one step along axis (d = -1 or +1), walls ignored; -1 if off the edge."""
        v = self.coord(c, axis) + d
        if v < 0 or v >= self.size:
            return -1
        return c + d * self._stride[axis]

    def axis_of(self, a, b):
        """(axis, direction) of the step from room a to grid-adjacent room b."""
        diff = b - a
        for axis, s in enumerate(self._stride):
            if diff == s:
                return axis, 1
            if diff == -s:
                return axis, -1
        raise ValueError(f"rooms {a} and {b} are not grid neighbours")

    def coord_str(self, c):
        return "(" + ",".join(str(v) for v in self.coord(c)) + ")"

    # ---- generation (mirrors Maze::build in maze.cpp) -------------------
    def _build(self):
        n, dims = self.n, self.dims
        adj = [[] for _ in range(n)]
        rng = MT19937(self.seed)
        seen = [False] * n
        seen[0] = True
        stack = [0]
        open_ = set()
        if self.generator == "dfs":
            while stack:  # randomized DFS -> perfect maze
                c = stack[-1]
                cand = []
                for a in range(dims):
                    for d in (-1, 1):
                        x = self.grid_neighbor(c, a, d)
                        if x >= 0 and not seen[x]:
                            cand.append(x)
                if not cand:
                    stack.pop()
                    continue
                x = cand[rng() % len(cand)]
                seen[x] = True
                adj[c].append(x)
                adj[x].append(c)
                open_.add((min(c, x), max(c, x)))
                stack.append(x)
        else:
            carve = self._carve_wilson if self.generator == "wilson" else self._carve_prim
            for c, x in carve(rng):
                adj[c].append(x)
                adj[x].append(c)
                open_.add((min(c, x), max(c, x)))
        if self.loops > 0:  # knock out a share of the remaining interior walls
            walls = []
            for c in range(n):
                for a in range(dims):
                    x = self.grid_neighbor(c, a, 1)
                    if x >= 0 and (c, x) not in open_:
                        walls.append((c, x))
            shuffle(walls, rng)
            k = int(len(walls) * min(1.0, self.loops) + 0.5)
            for c, x in walls[:k]:
                adj[c].append(x)
                adj[x].append(c)
        for v in adj:  # exit order carries no direction hint
            shuffle(v, rng)
        pool = [f"{a} {b}" for a in ADJ for b in NOUN]
        shuffle(pool, rng)
        p = len(pool)
        self.names = [pool[i] if i < p else f"{pool[i % p]} {i // p + 1}" for i in range(n)]
        self.adj = adj
        self.start = rng() % n
        ds = self.bfs(self.start)
        self.goal = ds.index(max(ds))  # first farthest room, like max_element
        self.names[self.goal] = GOAL_NAME
        self.dist = self.bfs(self.goal)  # distance to goal from every room
        self._by_name = {nm.lower(): i for i, nm in enumerate(self.names)}

    def _grid_neighbors(self, c):
        out = []
        for a in range(self.dims):
            for d in (-1, 1):
                x = self.grid_neighbor(c, a, d)
                if x >= 0:
                    out.append(x)
        return out

    def _carve_wilson(self, rng):
        """Wilson's algorithm: a uniformly random spanning tree (loop-erased random walks)."""
        n = self.n
        in_tree = [False] * n
        in_tree[0] = True
        nxt = [-1] * n
        for s in range(n):
            u = s
            while not in_tree[u]:
                nb = self._grid_neighbors(u)
                nxt[u] = nb[rng() % len(nb)]
                u = nxt[u]
            u = s
            while not in_tree[u]:
                in_tree[u] = True
                yield u, nxt[u]
                u = nxt[u]

    def _carve_prim(self, rng):
        """Randomized Prim: grow from room 0, picking a random frontier passage each step."""
        seen = [False] * self.n
        seen[0] = True
        frontier = [(0, x) for x in self._grid_neighbors(0)]
        while frontier:
            k = rng() % len(frontier)
            a, b = frontier[k]
            frontier[k] = frontier[-1]
            frontier.pop()
            if seen[b]:
                continue
            seen[b] = True
            yield a, b
            frontier.extend((b, x) for x in self._grid_neighbors(b) if not seen[x])

    # ---- search ---------------------------------------------------------
    def bfs(self, src):
        """Steps from src to every room (-1 if unreachable; never happens in a built maze)."""
        d = [-1] * self.n
        d[src] = 0
        q = deque([src])
        while q:
            c = q.popleft()
            for x in self.adj[c]:
                if d[x] < 0:
                    d[x] = d[c] + 1
                    q.append(x)
        return d

    def shortest_path(self, a=None, b=None):
        """Rooms on a shortest path from a to b, both ends included.
        Defaults: from the start to the goal."""
        a = self.start if a is None else a
        b = self.goal if b is None else b
        if b == self.goal:  # walk down the precomputed distance field
            d = self.dist
        else:
            d = self.bfs(b)
        path = [a]
        while path[-1] != b:
            c = path[-1]
            path.append(next(x for x in self.adj[c] if d[x] == d[c] - 1))
        return path

    def distance(self, a, b=None):
        """Steps from a to b (default: to the goal)."""
        return self.dist[a] if b is None or b == self.goal else self.bfs(a)[b]

    @property
    def shortest(self):
        return self.dist[self.start]

    def find(self, name):
        """Room number for a room name (case-insensitive), or None."""
        return self._by_name.get(name.strip().lower())

    def links(self):
        """Every open passage once, as (a, b) with a < b."""
        return sorted({(min(a, b), max(a, b)) for a in range(self.n) for b in self.adj[a]})

    def dead_ends(self):
        return [c for c in range(self.n) if len(self.adj[c]) == 1]

    # ---- views for the Colosseum viewer ---------------------------------
    def slice(self, fixed):
        """Rooms and links inside a slice of the maze.
        fixed: {axis: value}, e.g. {3: 1} keeps the 3D cube at w=1.
        Returns {"rooms": [...], "links": [(a, b), ...]} with links only between kept rooms."""
        keep = {c for c in range(self.n) if all(self.coord(c, a) == v for a, v in fixed.items())}
        return {"rooms": sorted(keep),
                "links": [(a, b) for a, b in self.links() if a in keep and b in keep]}

    def to_dict(self, include_dist=True):
        """Everything the viewer needs, as plain JSON-ready data."""
        rooms = []
        for c in range(self.n):
            r = {"id": c, "name": self.names[c], "coord": list(self.coord(c)), "exits": list(self.adj[c])}
            if include_dist:
                r["dist"] = self.dist[c]
            rooms.append(r)
        links = []
        for a, b in self.links():
            axis, _ = self.axis_of(a, b)
            links.append({"a": a, "b": b, "axis": axis})
        return {
            "format": "hypermaze-maze", "version": FORMAT_VERSION,
            "dims": self.dims, "size": self.size, "loops": self.loops, "seed": self.seed,
            "generator": self.generator,
            "rooms_count": self.n, "start": self.start, "goal": self.goal,
            "shortest": self.shortest, "solution": self.shortest_path(),
            "rooms": rooms, "links": links,
        }

    def to_json(self, **kw):
        indent = kw.pop("indent", None)
        return json.dumps(self.to_dict(**kw), indent=indent)

    def header(self):
        """The first line of <run>-maze.log, exactly as maze.cpp writes it."""
        return (f"# dims={self.dims} size={self.size} loops={self.loops:g} seed={self.seed} rooms={self.n}"
                f" start={self.names[self.start]}{self.coord_str(self.start)} goal={self.coord_str(self.goal)}"
                f" shortest={self.shortest}")

    # ---- live changes (used by injections) ------------------------------
    def _refresh(self):
        self.dist = self.bfs(self.goal)
        self._by_name = {nm.lower(): i for i, nm in enumerate(self.names)}

    def add_link(self, a, b):
        """Open a passage between grid-neighbour rooms a and b (appended last in each exit list)."""
        self.axis_of(a, b)  # raises if they aren't grid neighbours
        if b in self.adj[a]:
            return False
        self.adj[a].append(b)
        self.adj[b].append(a)
        self._refresh()
        return True

    def remove_link(self, a, b, keep_connected=True):
        """Close the passage a-b. With keep_connected (default) it refuses if that
        would cut any room off from the goal."""
        if b not in self.adj[a]:
            return False
        self.adj[a].remove(b)
        self.adj[b].remove(a)
        d = self.bfs(self.goal)
        if keep_connected and min(d) < 0:
            self.adj[a].append(b)
            self.adj[b].append(a)
            raise ValueError("closing that passage would cut rooms off from the Way Out")
        self._refresh()
        return True

    def rename(self, c, name):
        if c == self.goal:
            raise ValueError("the Way Out keeps its name")
        if name.lower() in self._by_name:
            raise ValueError(f"a room is already called {name}")
        self.names[c] = name
        self._refresh()
