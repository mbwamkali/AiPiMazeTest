"""v3 lessons: relay games played by the lesson bot (ant/relay3.py), one lesson per move it makes.

Mix: 1 to 3 ants (a lone ant keeps the old skill; later ants learn to follow "out" notes; the first of
several learns to go back and mark the way), wall materials from all wood to hard, a few small pens,
2D to 5D mazes. Now and then the bot takes a random wrong turn that is not taught, so the screens after
it teach getting back on track. Comments only at interesting moments, as in v2.

    python3 -m ant.lessons3 --out lessons-v3.jsonl --games 3000 --seed-base 300000
"""
import argparse
import json
import random

from hypermaze.core import Maze
from . import bots as B
from . import view3 as V
from .lessons import maze_mix
from .relay3 import RelayGame

MIXES = [({}, 4), ({"brick": 0.15, "damp_stone": 0.1, "ice": 0.05}, 3),
         ({"brick": 0.3, "damp_stone": 0.15, "ice": 0.1, "glass": 0.05}, 2), ({"ice": 0.3, "metal": 0.2}, 1)]
EXTRA_SAY = {
    "mark": ["The others are coming. I'll go back and mark the way.", "Found it, but the team needs a trail. Back I go."],
    "write_out": ["A note here for the ones behind me.", "Marking this junction: out that way."],
    "back_trail": ["Back toward the start, marking as I go.", "Retracing my steps for the team."],
    "follow": ["A note says the Way Out is this way. Following it.", "Someone marked this. I'll trust it."],
    "follow_on": [],
    "route": [],
    "lost": ["I've lost track. Trying this way."],
}
INTERESTING = {"goal", "new_more", "mark", "write_out", "follow", "lost"}


def comment(persona, kind, to, rng):
    say = B.SAY.get(persona) or next(iter(B.SAY.values()))
    lines = say.get(kind) or EXTRA_SAY.get(kind) or []
    return rng.choice(lines).format(to=to) if lines else None


def games(n, seed_base, rng, briefing="team"):
    for i in range(n):
        dims, size, loops, gen = maze_mix(rng)
        maze = Maze(dims, size, loops, seed=seed_base + i, generator=gen)
        ants = rng.choice([1, 1, 2, 2, 3, 3, 3])
        mix = rng.choices([m for m, _ in MIXES], [w for _, w in MIXES])[0]
        pen = rng.choice([200000] * 6 + [20000, 5000])
        yield RelayGame(maze, ants=ants, mix=mix, seed=seed_base + i, pen_ink=pen, briefing=briefing)


def main(argv=None):
    ap = argparse.ArgumentParser(description="v3 lessons (walls, trail notes, relay)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--games", type=int, default=3000)
    ap.add_argument("--seed-base", type=int, default=300000)
    ap.add_argument("--quiet-share", type=float, default=0.25)
    ap.add_argument("--wrong", type=float, default=0.04)
    ap.add_argument("--chatty", type=float, default=0.08)
    ap.add_argument("--briefing", choices=["team", "size", "none"], default="team",
                    help="what an ant is told about its team as it comes in (see ant/relay3.py)")
    a = ap.parse_args(argv)
    rng = random.Random(a.seed_base)
    n = talk = 0
    kinds = {}
    with open(a.out, "w") as f:
        for g in games(a.games, a.seed_base, rng, a.briefing):
            while not g.done:
                text, options = g.screen()
                doors = [v for k, v in options.items() if v[0] == "go"]
                if rng.random() < a.wrong and len(doors) > 1 and not g.cur.trailing:
                    g.apply(rng.choice(doors))          # an untaught wrong turn
                    continue
                key, kind = g.teach()
                persona = g.cur.persona
                quiet = rng.random() < a.quiet_share
                to = options[key][1] if options[key][0] in ("go", "write", "say") else ""
                say = None
                if not quiet and (kind in INTERESTING or rng.random() < a.chatty):
                    say = comment(persona, kind, to, rng)
                msgs = V.messages(persona, text, quiet)
                msgs.append({"role": "assistant", "content": V.answer(key, say)})
                f.write(json.dumps({"messages": msgs}) + "\n")
                n += 1
                talk += say is not None
                kinds[kind] = kinds.get(kind, 0) + 1
                g.apply(options[key])
    print(f"{n} lessons ({talk} with a comment) -> {a.out}")
    print(" ", dict(sorted(kinds.items(), key=lambda kv: -kv[1])))


if __name__ == "__main__":
    main()
