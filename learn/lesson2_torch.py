#!/usr/bin/env python3
"""Lesson 2: the same model as lesson 1, rewritten in PyTorch.

Lesson 1 did everything by hand: the score, the loss, the gradient, the update. PyTorch does the same work,
but you only write the SCORE and the LOSS; it works out the gradient for you (autograd) and an optimiser
takes the step. Every bigger model (lessons 3-7, and the Qwen ant) is built from exactly these pieces.

Same data, same model, same game scoring as lesson 1, so you can compare side by side.

Run it with the training Python (it has PyTorch):
    ~/ant-venv/bin/python learn/lesson2_torch.py --play 12
    ~/ant-venv/bin/python learn/lesson2_torch.py --optimizer adam --batch 1024     # mini-batches and Adam

Lesson 1 by hand            ->  Lesson 2 in PyTorch
  w = np.zeros(n); b = 0.0  ->  model = nn.Linear(n, 1)        (holds w and b, starts small)
  p = sigmoid(X @ w + b)    ->  logits = model(X)              (the score; the sigmoid lives in the loss)
  loss = -mean(y log p ...) ->  loss = BCEWithLogitsLoss()(logits, y)
  grad = X.T @ (p - y) / n  ->  loss.backward()                (autograd works out every gradient)
  w -= lr * grad            ->  optimizer.step()               (SGD here; Adam is one word away)
                                optimizer.zero_grad()           (gradients add up unless you clear them)
"""
import argparse
import os
import sys

import numpy as np
import torch
from torch import nn

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lesson1_logreg as L1   # noqa: E402  same data loading, same checks, same game scoring


def train(X, y, epochs, lr, optimizer="sgd", batch=0, seed=0):
    torch.manual_seed(seed)                       # same start every run: results you can reproduce
    Xt = torch.tensor(X, dtype=torch.float32)     # a tensor: NumPy's array, plus gradients and GPUs
    yt = torch.tensor(y, dtype=torch.float32).unsqueeze(1)
    model = nn.Linear(X.shape[1], 1)              # score = w . x + b, one weight per feature
    loss_fn = nn.BCEWithLogitsLoss()              # sigmoid + cross-entropy in one, numerically safer
    opt = (torch.optim.Adam(model.parameters(), lr=lr) if optimizer == "adam"
           else torch.optim.SGD(model.parameters(), lr=lr))
    n = len(yt)
    for epoch in range(1, epochs + 1):
        order = torch.randperm(n) if batch else torch.arange(n)
        step = batch or n                         # batch 0 = the whole set at once, exactly like lesson 1
        for i in range(0, n, step):
            idx = order[i:i + step]
            opt.zero_grad()                       # clear last step's gradients
            loss = loss_fn(model(Xt[idx]), yt[idx])
            loss.backward()                       # autograd: gradient of the loss for every weight
            opt.step()                            # move the weights a little downhill
        if epoch == 1 or epoch % max(1, epochs // 10) == 0:
            with torch.no_grad():
                full = loss_fn(model(Xt), yt).item()
            print(f"  epoch {epoch:4d}  loss {full:.4f}")
    w = model.weight.detach().numpy()[0]
    b = float(model.bias.detach().numpy()[0])
    return w, b


def main(argv=None):
    ap = argparse.ArgumentParser(description="Lesson 2: lesson 1's model in PyTorch")
    ap.add_argument("--epochs", type=int, default=300)
    ap.add_argument("--lr", type=float, default=0.5)
    ap.add_argument("--optimizer", choices=["sgd", "adam"], default="sgd")
    ap.add_argument("--batch", type=int, default=0, help="rows per step; 0 = all at once (as lesson 1)")
    ap.add_argument("--play", type=int, default=60)
    a = ap.parse_args(argv)
    X, y, turns, names = L1.load("train")
    Xt, yt, turns_t, _ = L1.load("test")
    print(f"training rows: {len(y)}, features: {len(names)}")
    print("lesson 1 (NumPy, by hand):")
    w1, b1 = L1.train(X, y, a.epochs, a.lr)
    print(f"lesson 2 (PyTorch, {a.optimizer}, batch {a.batch or 'all'}):")
    w2, b2 = train(X, y, a.epochs, a.lr, a.optimizer, a.batch)
    print("weights side by side (lesson 1 starts at zero, PyTorch starts small and random, so they differ a little):")
    print(f"  {'feature':18s} {'lesson 1':>9s} {'lesson 2':>9s}")
    for n, v1, v2 in sorted(zip(names, w1, w2), key=lambda t: -abs(t[2])):
        print(f"  {n:18s} {v1:+9.2f} {v2:+9.2f}")
    print(f"  {'(bias)':18s} {b1:+9.2f} {b2:+9.2f}")
    print("on paper (lesson 2's model):")
    L1.check(X, y, turns, w2, b2, "training mazes")
    L1.check(Xt, yt, turns_t, w2, b2, "test mazes    ")
    print(f"playing {a.play} mazes it has never seen (lesson 2's model, then the teacher):")
    L1.play(w2, b2, names, a.play, 900000)


if __name__ == "__main__":
    main()
