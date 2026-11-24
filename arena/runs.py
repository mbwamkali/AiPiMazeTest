"""Runs: one contestant walking one maze under one rulebook, stored on disk.

Each run is a folder <data>/runs/<run_id>/ with
  run.json      settings, rulebook and its fingerprint, token hash
  events.jsonl  append-only: start, walk, wall, say, edit, refused, message, harness, device, end
The state (position, visits, the maze with its edits) is rebuilt by replaying events.jsonl,
so a keeper restart picks up runs where they were.
"""
import hashlib
import json
import os
import secrets
import threading
import time

from hypermaze.core import Maze
from . import rulebook as rules
from .notebook import Notebook
from .pen import PAPER, Pen, assign_surfaces
from .team import MAX_SAY, Team
from .world import World, EditRefused

DEFAULT_FAREWELL = "The lights go out. The game is over."
MAX_WALL = 120          # letters in one line written on a wall
MAX_MARKS = 20          # lines a room's walls show (the oldest are written over)
WALL_REPLY = "You write on the wall."   # the same for every wall and every pen


def _hash_token(tok):
    return hashlib.sha256(tok.encode()).hexdigest()


class Run:
    def __init__(self, run_dir, settings, rb, token_hash):
        self.dir = run_dir
        self.id = os.path.basename(run_dir)
        self.settings = settings
        self.rulebook = rb
        self.fingerprint = rules.fingerprint(rb)
        self.rules = rules.complete(rb)   # rb plus any settings it predates, at their defaults
        self.token_hash = token_hash
        maze = Maze(settings["dims"], settings["size"], settings["loops"], settings["seed"], rb["generator"])
        self.world = World(maze)
        self.cur = self.world.start
        self.moves = 0
        self.visits = [0] * maze.n
        self.visits[self.cur] = 1
        self.seen = {self.cur}
        self.path = [self.cur]
        self.best = self.world.dist[self.cur]
        self.edits_used = 0
        self.last_edit_turn = None
        self.pending = []           # in-world messages for the next screen
        self.ended = None           # None | way_out | exhausted | stopped
        self.farewell = None
        r = self.rules
        self.surfaces = assign_surfaces(maze.n, settings["seed"], r["surfaces"])   # wall material per room
        self.pen = Pen(r["pen_ink"], r["wall_ink_per_letter"], r["wear"])       # hidden: ink and tip
        self.pen_seed = f"{settings['seed']}:{self.id}"
        self.marks = {}             # room -> lines on its walls, as they came out (the team's, in a team)
        self.team = None            # the Team this run plays in, if its match is a team
        self.writes = 0             # lines written on walls
        self._pen_log = None        # the last notebook line's pen numbers, for the device event
        self.notebook = Notebook(render=self._on_paper)  # the player's notebook: only what the player writes
        self.events = []
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self._file = None
        self._replaying = False

    # ---- events ---------------------------------------------------------------------
    def _emit(self, ev, write=True):
        if self._replaying:  # rebuilding state from events.jsonl: the events are already there
            return ev
        with self.cond:
            return self._emit_locked(ev, write)

    def _emit_locked(self, ev, write):
        ev.setdefault("t_wall", round(time.time(), 3))
        ev["seq"] = len(self.events)
        self.events.append(ev)
        if write:
            if self._file is None:
                self._file = open(os.path.join(self.dir, "events.jsonl"), "a", encoding="utf-8")
            self._file.write(json.dumps(ev) + "\n")
            self._file.flush()
            os.fsync(self._file.fileno())
        self.cond.notify_all()
        return ev

    # ---- contestant -----------------------------------------------------------------
    def room_view(self):
        """What the player is told: this room's name and its doors, nothing about the house's shape.
        Doors come in alphabetical order so their order gives no direction away."""
        with self.lock:
            w = self.world.view(self.cur)
            v = {"room": w["room"], "doors": sorted(w["doors"], key=lambda d: (d.lower(), d)),
                 "walls": self.surfaces[self.cur], "marks": list(self.marks.get(self.cur, []))}
            if self.team and self.team.mode == "together":
                v["here"] = [self.team.name_of(r) for r in self.team.others_here(self)]
            v.update(turn=self.moves, messages=self.pending, ended=self.ended)
            if self.ended:
                v.update(self._end_info())
            self.pending = []
            return v

    def _end_info(self):
        info = {"moves": self.moves}
        if self.ended == "stopped":
            info["farewell"] = self.farewell
        return info

    def walk(self, to_name, write=True):
        """Move through the door to the named room. Returns the new view, or raises LookupError('no_door')."""
        with self.lock:
            if self.ended:
                return self.room_view()
            w = self.world
            to = next((x for x in w.adj[self.cur] if w.names[x] == to_name), None)
            if to is None:
                raise LookupError("no_door")
            frm = self.cur
            self.cur = to
            self.moves += 1
            self.visits[to] += 1
            self.seen.add(to)
            self.path.append(to)
            self.best = min(self.best, w.dist[to])
            self._emit({"t": "walk", "n": self.moves, "from": frm, "to": to, "to_name": w.names[to],
                        "coord": list(w.m.coord(to)), "dist": w.dist[to], "visits": self.visits[to],
                        "result": "revisit" if self.visits[to] > 1 else "new"}, write)
            if to == w.goal:
                self._end("way_out", write)
            elif self.rulebook["max_moves"] and self.moves >= self.rulebook["max_moves"]:
                self._end("exhausted", write)
            return self.room_view()

    def _end(self, why, write=True, farewell=None):
        self.ended = why
        self.farewell = farewell
        self._emit({"t": "end", "result": why, "moves": self.moves, "shortest": self.settings["shortest"],
                    "closest": self.best, "farewell": farewell}, write)

    def stop(self, farewell=None, write=True):
        with self.lock:
            if not self.ended:
                self._end("stopped", write, farewell or DEFAULT_FAREWELL)

    # ---- the pen ----------------------------------------------------------------------
    def _on_paper(self, part):
        line, log = self.pen.render(part, PAPER, self.pen_seed)
        if self._pen_log is not None:
            self._pen_log.append(log)
        return line

    def wall_write(self, text, write=True):
        """Write one line on this room's walls. Costs a turn. The reply is always the same; the room view
        then shows the line as it actually came out (or doesn't, if the wall didn't take it)."""
        with self.lock:
            if self.ended:
                return dict(self.room_view(), reply="")
            text = " ".join(str(text).split())[:MAX_WALL]
            if not text:
                return dict(self.room_view(), reply="You hold the pen to the wall, but write nothing.")
            surface = self.surfaces[self.cur]
            line, log = self.pen.render(text, surface, self.pen_seed)
            if line:
                with (self.team.lock if self.team else self.lock):     # a team shares its walls
                    ms = self.marks.setdefault(self.cur, [])
                    ms.append(line)
                    del ms[:-MAX_MARKS]
            self.moves += 1
            self.writes += 1
            self._emit(dict({"t": "wall", "n": self.moves, "room": self.cur, "room_name": self.world.names[self.cur]},
                            **log), write)
            if self.rulebook.get("max_moves") and self.moves >= self.rulebook["max_moves"]:
                self._end("exhausted", write)
            return dict(self.room_view(), reply=WALL_REPLY)

    # ---- talking (a team playing together: only the ants in the same room hear) -----------
    def say(self, text, write=True):
        """Say one line. Costs nothing; the ants standing here now hear it on their next screen."""
        text = " ".join(str(text).split())[:MAX_SAY]
        with self.lock:
            if self.ended or not text:
                return dict(self.room_view(), reply="You say nothing.")
            hearers = self.team.others_here(self) if self.team else []
            me = self.team.name_of(self) if self.team else ""
            self._emit({"t": "say", "turn": self.moves, "room": self.cur, "text": text,
                        "heard_by": [self.team.name_of(r) for r in hearers]}, write)
        for r in hearers:                       # deliver outside our own lock: no two runs lock each other
            with r.lock:
                r.pending.append(f'{me} says: "{text}"')
        with self.lock:
            return dict(self.room_view(), reply=f'You say: "{text}"' if hearers else "Nobody here hears you.")

    # ---- edits ----------------------------------------------------------------------
    def try_edit(self, edit, by="director", why="", apply=True, write=True):
        """Check (and apply) an edit. Returns a result dict: status applied | refused | ok (preview)."""
        with self.lock:
            try:
                if self.ended:
                    raise EditRefused("the run has ended")
                plan = self.world.plan(edit, self.cur)
                reason = rules.check(self.rulebook, plan, self, by)
            except EditRefused as e:
                plan, reason = None, str(e)
            seen_hit = bool(plan) and (any(a in self.seen or b in self.seen for a, b in plan.doors_touched)
                                       or any(c in self.seen or any(x in self.seen for x in self.world.adj[c])
                                              for c in plan.renamed))
            if reason:
                if apply:
                    self._emit({"t": "refused", "edit": edit, "by": by, "why": why, "reason": reason}, write)
                return {"status": "refused", "reason": reason}
            before = self.world.dist[self.cur]
            if not apply:
                after = self.world._bfs(self.world.goal, plan.adj)[self.cur]
                return {"status": "ok", "summary": plan.summary, "dist_now": before, "dist_after": after,
                        "touches_seen": seen_hit}
            self.world.commit(plan)
            self.edits_used += 1
            self.last_edit_turn = self.moves
            if edit["type"] == "message":
                self.pending.append(str(edit["text"]).strip())
            self._emit({"t": "edit", "edit": edit, "by": by, "why": why, "summary": plan.summary,
                        "map_version": self.world.version, "touches_seen": seen_hit,
                        "dist_before": before, "dist_after": self.world.dist[self.cur]}, write)
            return {"status": "applied", "summary": plan.summary, "map_version": self.world.version,
                    "touches_seen": seen_hit}

    # ---- devices (the notebook) ------------------------------------------------------
    def device_view(self, name, page=None):
        """The device's screen. Reading changes nothing, so it is not an event."""
        if name != "notebook":
            raise LookupError("no_device")
        with self.lock:
            return {"screen": self.notebook.view(page)}

    def device_input(self, name, line, write=True):
        """One line typed into the device. Logged (so the Colosseum can watch), never costs a move."""
        if name != "notebook":
            raise LookupError("no_device")
        with self.lock:
            self._pen_log = []
            try:
                reply = self.notebook.input(line)
            finally:
                pen, self._pen_log = self._pen_log, None
            ev = {"t": "device", "device": name, "input": str(line), "reply": reply,
                  "page": self.notebook.open_page + 1, "screen": self.notebook.view(), "turn": self.moves}
            if pen:
                ev["pen"] = pen      # for the Colosseum only: what was meant, what came out, ink and tip
            self._emit(ev, write)
            return {"reply": reply, "screen": self.notebook.view()}

    # ---- harness --------------------------------------------------------------------
    def harness(self, data, write=True):
        with self.lock:
            keep = {k: data[k] for k in ("turn", "sent", "reply", "where", "screen", "speed") if k in data}
            return self._emit(dict(t="harness", **keep), write)

    def map_dict(self):
        """The whole maze as it stands (World.to_dict) plus each room's walls and what is written on them."""
        with self.lock:
            d = self.world.to_dict()
            for r in d["rooms"]:
                r["walls"] = self.surfaces[r["id"]]
                if self.marks.get(r["id"]):
                    r["marks"] = list(self.marks[r["id"]])
            d["surfaces"] = self.rules["surfaces"]
            return d

    # ---- summaries ------------------------------------------------------------------
    def state(self):
        with self.lock:
            w = self.world
            return {"run_id": self.id, "cur": self.cur, "cur_name": w.names[self.cur], "coord": list(w.m.coord(self.cur)),
                    "dist": w.dist[self.cur], "moves": self.moves, "shortest": self.settings["shortest"],
                    "closest": self.best, "seen": sorted(self.seen), "path": self.path,
                    "revisits": sum(v - 1 for v in self.visits if v > 1), "edits_used": self.edits_used,
                    "map_version": w.version, "ended": self.ended, "walls": self.surfaces[self.cur],
                    "pen": self.pen.state(), "wall_writes": self.writes,
                    "team": self.team.id if self.team else None, "rulebook": self.rulebook,
                    "fingerprint": self.fingerprint, "settings": self.settings, "events": len(self.events)}


