#!/usr/bin/env python3
"""Show a maze's solution: the shortest route from the start to the Way Out.

  tools/solve.py --dims 2 --size 8 --loops 0.2 --seed 7
  tools/solve.py ... --from "Coral Study"      # route from any room
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hypermaze import Maze  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--dims", type=int, default=2)
ap.add_argument("--size", type=int, default=8)
ap.add_argument("--loops", type=float, default=0.0)
ap.add_argument("--seed", type=int, default=1)
ap.add_argument("--from", dest="frm")
a = ap.parse_args()
m = Maze(a.dims, a.size, a.loops, a.seed)
src = m.start if not a.frm else m.find(a.frm)
if src is None:
    sys.exit(f"no room called {a.frm!r}")
print(m.header())
for i, c in enumerate(m.shortest_path(src)):
    print(f"{i:4d}  {m.names[c]:<22} {m.coord_str(c):<14} dist {m.dist[c]}")
