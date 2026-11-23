"""Injecting things into a running game.

Two ways in, both off by default (with neither, the game is byte-identical to the C++):

1. Plugins: a Python file with  def setup(game): ...  loaded with --plugin path.py.
   It can register hooks (game.on("move", fn) ...), add player commands
   (game.add_command("pray", fn)) and call game.say(text). See plugins/example_signposts.py.

2. The inbox: a JSON-lines file given with --inject path.jsonl. Anything appended to it
   while the game runs is applied at the next turn, just before the player's input is
   handled, so its effect shows in the reply to that input. One JSON object per line:

     {"kind": "message",    "text": "A bell rings somewhere above you."}
     {"kind": "teleport",   "room": "Coral Study"}                  # name or room number
     {"kind": "open_link",  "a": "Coral Study", "b": "Iron Kitchen"} # grid neighbours only
     {"kind": "close_link", "a": "Coral Study", "b": "Iron Kitchen"} # refused if it cuts off the Way Out
     {"kind": "rename",     "room": "Coral Study", "name": "Drowned Study"}
     {"kind": "stop",       "reason": "showrunner ended the episode"}

   Blank lines and lines starting with # are skipped. tools/inject.py writes these for you.
   Every applied injection goes to the maze log as a "- <room> INJECT ..." line (like the
   notebook lines) and to the moves log as {"t": "inject", ...}. New kinds: add a function
   to KINDS.
"""
import importlib.util
import json
import os
import sys


def _room(game, ref):
    if isinstance(ref, int):
        if 0 <= ref < game.m.n:
            return ref
        raise ValueError(f"no room {ref}")
    c = game.m.find(str(ref))
    if c is None:
        raise ValueError(f"no room called {ref!r}")
    return c


def _message(game, inj):
    game.say(str(inj["text"]))
    return "message " + str(inj["text"])


def _teleport(game, inj):
    to = _room(game, inj["room"])
    if to == game.m.goal:
        raise ValueError("teleporting onto the Way Out is not allowed")
    frm = game.cur
    if not game.visits[to]:
        game.came_from[to] = frm
    game.cur = to
    game.visits[to] += 1
    game.path.append(to)
    game.best = min(game.best, game.m.dist[to])
    return f"teleport {game.m.names[frm]} -> {game.m.names[to]}{game.m.coord_str(to)}"


def _open_link(game, inj):
    a, b = _room(game, inj["a"]), _room(game, inj["b"])
    game.m.add_link(a, b)
    return f"open_link {game.m.names[a]} <-> {game.m.names[b]}"


def _close_link(game, inj):
    a, b = _room(game, inj["a"]), _room(game, inj["b"])
    if not game.m.remove_link(a, b):
        raise ValueError("there is no passage there")
    game.tried[a].discard(b)
    game.tried[b].discard(a)
    return f"close_link {game.m.names[a]} <-> {game.m.names[b]}"


def _rename(game, inj):
    c = _room(game, inj["room"])
    old = game.m.names[c]
    game.m.rename(c, str(inj["name"]))
    return f"rename {old} -> {inj['name']}"


def _stop(game, inj):
    game.stop_requested = str(inj.get("reason", "stopped"))
    return "stop " + game.stop_requested


KINDS = {"message": _message, "teleport": _teleport, "open_link": _open_link,
         "close_link": _close_link, "rename": _rename, "stop": _stop}


def apply(game, inj):
    """Apply one injection (a dict). Returns True if applied; problems go to stderr and the logs."""
    kind = inj.get("kind")
    try:
        if kind not in KINDS:
            raise ValueError(f"unknown kind {kind!r}")
        what = KINDS[kind](game, inj)
    except (KeyError, ValueError, TypeError) as e:
        sys.stderr.write(f"inject: skipped {inj!r}: {e}\n")
        game._event({"t": "inject", "ok": False, "inject": inj, "error": str(e)})
        return False
    game._log(("%-5s %-22s %-12s %s\n" % ("-", game.m.names[game.cur], "", "INJECT " + what))[:511])
    game._event({"t": "inject", "ok": True, "at": game.cur, "inject": inj, "what": what})
    return True


class Inbox:
    """Follows a JSON-lines file, handing back lines appended since the last poll."""

    def __init__(self, path, from_start=False):
        self.path = path
        if not os.path.exists(path):
            open(path, "a").close()
        # By default only things written after the game starts count, so an old inbox can be reused.
        self.pos = 0 if from_start else os.path.getsize(path)
        self._partial = ""

    def poll(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                if os.path.getsize(self.path) < self.pos:  # file was truncated: start over
                    self.pos = 0
                f.seek(self.pos)
                data = f.read()
                self.pos = f.tell()
        except OSError:
            return []
        data = self._partial + data
        lines = data.split("\n")
        self._partial = lines.pop()  # keep an unfinished last line for next time
        out = []
        for ln in lines:
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            try:
                obj = json.loads(ln)
                if not isinstance(obj, dict):
                    raise ValueError("not an object")
                out.append(obj)
            except ValueError as e:
                sys.stderr.write(f"inject: bad line {ln!r}: {e}\n")
        return out

    def apply_new(self, game):
        for inj in self.poll():
            apply(game, inj)


def load_plugin(game, path):
    """Load a plugin file and call its setup(game)."""
    name = "hypermaze_plugin_" + os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "setup"):
        raise ValueError(f"plugin {path} has no setup(game)")
    mod.setup(game)
    return mod
