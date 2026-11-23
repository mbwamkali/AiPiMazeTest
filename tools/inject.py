#!/usr/bin/env python3
"""Append an injection to a running game's inbox (see hypermaze/inject.py).

  tools/inject.py run-inject.jsonl message "A bell rings somewhere above you."
  tools/inject.py run-inject.jsonl teleport "Coral Study"
  tools/inject.py run-inject.jsonl open_link "Coral Study" "Iron Kitchen"
  tools/inject.py run-inject.jsonl close_link "Coral Study" "Iron Kitchen"
  tools/inject.py run-inject.jsonl rename "Coral Study" "Drowned Study"
  tools/inject.py run-inject.jsonl stop "The audience has seen enough."
  tools/inject.py run-inject.jsonl raw '{"kind": "message", "text": "hi"}'
"""
import json
import sys

FIELDS = {"message": ["text"], "teleport": ["room"], "open_link": ["a", "b"], "close_link": ["a", "b"],
          "rename": ["room", "name"], "stop": ["reason"]}


def main(argv):
    if len(argv) < 3 or argv[1] not in list(FIELDS) + ["raw"]:
        sys.stderr.write(__doc__)
        return 2
    path, kind, args = argv[0], argv[1], argv[2:]
    if kind == "raw":
        obj = json.loads(args[0])
    else:
        names = FIELDS[kind]
        if len(args) != len(names):
            sys.stderr.write(f"{kind} needs: {' '.join(names)}\n")
            return 2
        obj = {"kind": kind}
        for k, v in zip(names, args):
            obj[k] = int(v) if v.isdigit() else v
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
