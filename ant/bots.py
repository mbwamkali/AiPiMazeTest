"""Teacher bots for the stooge ants: each persona's strategy, played on a full Game.

Used to write training lessons (`python3 -m ant.bots --out lessons.jsonl`, add --v2 for the fast
numbered-door format) and by tools/race.py. Not needed by an ant at play.
"""
import argparse
import json
import random
import sys

from hypermaze import Game, Maze

from . import compact as C
from .lessons import maze_mix, room_screen
from .personas import NAMES, messages

SAY = {
    "Mooore": {
        "goal": ["Way Out, right there. Follow me, nobody dawdle.", "There's the exit. I knew it all along.",
                 "Way Out! Told you I'd find it."],
        "new": ["{to}, you're next. Out of my way.", "I'm taking {to}. No arguments.",
                "{to}. Move it.", "Right, {to}. I make the decisions around here."],
        "new_more": ["Plenty of doors. I'll start at the end of the list: {to}.",
                     "Last door first, that's my rule. {to}.", "{to}. The rest can wait their turn."],
        "back": ["Nothing left here. Back to {to}, march!", "Done with this room. {to}, now.",
                 "This place is finished. Retreating to {to} on my orders."],
        "back_loop": ["That untried door goes somewhere I've been. Waste of time. Back to {to}."],
        "return": ["Who left {to} half done? Back we go.", "Get back to {to} and finish the job."],
    },
    "Harry": {
        "goal": ["Is that... the Way Out? Oh thank goodness.", "The Way Out! Please don't be a trick.",
                 "Finally, the Way Out. My feet are killing me."],
        "new": ["Oh boy, {to}. I don't like it, but okay.", "Fine, fine, {to}. Why is it always me?",
                "{to} is new. Probably dark in there. Going anyway.", "I guess {to}. Nobody asks what I want."],
        "new_more": ["So many doors, I'll just take the first: {to}.", "{to}, first on the list. Less to think about.",
                     "Ugh, choices. {to}, I suppose."],
        "nerves": ["Nope, nope. Back to {to} for a second.", "I've got a bad feeling. Stepping back to {to}.",
                   "Too spooky. Retreat to {to}!"],
        "return": ["Okay, deep breath. Back to {to}.", "Alright, I'm being silly. Returning to {to}.",
                   "Sigh. {to} again. I'll be brave this time."],
        "map": ["Wait, let me check my notes before I go anywhere.", "Hold on, checking the map. Just to be sure."],
        "back": ["Nothing new here. Back to {to}, I knew it.", "Dead end, of course. Back to {to}.",
                 "All done here, I think. Shuffling back to {to}."],
        "back_loop": ["That door leads somewhere I've already been. Not falling for that. {to}."],
    },
    "Curlee": {
        "goal": ["Woo-hoo! Way Out! Wheee!", "Daylight! Curlee wins!", "Way Out! Boing boing!"],
        "new": ["Ooh, {to}! Charge!", "{to}! Curlee bonk!", "Wheee, {to}!", "Never been to {to}. Hup!"],
        "new_more": ["Eeny meeny... {to}!", "Lots of doors! {to}, because why not.", "Spin the bottle: {to}!"],
        "barge": ["Ooh, what's in {to}? Barging in!", "{to}! Again! Whee!", "Coming through, {to}!"],
        "return": ["Oops. Back to {to}.", "Whoopsie, wrong room. {to}!", "Hehe, back to {to}."],
        "back": ["Nothing here! Back to {to}!", "Boring room. {to}, go go go!", "Woop, dead end. {to}!"],
        "back_loop": ["Already been through there. Back to {to}!"],
    },
}

# how often each habit fires
HARRY_NERVES = 0.12
HARRY_MAP = 0.6
CURLEE_BARGE = 0.12



def decide(persona, g, last, rng):
    """(command, kind): command is a room name or 'map'."""
    m, cur = g.m, g.cur
    names = m.names
    for x in m.adj[cur]:
        if x == m.goal:
            return names[x], "goal"
    if last and last[0] == "walk" and last[2]:          # we left a room half done: go back
        return last[1], "return"
    new = [x for x in g.untried(cur) if not g.visits[x]]
    if persona == "Harry" and new and last and last[0] == "walk" and rng.random() < HARRY_NERVES:
        return last[1], "nerves"
    if persona == "Curlee" and new:
        seen = [x for x in m.adj[cur] if g.visits[x]]
        if seen and rng.random() < CURLEE_BARGE:
            return names[rng.choice(seen)], "barge"
    if new:
        kind = "new_more" if len(new) > 1 else "new"
        if persona == "Mooore":
            return names[new[-1]], kind
        if persona == "Curlee":
            return names[rng.choice(new)], kind
        return names[new[0]], kind
    if g.came_from[cur] >= 0:
        if persona == "Harry" and not (last and last[0] == "map") and rng.random() < HARRY_MAP:
            return "map", "map"
        loop = any(g.visits[x] for x in g.untried(cur))
        return names[g.came_from[cur]], ("back_loop" if loop else "back")
    return None, "stuck"


