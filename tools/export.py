#!/usr/bin/env python3
"""Write a maze as JSON for the Colosseum viewer.

  tools/export.py --dims 2 --size 8 --seed 7 [--loops 0.2] [--out maze.json] [--slice 1=0]
Prints to stdout without --out. --slice AXIS=VALUE (repeatable) adds a "slice" section.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hypermaze import Maze  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--dims", type=int, default=2)
ap.add_argument("--size", type=int, default=8)
ap.add_argument("--loops", type=float, default=0.0)
ap.add_argument("--seed", type=int, default=1)
ap.add_argument("--slice", action="append", default=[])
ap.add_argument("--out")
ap.add_argument("--indent", type=int)
a = ap.parse_args()
m = Maze(a.dims, a.size, a.loops, a.seed)
d = m.to_dict()
if a.slice:
    fixed = {int(k): int(v) for k, v in (s.split("=") for s in a.slice)}
    d["slice"] = {"fixed": {str(k): v for k, v in fixed.items()}, **m.slice(fixed)}
text = json.dumps(d, indent=a.indent)
if a.out:
    open(a.out, "w").write(text + "\n")
else:
    print(text)
