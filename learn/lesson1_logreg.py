#!/usr/bin/env python3
"""Lesson 1: your first model, by hand, in NumPy (logistic regression).

The idea in one sentence: give every possible move a SCORE = weighted sum of its features, and play the
move with the highest score. "Training" means finding weights that make the teacher's moves score highest.

You need the data from lesson 0 first:
    python3 learn/lesson0_data.py --mazes 100
Then:
    python3 learn/lesson1_logreg.py                 # train, then play 60 held-out mazes against the teacher
    python3 learn/lesson1_logreg.py --epochs 50 --lr 0.5

What happens, in order:
  1. Load the rows from lesson 0: one row per (turn, possible move), its features, and label 1 if it was the
     teacher's move.
  2. The model: score = w . x + b, turned into a probability with the sigmoid function, p = 1 / (1 + e^-score).
     p close to 1 means "this looks like the move the teacher would make".
  3. The loss (how wrong we are): binary cross-entropy, -[y log p + (1-y) log (1-p)], averaged over rows.
  4. The gradient: how much the loss changes if we nudge each weight. For this model it is simply
     (p - y) * x, averaged. Gradient descent = take a small step against it, many times.
  5. Check it two ways: rows it gets right (a weak measure), and turns where its top move is the teacher's.
  6. The real test: let it PLAY fresh mazes as the ant's brain, and compare with the teacher.
"""
import argparse
import csv
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
from lesson0_data import FEATURES   # noqa: E402  the same features lesson 0 wrote, read off the screen
from hypermaze.core import Maze      # noqa: E402
from ant.relay3 import RelayGame     # noqa: E402

DATA = os.path.join(HERE, "data")


# ---- 1. load the rows --------------------------------------------------------------------------------
def load(name):
    with open(os.path.join(DATA, f"{name}_rows.csv")) as f:
        rows = list(csv.DictReader(f))
    names = [k for k in rows[0] if k not in ("seed", "turn", "option", "label")]
    X = np.array([[float(r[k]) for k in names] for r in rows])      # one row of numbers per possible move
    y = np.array([float(r["label"]) for r in rows])                  # 1 = the teacher's move
    turns = np.array([int(r["turn"]) for r in rows])                 # which turn each row belongs to
    return X, y, turns, names


# ---- 2.-4. the model, its loss, and training by gradient descent -------------------------------------
def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


def train(X, y, epochs, lr):
    """Plain batch gradient descent. Returns the weights w (one per feature) and the bias b."""
    w = np.zeros(X.shape[1])          # start knowing nothing: every move scores the same
    b = 0.0
    for epoch in range(1, epochs + 1):
        p = sigmoid(X @ w + b)                                    # what the model thinks, for every row
        loss = -np.mean(y * np.log(p + 1e-9) + (1 - y) * np.log(1 - p + 1e-9))
        grad_w = X.T @ (p - y) / len(y)                           # the gradient, worked out by hand above
        grad_b = np.mean(p - y)
        w -= lr * grad_w                                          # one small step downhill
        b -= lr * grad_b
        if epoch == 1 or epoch % max(1, epochs // 10) == 0:
            print(f"  epoch {epoch:4d}  loss {loss:.4f}")
    return w, b


# ---- 5. how good is it on paper? --------------------------------------------------------------------
def check(X, y, turns, w, b, label):
    p = sigmoid(X @ w + b)
    rows_right = np.mean((p > 0.5) == (y == 1))
    hits = total = 0
    for t in np.unique(turns):                   # per turn: is the highest-scoring move the teacher's?
        idx = np.where(turns == t)[0]
        hits += y[idx[np.argmax(p[idx])]] == 1
        total += 1
    print(f"  {label}: rows right {rows_right:.1%} | turns where its top move is the teacher's {hits / total:.1%}")


# ---- 6. the real test: play -------------------------------------------------------------------------
def play(w, b, names, mazes, seed_base):
    """Let the model choose every move (features -> score -> highest wins); compare with the teacher."""
    out = {"model": [], "teacher": []}
    for i in range(mazes):
        size = [10, 12, 14, 16][i % 4]
        for who in out:
            maze = Maze(2, size, 0.1, seed=seed_base + i)
            g = RelayGame(maze, ants=1 + i % 3, seed=seed_base + i, briefing="size", teacher="fair", memory=10,
                          cap_factor=6)
            while not g.done:
                screen, options = g.screen()
                if who == "teacher":
                    move, _ = g.teach()
                else:
                    keys = sorted(options)
                    scores = [float(np.dot(w, [FEATURES(screen, k)[n] for n in names]) + b) for k in keys]
                    move = keys[int(np.argmax(scores))]
                g.apply(options[move])
            r = g.result()
            out[who].append((r["all_out"], r["total_moves"] / r["ants"], r["shortest"]))
    for who, rs in out.items():
        print(f"  {who:8s}: everyone out in {np.mean([r[0] for r in rs]):.0%} of games, "
              f"{np.mean([r[1] for r in rs]):.0f} moves per ant (shortest path averages {np.mean([r[2] for r in rs]):.0f})")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Lesson 1: logistic regression by hand")
    ap.add_argument("--epochs", type=int, default=300)
    ap.add_argument("--lr", type=float, default=0.5, help="learning rate: how big each downhill step is")
    ap.add_argument("--play", type=int, default=60, help="held-out mazes to play")
    a = ap.parse_args(argv)
    X, y, turns, names = load("train")
    Xt, yt, turns_t, _ = load("test")
    print(f"training rows: {len(y)}, features: {len(names)}")
    print("training:")
    w, b = train(X, y, a.epochs, a.lr)
    print("what it learned (weight per feature; positive = 'makes a move more likely'):")
    for n, v in sorted(zip(names, w), key=lambda nv: -abs(nv[1])):
        print(f"  {n:18s} {v:+.2f}")
    print(f"  {'(bias)':18s} {b:+.2f}")
    print("on paper:")
    check(X, y, turns, w, b, "training mazes")
    check(Xt, yt, turns_t, w, b, "test mazes    ")
    print(f"playing {a.play} mazes it has never seen (the real test):")
    play(w, b, names, a.play, 900000)


if __name__ == "__main__":
    main()
