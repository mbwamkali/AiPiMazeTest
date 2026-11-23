"""v2 ant format, built for speed on the Pis.

- The game's notes screen is rewritten as a short numbered door list (parsed from the
  screen text, so it works the same in-process and through the keeper).
- The ant answers with the door NUMBER first ("GO: 2" or "GO: map"), so the move can be
  sent the moment that line arrives.
- In talk mode it adds one in-character line after the GO line only at interesting
  moments (junctions, dead ends, nerves, barging, map checks, the Way Out), plus now and
  then on a dull move for flavour. Dull corridor moves are just "GO: n".

    view, doors = compact.view(screen_text, last)
    move = compact.parse(reply, doors)        # room name, "map", or None
"""
import json
import random
import re

from . import personas as P

TALK2 = (" Answer GO: <door number or map>. Only when something interesting happens, add one short "
         "in-character line after it.")
QUIET2 = " Answer only GO: <door number or map>."

INTERESTING = {"goal", "new_more", "nerves", "return", "barge", "map", "back_loop"}
CHATTY_DULL = 0.08   # share of dull moves that still get a comment

_ROOM = re.compile(r"You are in the (.+?)\.\n")
_PATHS = re.compile(r"Paths: (.*)")
_UNTRIED = re.compile(r"untried from here: (.*?)(?:\. You first came here from (.+?))?\. Other visited rooms with untried paths: (\d+)")


def parse_screen(screen):
    """(room, [door names], {name: 'new'|'visited'} untried, first_came_from, open_elsewhere)."""
    room = _ROOM.search(screen).group(1)
    doors = [d.strip() for d in _PATHS.search(screen).group(1).split(", ") if d.strip()]
    m = _UNTRIED.search(screen)
    untried, came, open_ = {}, None, 0
    if m:
        if m.group(1) != "none":
            for part in m.group(1).split(", "):
                if part.endswith(" (new)"):
                    untried[part[:-6]] = "new"
                elif part.endswith(" (visited)"):
                    untried[part[:-10]] = "visited"
        came = m.group(2)
        open_ = int(m.group(3))
    return room, doors, untried, came, open_


def view(screen, last):
    room, doors, untried, came, open_ = parse_screen(screen)
    lines = [f"Room: {room}"]
    for i, d in enumerate(doors, 1):
        if d == "Way Out":
            tag = "THE WAY OUT"
        elif untried.get(d) == "new":
            tag = "new"
        elif untried.get(d) == "visited":
            tag = "untried, leads to a known room"
        else:
            tag = "done"
        if d == came:
            tag += ", you first came from here"
        lines.append(f"{i} {d} - {tag}")
    lines.append(f"Rooms elsewhere with untried paths: {open_}")
    lines.append(P.last_text(last))
    return "\n".join(lines), doors


def messages(persona, screen, last, quiet):
    text, doors = view(screen, last)
    return [{"role": "system", "content": P.SYSTEM[persona] + (QUIET2 if quiet else TALK2)},
            {"role": "user", "content": text}], doors


def answer(cmd, doors, reason):
    go = "GO: map" if cmd == "map" else f"GO: {doors.index(cmd) + 1}"
    return go if not reason else f"{go}\n{reason}"


def parse(reply, doors):
    """The move from a reply: a room name, 'map', or None if unreadable."""
    for line in reply.splitlines():
        if "GO:" in line:
            v = line.split("GO:", 1)[1].strip().strip("*`'\". ").lower()
            if v.startswith("map"):
                return "map"
            m = re.match(r"(\d+)", v)
            if m and 1 <= int(m.group(1)) <= len(doors):
                return doors[int(m.group(1)) - 1]
            return None
    return None


def wants_comment(kind, ndoors, rng):
    if kind in INTERESTING:
        return True
    if kind == "back" and ndoors == 1:      # a true dead end
        return True
    return rng.random() < CHATTY_DULL


def main(argv=None):
    import argparse
    from hypermaze import Maze
    from .lessons import maze_mix
    ap = argparse.ArgumentParser(description="v2 (fast) lessons for the stooge ants")
    ap.add_argument("--out", required=True)
    ap.add_argument("--mazes", type=int, default=200, help="mazes per ant")
    ap.add_argument("--seed-base", type=int, default=100000)
    ap.add_argument("--quiet-share", type=float, default=0.25)
    ap.add_argument("--wrong", type=float, default=0.05)
    a = ap.parse_args(argv)
    rng = random.Random(a.seed_base)
    n = talk = 0
    with open(a.out, "w") as f:
        for p in P.NAMES:
            for i in range(a.mazes):
                dims, size, loops, gen = maze_mix(rng)
                maze = Maze(dims, size, loops, seed=a.seed_base + i, generator=gen)
                for screen, last, cmd, reason, kind in P.play(p, maze, rng, a.wrong):
                    quiet = rng.random() < a.quiet_share
                    msgs, doors = messages(p, screen, last, quiet)
                    say = None if quiet or not wants_comment(kind, len(doors), rng) else reason
                    msgs.append({"role": "assistant", "content": answer(cmd, doors, say)})
                    f.write(json.dumps({"messages": msgs}) + "\n")
                    n += 1
                    talk += say is not None
    print(f"{n} lessons, {talk} with a comment ({talk / max(1, n):.0%}) -> {a.out}")


if __name__ == "__main__":
    main()