def step(g, cmd, prev=None):
    """Send a command; return (screen, last). prev = the last action before this one."""
    if cmd.strip().lower() == "map":
        return room_screen(g.send("map")), ("map",)
    frm = g.cur
    had_new = any(not g.visits[x] for x in g.untried(frm))
    before = list(g.visits)
    screen = room_screen(g.send(cmd))
    if g.cur == frm:          # invalid input, nothing moved
        return screen, ("invalid",)
    to = g.cur
    # "left half done": frm still had untried new paths, and we walked into a known room
    # that is not frm's own child (walking down into a child is a normal return trip)
    # and the move before was not itself flagged (so a trip back never gets flagged)
    left = had_new and before[to] > 0 and g.came_from[to] != frm and not (prev and prev[0] == "walk" and prev[2])
    return screen, ("walk", g.m.names[frm], left)


def say(persona, kind, to, rng):
    return rng.choice(SAY[persona][kind]).format(to=to)


def play(persona, maze, rng, wrong=0.05):
    """Yield (screen, last, command, reason, kind) for every lesson-worthy move."""
    g = Game(maze, mode="notes")
    screen, last = room_screen(g.intro()), None
    limit = maze.n * 6
    steps = 0
    while not g.done and steps < limit:
        steps += 1
        if rng.random() < wrong and len(maze.adj[g.cur]) > 1:   # unplanned wrong turn, not taught
            screen, last = step(g, maze.names[rng.choice(maze.adj[g.cur])], last)
            continue
        cmd, kind = decide(persona, g, last, rng)
        if cmd is None:
            break
        yield screen, last, cmd, say(persona, kind, cmd, rng), kind
        screen, last = step(g, cmd, last)
    return g


def main(argv=None):
    ap = argparse.ArgumentParser(description="lessons for the stooge ants")
    ap.add_argument("--out", required=True)
    ap.add_argument("--mazes", type=int, default=300, help="mazes per ant")
    ap.add_argument("--seed-base", type=int, default=100000)
    ap.add_argument("--quiet-share", type=float, default=0.3)
    ap.add_argument("--wrong", type=float, default=0.05)
    a = ap.parse_args(argv)
    rng = random.Random(a.seed_base)
    counts = {}
    with open(a.out, "w") as f:
        for p in NAMES:
            for i in range(a.mazes):
                dims, size, loops, gen = maze_mix(rng)
                maze = Maze(dims, size, loops, seed=a.seed_base + i, generator=gen)
                for screen, last, cmd, reason, kind in play(p, maze, rng, a.wrong):
                    quiet = rng.random() < a.quiet_share
                    msgs = messages(p, screen, last, quiet)
                    msgs.append({"role": "assistant", "content": f"GO: {cmd}" if quiet else f"{reason}\nGO: {cmd}"})
                    f.write(json.dumps({"messages": msgs}) + "\n")
                    counts.setdefault(p, {}).setdefault(kind, 0)
                    counts[p][kind] += 1
    for p, c in counts.items():
        print(p, sum(c.values()), "lessons", c)


def main_v2(argv=None):
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
        for p in NAMES:
            for i in range(a.mazes):
                dims, size, loops, gen = maze_mix(rng)
                maze = Maze(dims, size, loops, seed=a.seed_base + i, generator=gen)
                for screen, last, cmd, reason, kind in play(p, maze, rng, a.wrong):
                    quiet = rng.random() < a.quiet_share
                    msgs, doors = C.messages(p, screen, last, quiet)
                    say = None if quiet or not C.wants_comment(kind, len(doors), rng) else reason
                    msgs.append({"role": "assistant", "content": C.answer(cmd, doors, say)})
                    f.write(json.dumps({"messages": msgs}) + "\n")
                    n += 1
                    talk += say is not None
    print(f"{n} lessons, {talk} with a comment ({talk / max(1, n):.0%}) -> {a.out}")


if __name__ == "__main__":
    if "--v2" in sys.argv[1:]:
        main_v2([x for x in sys.argv[1:] if x != "--v2"])
    else:
        main()
