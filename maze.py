#!/usr/bin/env python3
"""hypermaze-text in pure Python: an n-dimensional maze played as a text adventure.

Drop-in for the C++ ./maze: same options, same screens, same <run>-maze.log, and
the same seed builds the same maze. (Defaults differ: 2D size 8 here, 4D size 3 in the C++.)

Usage: ./maze.py [--dims 2] [--size 8] [--loops 0.0] [--seed 1] [--log path.log] [--max-moves 0]
                 [--notes | --notebook] [--pages 100] [--lines 35] [--width 60]
Extra options (not in the C++ version):
  --moves-log PATH   JSON-lines replay log (default: next to --log, e.g. notes-maze.log -> notes-moves.jsonl;
                     none when --log is /dev/null)
  --no-moves-log     don't write it
  --notes-one-way    old notes behaviour: a path only counts as tried from the room you left
  --inject PATH      follow a JSON-lines inbox and apply what's appended while the game runs
                     (see hypermaze/inject.py; tools/inject.py writes to it)
  --plugin FILE.py   load a plugin (def setup(game)); may be given more than once
Commands: <room name>, look, quit, plus the mode's commands (map / map all; notebook commands).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hypermaze.core import Maze  # noqa: E402
from hypermaze.game import Game  # noqa: E402
from hypermaze.inject import Inbox, load_plugin  # noqa: E402

ENC = "latin-1"  # one char per byte, so lengths and case rules match the C++ exactly


def default_moves_log(log_path):
    if log_path in ("/dev/null", ""):
        return None
    if log_path.endswith("maze.log"):
        return log_path[: -len("maze.log")] + "moves.jsonl"
    return log_path + ".moves.jsonl"


def parse(argv):
    o = dict(dims=2, size=8, loops=0.0, seed=1, log="maze.log", max_moves=0, notes=False, notebook=False,
             pages=100, lines=35, width=60, moves_log=None, inject=None, plugins=[], no_moves_log=False, notes_one_way=False)
    i = 0
    while i < len(argv):
        a = argv[i]

        def nxt():
            nonlocal i
            i += 1
            return argv[i] if i < len(argv) else ""

        try:
            if a == "--dims": o["dims"] = int(nxt())
            elif a == "--size": o["size"] = int(nxt())
            elif a == "--loops": o["loops"] = float(nxt())
            elif a == "--seed": o["seed"] = int(nxt()) & 0xFFFFFFFF
            elif a == "--notes": o["notes"] = True
            elif a == "--notebook": o["notebook"] = True
            elif a == "--log": o["log"] = nxt()
            elif a == "--max-moves": o["max_moves"] = int(nxt())
            elif a == "--pages": o["pages"] = int(nxt())
            elif a == "--lines": o["lines"] = int(nxt())
            elif a == "--width": o["width"] = int(nxt())
            elif a == "--moves-log": o["moves_log"] = nxt()
            elif a == "--no-moves-log": o["no_moves_log"] = True
            elif a == "--notes-one-way": o["notes_one_way"] = True
            elif a == "--inject": o["inject"] = nxt()
            elif a == "--plugin": o["plugins"] = o["plugins"] + [nxt()]
            elif a in ("-h", "--help"):
                sys.stdout.write(__doc__)
                sys.exit(0)
            else:
                sys.stderr.write(f"unknown option {a}\n")
                sys.exit(2)
        except ValueError:
            sys.stderr.write(f"bad value for {a}\n")
            sys.exit(2)
        i += 1
    return o


def main(argv=None):
    o = parse(sys.argv[1:] if argv is None else argv)
    if o["dims"] < 1 or o["size"] < 2:
        sys.stderr.write("need dims>=1, size>=2\n")
        return 2
    if o["notes"] and o["notebook"]:
        mode = "both"
    else:
        mode = "notes" if o["notes"] else "notebook" if o["notebook"] else "none"
    maze = Maze(o["dims"], o["size"], o["loops"], o["seed"])
    log = open(o["log"], "w", encoding=ENC, newline="")
    mpath = None if o["no_moves_log"] else (o["moves_log"] or default_moves_log(o["log"]))
    mlog = open(mpath, "w", encoding="utf-8") if mpath else None
    game = Game(maze, mode=mode, max_moves=o["max_moves"],
                pages=o["pages"], lines=o["lines"], width=o["width"],
                notes_both_ways=not o["notes_one_way"], maze_log=log, moves_log=mlog)

    for path in o["plugins"]:
        load_plugin(game, path)
    inbox = Inbox(o["inject"]) if o["inject"] else None

    out = sys.stdout.buffer
    inp = sys.stdin.buffer

    def say(s):
        if s:
            out.write(s.encode(ENC, "replace"))
            out.flush()

    say(game.intro())
    while not game.done:
        b = inp.readline()
        if not b:
            break
        line = b.decode(ENC)
        if line.endswith("\n"):
            line = line[:-1]
        if inbox:
            inbox.apply_new(game)
            if game.stop_requested:
                say(game.stop(game.stop_requested))
                break
        say(game.send(line))
    game.quit()  # logs QUIT if the game is still open (end of input)
    log.close()
    if mlog:
        mlog.close()
    return 0 if game.result == "SOLVED" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:  # the reader went away (e.g. piped into head)
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(1)
