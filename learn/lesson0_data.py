#!/usr/bin/env python3
"""Lesson 0: turn maze games into training data.

Every model we build later learns from EXAMPLES: "the ant saw this screen -> the good move was that".
This script makes those examples. Read it top to bottom; every step is small.

  1. Play games with the teacher (the fair bot). It sees exactly the screen a person sees in house.py:
     the room, its doors, its walls, what is written on them, the last 10 rooms it remembers and its own
     notes. At each turn we record the screen and the move the teacher chose.
  2. Turn each screen into FEATURES: small facts a model can use, read off the screen text only,
     one row per possible move ("is this door the Way Out?", "is it in my memory?" ...).
  3. Split into a TRAINING set and a TEST set by maze (never by turn: turns from one maze look alike, so
     testing on turns from mazes the model trained on would flatter it).
  4. Save to files and print what we got.

Run it (about a minute):
    python3 learn/lesson0_data.py                       # 300 training mazes, 60 test mazes
    python3 learn/lesson0_data.py --mazes 50 --show 3   # small run, show 3 examples in full

Files written to learn/data/ (each line is one JSON record, easy to read with any tool):
    train.jsonl / test.jsonl        one record per turn: screen text, the moves allowed, the teacher's move
    train_rows.csv / test_rows.csv  one row per (turn, possible move) with the features and a 0/1 label

The lesson format is meant to change. To change it, edit FEATURES below (or the screen in runner/house.py);
everything else follows.
"""
import argparse
import csv
import json
import os
import random
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from hypermaze.core import Maze      # noqa: E402  the maze itself
from ant.relay3 import RelayGame     # noqa: E402  games with the fair teacher and the house.py screen

OUT = os.path.join(ROOT, "learn", "data")


# ---- 1. playing games ------------------------------------------------------------------------------
def play(maze, seed, ants):
    """Play one relay game with the fair teacher; yield one record per decision."""
    game = RelayGame(maze, ants=ants, seed=seed, briefing="size", teacher="fair", memory=10)
    while not game.done:
        screen, options = game.screen()          # what the ant sees, and the commands it may type
        move, kind = game.teach()                # what the teacher types
        yield {"seed": seed, "ant": game.k, "screen": screen, "options": sorted(options), "move": move,
               "kind": kind}
        game.apply(options[move])


# ---- 2. features: small facts read off the screen text --------------------------------------------
def parse(screen):
    """Pull the parts of the screen apart. Only the text the ant sees is used, nothing else."""
    room = re.search(r"You are in the (.+?)\.\n", screen).group(1)
    doors = [d.strip() for d in re.search(r"Paths: (.*)", screen).group(1).split(", ")]
    m = re.search(r"You remember: (.*), then here\.", screen)
    memory = m.group(1).split(", ") if m else []
    m = re.search(r"Written on the walls:\n((?:  .*\n?)*)", screen)
    walls = [l.strip() for l in m.group(1).splitlines()] if m else []
    m = re.search(r"Your notes about this room:\n((?:  .*\n?)*)", screen)
    notes = [l.strip() for l in m.group(1).splitlines()] if m else []
    return room, doors, memory, walls, notes


def FEATURES(screen, option):
    """One row of numbers for one possible move. Each is a fact a person could read off the screen."""
    room, doors, memory, walls, notes = parse(screen)
    is_door = option in doors
    door = option if is_door else ""
    return {
        "is_door": int(is_door),
        "is_way_out": int(door == "Way Out"),
        "came_from_here": int(bool(memory) and door == memory[-1]),      # the room I was in just before
        "in_memory": int(door in memory),                                 # one of my last 10 rooms
        "memory_age": (len(memory) - memory.index(door)) if door in memory else 0,   # 1 = just now
        "noted_went": int(any(n == f"{room} -> {door}" for n in notes)),  # my notes: I went from here to there
        "noted_came": int(any(n == f"{door} -> {room}" for n in notes)),  # my notes: I came here from there
        "wall_says_out": int(f"out {door}" in walls),                     # someone wrote "out <door>" here
        "n_doors": len(doors),
        "is_write": int(option.startswith("write")),
        "is_look": int(option == "look"),
    }


# ---- 3. the train / test split, by maze --------------------------------------------------------------
def mazes(n, seed_base):
    rng = random.Random(seed_base)
    for i in range(n):
        size = rng.choice([8, 10, 12, 14, 16, 18, 20])
        yield Maze(2, size, rng.choice([0.0, 0.1, 0.25]), seed=seed_base + i), seed_base + i, rng.choice([1, 2, 3])


def build(n, seed_base, name):
    records, rows = [], []
    for maze, seed, ants in mazes(n, seed_base):
        for rec in play(maze, seed, ants):
            records.append(rec)
            for option in rec["options"]:
                row = {"seed": seed, "turn": len(records) - 1, "option": option}
                row.update(FEATURES(rec["screen"], option))
                row["label"] = int(option == rec["move"])     # 1 = the move the teacher chose
                rows.append(row)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, f"{name}.jsonl"), "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    with open(os.path.join(OUT, f"{name}_rows.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return records, rows


# ---- 4. run it and look at what we got ---------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="Lesson 0: games -> training data")
    ap.add_argument("--mazes", type=int, default=300, help="training mazes (test mazes = a fifth of this)")
    ap.add_argument("--show", type=int, default=1, help="how many examples to print in full")
    a = ap.parse_args(argv)
    train, train_rows = build(a.mazes, 700000, "train")
    test, test_rows = build(max(10, a.mazes // 5), 800000, "test")   # different seeds: mazes it never saw

    print(f"training: {len(train)} turns from {a.mazes} mazes -> {len(train_rows)} rows (one per possible move)")
    print(f"test:     {len(test)} turns from {max(10, a.mazes // 5)} other mazes -> {len(test_rows)} rows")
    kinds = {}
    for r in train:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print("what the teacher was doing:", ", ".join(f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])))
    pos = sum(r["label"] for r in train_rows)
    print(f"labels: {pos} rows are the teacher's move, {len(train_rows) - pos} are not "
          f"(a model that always says 'not this one' would be right {100 * (1 - pos / len(train_rows)):.0f}% "
          f"of the time and useless: that is why we will score by games won, not by rows right)")
    for r in random.Random(1).sample(train, min(a.show, len(train))):
        print("\n" + "-" * 70 + "\n" + r["screen"])
        print(f"\nallowed: {r['options']}\nteacher typed: {r['move']}   ({r['kind']})")
        print("features of the teacher's move:", FEATURES(r["screen"], r["move"]))
    print(f"\nfiles in {OUT}")


if __name__ == "__main__":
    main()
