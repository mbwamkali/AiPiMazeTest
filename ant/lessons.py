"""Make "ant" lessons: a strategy bot plays mazes in notes mode, and every move it
makes becomes one training example (screen -> one human-sounding reason + GO: <room>).

The bot's rule only uses what the notes screen shows, so the model can learn it
from the screen alone (no history needed, which keeps prompts short and fast):
  1. If the Way Out is one of the paths, take it.
  2. Else if a path is untried and leads somewhere new, take the first such path.
  3. Else walk back to the room you first came here from (depth-first backtrack).

To teach recovery, the bot sometimes takes a random wrong turn on purpose. That
wrong move is NOT saved as a lesson, but the screens that follow it are, so the
model sees how to get back on track from a messy position.

    python -m ant.lessons --out lessons.jsonl --mazes 2000 --seed-base 100000
"""
import argparse
import json
import random
import re

from hypermaze import Game, Maze

GOAL = "Way Out"

SYSTEM_TALK = ("You are an ant exploring a maze, one room at a time. Read the room screen, "
               "say one short sentence about your thinking, then answer GO: <room name>.")
SYSTEM_QUIET = ("You are an ant exploring a maze, one room at a time. Read the room screen "
                "and answer only GO: <room name>.")

REASONS = {
    "goal": [
        "The Way Out is right there, I'm taking it!",
        "Daylight! The Way Out is one of the paths.",
        "There it is, the Way Out. Going.",
        "I can see the Way Out from here, no more wandering.",
    ],
    "new": [
        "I haven't been to {to} yet, so let's look there.",
        "{to} is new to me, exploring it.",
        "Untried path to {to}, which I've never seen. Trying it.",
        "Fresh ground: {to}. In I go.",
        "My notes say {to} is untried and new, so that's next.",
        "Let's check {to}, I haven't been that way.",
    ],
    "new_more": [
        "A few new paths here; I'll start with {to} and come back for the rest.",
        "{to} first. The other new paths can wait.",
        "Several untried ways out of here, picking {to} for now.",
    ],
    "back": [
        "Nothing new left here, heading back to {to} where I came from.",
        "Dead end for me. Back to {to}.",
        "All paths here are done, so I'll retrace my steps to {to}.",
        "This room is used up. Going back the way I first came, to {to}.",
        "No untried new rooms here. Backtracking to {to}.",
    ],
    "back_loop": [
        "The only untried path leads somewhere I've already been, so back to {to} instead.",
        "That untried path goes to a room I know, not worth it. Back to {to}.",
    ],
}

_ROOM = re.compile(r"You are in the (.+?)\.\n")


def room_screen(screen):
    """The part of a screen the model sees: from 'You are in' up to the prompt."""
    i = screen.find("You are in the")
    s = screen[i:] if i >= 0 else screen
    return s.replace("\n> ", "").rstrip()


def decide(g):
    """(room number, kind) the strategy bot picks, using only what the notes screen shows."""
    m, cur = g.m, g.cur
    for x in m.adj[cur]:
        if x == m.goal:
            return x, "goal"
    new = [x for x in g.untried(cur) if not g.visits[x]]
    if new:
        return new[0], ("new_more" if len(new) > 1 else "new")
    if g.came_from[cur] >= 0:
        loop = any(g.visits[x] for x in g.untried(cur))
        return g.came_from[cur], ("back_loop" if loop else "back")
    return None, "stuck"  # explored everything and no Way Out: cannot happen in a connected maze


def reason_for(kind, to_name, rng):
    return rng.choice(REASONS[kind]).format(to=to_name)


def play(maze, rng, wrong=0.08, max_moves=None):
    """Play one maze with the bot; yield (screen, move_name, reason, kind)."""
    g = Game(maze, mode="notes")
    screen = g.intro()
    limit = max_moves or maze.n * 4
    while not g.done and g.moves < limit:
        if rng.random() < wrong and len(maze.adj[g.cur]) > 1:
            to = rng.choice(maze.adj[g.cur])          # deliberate wrong turn, not saved
            screen = g.send(maze.names[to])
            continue
        to, kind = decide(g)
        if to is None:
            break
        name = maze.names[to]
        yield room_screen(screen), name, reason_for(kind, name, rng), kind
        screen = g.send(name)
    return g


def example(screen, move, reason, quiet):
    sysmsg = SYSTEM_QUIET if quiet else SYSTEM_TALK
    answer = f"GO: {move}" if quiet else f"{reason}\nGO: {move}"
    return {"messages": [{"role": "system", "content": sysmsg},
                         {"role": "user", "content": screen},
                         {"role": "assistant", "content": answer}]}


def maze_mix(rng):
    """A spread of maze shapes: 2D to 5D, small to medium, some with loops."""
    dims = rng.choice([2, 2, 3, 3, 4, 4, 5])
    size = {2: rng.randint(3, 8), 3: rng.randint(2, 5), 4: rng.randint(2, 4), 5: rng.randint(2, 3)}[dims]
    loops = rng.choice([0.0, 0.0, 0.0, 0.1, 0.2])
    gen = rng.choice(["dfs", "dfs", "wilson", "prim"])
    return dims, size, loops, gen


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--mazes", type=int, default=200)
    ap.add_argument("--seed-base", type=int, default=100000, help="maze seeds used: seed-base .. seed-base+mazes-1")
    ap.add_argument("--quiet-share", type=float, default=0.3, help="share of lessons in quiet mode (GO line only)")
    ap.add_argument("--wrong", type=float, default=0.08, help="chance of a deliberate wrong turn per move")
    a = ap.parse_args(argv)
    rng = random.Random(a.seed_base)
    kinds, n = {}, 0
    with open(a.out, "w") as f:
        for i in range(a.mazes):
            dims, size, loops, gen = maze_mix(rng)
            maze = Maze(dims, size, loops, seed=a.seed_base + i, generator=gen)
            for screen, move, reason, kind in play(maze, rng, a.wrong):
                f.write(json.dumps(example(screen, move, reason, rng.random() < a.quiet_share)) + "\n")
                kinds[kind] = kinds.get(kind, 0) + 1
                n += 1
    print(f"{n} lessons from {a.mazes} mazes -> {a.out}  kinds: {kinds}")


if __name__ == "__main__":
    main()
