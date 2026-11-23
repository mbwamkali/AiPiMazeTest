#!/usr/bin/env python3
"""Turn a run's log (<run>-maze.log from C++ or Python, or <run>-moves.jsonl) into one
viewer JSON file with the maze and the replay.

  tools/log2json.py notes-maze.log notes-run.json
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hypermaze.replay import load_run, to_viewer  # noqa: E402

if len(sys.argv) != 3:
    sys.exit(__doc__)
run = load_run(sys.argv[1])
open(sys.argv[2], "w").write(json.dumps(to_viewer(run)) + "\n")
e = run["end"] or {}
print(f"{len(run['path']) - 1} moves, {len(run['steps'])} events, end: {e.get('result', 'none')}")
