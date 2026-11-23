"""Turning a run's logs back into something the viewer can replay.

Reads either log a run leaves:
  - <run>-moves.jsonl  (written by the Python maze.py)
  - <run>-maze.log     (written by the C++ ./maze or the Python one; old runs work too)
Because the same seed rebuilds the same maze, both give the full maze plus the route.

    run = load_run("notes-maze.log")
    run["maze"]   -> Maze object
    run["path"]   -> room numbers stood in, in order (start first)
    run["steps"]  -> one dict per event: move / invalid / note / inject / cmd
    run["end"]    -> {"result": "SOLVED", "moves": ..} or None if the log stops early
    to_viewer(run) -> one JSON-ready dict: {"maze": ..., "replay": ...}
"""
import json
import re

from .core import Maze

_HDR = re.compile(r"# dims=(\d+) size=(\d+) loops=(\S+) seed=(\d+) rooms=(\d+) start=(.*?)\(([\d,]+)\) goal=\(([\d,]+)\) shortest=(\d+)")
_MOVE = re.compile(r"^(\d+)\s+(.+?)\s+(\([\d,]+\))\s+(.+?)\s+(\([\d,]+\))\s+(-?\d+)\s+(\d+)\s+(new|revisit)\s*$")
_END = re.compile(r"^# (SOLVED|GAVE UP|QUIT|STOPPED) (.*)$")


def _parse_kv(s):
    return {k: int(v) for k, v in re.findall(r"(\w+)=(-?\d+)", s)}


def load_maze_log(path):
    with open(path, encoding="latin-1") as f:
        lines = f.read().split("\n")
    h = _HDR.match(lines[0])
    if not h:
        raise ValueError(f"{path}: first line is not a maze.log header")
    dims, size, loops, seed = int(h[1]), int(h[2]), float(h[3]), int(h[4])
    maze = Maze(dims, size, loops, seed)
    if maze.start != maze.room_at(map(int, h[7].split(","))) or maze.shortest != int(h[9]):
        raise ValueError(f"{path}: rebuilt maze does not match the log header")
    cur = maze.start
    path_, steps, end = [cur], [], None
    for ln in lines[2:]:
        if not ln:
            continue
        m = _MOVE.match(ln)
        if m:
            frm = maze.room_at(map(int, m[3][1:-1].split(",")))
            to = maze.room_at(map(int, m[5][1:-1].split(",")))
            steps.append({"t": "move", "n": int(m[1]), "from": frm, "to": to, "dist": int(m[6]),
                          "visits": int(m[7]), "result": m[8]})
            path_.append(to)
            cur = to
            continue
        if ln.endswith("\tINVALID"):
            parts = ln.split("\t")
            steps.append({"t": "invalid", "at": cur, "input": "\t".join(parts[3:-4])})
            continue
        e = _END.match(ln)
        if e:
            end = {"result": e[1], **_parse_kv(e[2])}
            continue
        if ln.startswith("- "):  # notebook or inject line: "-     <room>  <what>"
            what = ln[5 + 1 + 22 + 1 + 12 + 1:] if len(ln) > 42 else ln
            kind = "inject" if what.startswith("INJECT ") else "note"
            steps.append({"t": kind, "at": cur, "what": what})
    return {"maze": maze, "mode": None, "path": path_, "steps": steps, "end": end}


def load_moves_jsonl(path):
    with open(path, encoding="utf-8") as f:
        events = [json.loads(ln) for ln in f if ln.strip()]
    st = events[0]
    if st.get("t") != "start":
        raise ValueError(f"{path}: first line is not a start event")
    maze = Maze(st["dims"], st["size"], st["loops"], st["seed"])
    path_, steps, end = [maze.start], [], None
    for e in events[1:]:
        if e["t"] == "end":
            end = {k: v for k, v in e.items() if k != "t"}
            continue
        if e["t"] == "inject" and e.get("ok"):  # replay live changes to the maze so later moves line up
            inj = e["inject"]
            try:
                if inj["kind"] == "open_link":
                    maze.add_link(_ref(maze, inj["a"]), _ref(maze, inj["b"]))
                elif inj["kind"] == "close_link":
                    maze.remove_link(_ref(maze, inj["a"]), _ref(maze, inj["b"]))
                elif inj["kind"] == "rename":
                    maze.rename(_ref(maze, inj["room"]), inj["name"])
                elif inj["kind"] == "teleport":
                    path_.append(_ref(maze, inj["room"]))
            except (KeyError, ValueError):
                pass
        if e["t"] == "move":
            path_.append(e["to"])
        steps.append(e)
    return {"maze": maze, "mode": st.get("mode"), "path": path_, "steps": steps, "end": end}


def _ref(maze, r):
    return r if isinstance(r, int) else maze.find(r)


def load_run(path):
    """Load a run from either log type (picked by the file's content)."""
    with open(path, encoding="latin-1") as f:
        first = f.readline()
    if first.startswith("{"):
        return load_moves_jsonl(path)
    return load_maze_log(path)


def to_viewer(run):
    """One JSON-ready dict with the maze and the replay, for the Colosseum viewer."""
    maze = run["maze"]
    return {"format": "hypermaze-run", "version": 1, "maze": maze.to_dict(),
            "replay": {"mode": run["mode"], "path": run["path"],
                       "path_coords": [list(maze.coord(c)) for c in run["path"]],
                       "steps": run["steps"], "end": run["end"]}}