class Store:
    """All runs under <data>/runs, plus the token -> run lookup."""

    def __init__(self, data_dir):
        self.root = os.path.join(data_dir, "runs")
        os.makedirs(self.root, mode=0o700, exist_ok=True)
        self.runs = {}
        self.teams = {}             # match id -> Team
        self.lock = threading.Lock()
        for rid in sorted(os.listdir(self.root)):
            try:
                self.runs[rid] = self._load(os.path.join(self.root, rid))
            except (OSError, ValueError, KeyError) as e:
                print(f"skipping run {rid}: {e}")
        for run in sorted(self.runs.values(), key=lambda r: (r.settings.get("match", ""), r.settings.get("seat", 0), r.id)):
            self._join_team(run)
        for team in self.teams.values():
            team.rebuild_marks(MAX_MARKS)

    def _join_team(self, run):
        mode, match = run.rules.get("team", "solo"), run.settings.get("match")
        if mode == "solo" or not match:
            return
        team = self.teams.get(match)
        if team is None:
            team = self.teams[match] = Team(match, mode)
        team.add(run)

    def _load(self, d):
        with open(os.path.join(d, "run.json")) as f:
            meta = json.load(f)
        run = Run(d, meta["settings"], meta["rulebook"], meta["token_hash"])
        path = os.path.join(d, "events.jsonl")
        events = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                events = [json.loads(line) for line in f if line.strip()]
        run._replaying = True
        for ev in events:
            t = ev["t"]
            if t == "walk":
                run.walk(run.world.names[ev["to"]])
            elif t == "edit":
                run.try_edit(ev["edit"], ev.get("by", "director"), ev.get("why", ""))
            elif t == "device":
                run.device_input(ev["device"], ev["input"])
            elif t == "wall":
                run.wall_write(ev["intended"])
            # "say" changes nothing in the run itself (what others heard was shown then)
            elif t == "end" and not run.ended:
                run.stop(ev.get("farewell"))
                run.ended = ev["result"]
        run.pending = []  # messages from before the restart were already shown or are stale
        run._replaying = False
        for i, ev in enumerate(events):
            ev["seq"] = i
        run.events = events
        return run

    def create(self, dims=2, size=8, loops=0.0, seed=None, rulebook=None, labels=None):
        rb = rules.make(rulebook)
        if seed is None:
            seed = secrets.randbits(32)
        maze = Maze(int(dims), int(size), float(loops), int(seed), rb["generator"])
        settings = {"dims": maze.dims, "size": maze.size, "loops": maze.loops, "seed": maze.seed,
                    "rooms": maze.n, "shortest": maze.shortest, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        for k in ("match", "agent"):            # who and which match, for the Colosseum; never shown to the player
            if labels and labels.get(k):
                settings[k] = str(labels[k])
        if labels and isinstance(labels.get("seat"), int):
            settings["seat"] = labels["seat"]   # its place in the match (the relay order)
        token = secrets.token_urlsafe(24)
        with self.lock:
            rid = time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + secrets.token_hex(2)
            d = os.path.join(self.root, rid)
            os.makedirs(d, mode=0o700)
            with open(os.path.join(d, "run.json"), "w") as f:
                json.dump({"settings": settings, "rulebook": rb, "fingerprint": rules.fingerprint(rb),
                           "token_hash": _hash_token(token)}, f, indent=1)
            run = Run(d, settings, rb, _hash_token(token))
            run._emit({"t": "start", "settings": settings, "rulebook": rb, "fingerprint": run.fingerprint,
                       "start": run.world.start, "goal": run.world.goal})
            self.runs[rid] = run
            self._join_team(run)
        return run, token

    def by_token(self, token):
        h = _hash_token(token)
        for run in self.runs.values():
            if secrets.compare_digest(run.token_hash, h):
                return run
        return None
