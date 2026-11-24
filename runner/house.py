#!/usr/bin/env python3
"""A text adventure: find the Way Out of the house.

The house itself lives on a server. You only ever see the room you are standing in: its name and
the doors you can take, each labelled with the room it leads to. Anything else is yours to remember.
With --notebook you also carry a notebook; the house keeps it for you, one line at a time.

Usage: house.py [--server URL] [--notebook]
  --server URL   where the house is (default: $HOUSE_SERVER, else http://127.0.0.1:7341)
  --token T      your key to the house (default: $HOUSE_TOKEN, else token_file in the settings)
  --config FILE  settings file (default: $HOUSE_CONFIG if set)
Commands: <room name>, look, quit, and with --notebook: read, read page <n>, next, back,
write <text>, erase <line>, tab <label>, tabs.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ENC = "latin-1"
_TRIM = " \t\r\n.\"'"
_LOWER = {c: c + 32 for c in range(ord("A"), ord("Z") + 1)}
PATIENCE = 120  # seconds to keep knocking before giving up
NOTEBOOK_WORDS = ("read", "page", "next", "back", "previous", "prev", "write", "erase", "tab", "tabs", "notebook")


def lower(s):
    return s.translate(_LOWER)


def trim(s):
    return s.strip(_TRIM)


class Gone(Exception):
    """The house can't be reached."""


class House:
    def __init__(self, url, token):
        self.url = url.rstrip("/")
        self.token = token

    def _call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": "Bearer " + self.token}
        if data is not None:
            headers["Content-Type"] = "application/json"
        deadline = time.time() + PATIENCE
        wait = 1.0
        while True:
            req = urllib.request.Request(self.url + path, data, headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 400:
                    return json.load(e)
                if e.code == 404:
                    raise Gone("the house does not know this key")
                err = e
            except (urllib.error.URLError, OSError, ValueError) as e:
                err = e
            if time.time() > deadline:
                raise Gone(str(err))
            time.sleep(wait)
            wait = min(wait * 2, 10)

    def room(self):
        return self._call("GET", "/v1/room")

    def walk(self, name):
        return self._call("POST", "/v1/walk", {"to": name})

    def read(self, page=None):
        """A notebook page as text (the open page by default)."""
        return self._call("GET", "/v1/device/notebook" + (f"?page={int(page)}" if page else ""))["screen"]

    def write(self, line):
        """One line into the notebook; returns the notebook's reply."""
        return self._call("POST", "/v1/device/notebook", {"line": line}).get("reply", "")


class Game:
    def __init__(self, house, notebook=False):
        self.house = house
        self.notebook = notebook
        self.view = house.room()
        self.moves = 0
        self.done = False
        self.result = None

    @property
    def cur(self):
        return self.view["room"]

    def _refresh(self):
        self.view = self.house.room()

    def show(self):
        o = ["\nYou are in the ", self.cur, ".\nPaths:"]
        for i, x in enumerate(self.view["doors"]):
            o.append((", " if i else " ") + x)
        for m in self.view.get("messages") or []:
            o.append("\n" + m)
        self.view["messages"] = []
        o.append("\n> ")
        return "".join(o)

    def intro(self):
        o = ["You wake in a room with no memory of how you got here. Your mind is foggy: you can only\n"
             "hold the last little while in your head before it slips away."]
        if self.notebook:
            o.append(" Apart from your clothes, all\n"
                     "you have is a small backpack, and in it a notebook and a pencil. What you do with it is yours.")
        o.append("\nSomewhere in this house is the Way Out. Type the name of a path to walk to that room.\n"
                 "Commands: look, quit")
        if self.notebook:
            o.append(".\nThe notebook has a fixed number of pages, and in this fog you can only make out one page at a time.\n"
                     "Notebook commands (none of them cost a move): read, read page <n>, next, back,\n"
                     "write <text> (on the open page), erase <line>, tab <label> (on the open page), tabs")
        o.append(".\n")
        return "".join(o) + (self._ending() or self.show())

    def _ending(self):
        """The closing text if the game is over, else None."""
        v = self.view
        why = v.get("ended")
        if not why:
            return None
        self.done, self.result = True, why
        moves = v.get("moves", self.moves)
        said = "".join("\n" + m for m in v.get("messages") or [])
        v["messages"] = []
        if why == "way_out":
            return said + f"\nYou step through into daylight. You found the Way Out in {moves} moves.\n"
        if why == "exhausted":
            return said + f"\nYou are exhausted after {moves} moves.\n"
        return said + "\n" + str(v.get("farewell") or "The lights go out. The game is over.") + "\n"

    def send(self, text):
        if self.done:
            return ""
        in_ = lower(trim(text))
        if in_.startswith("go "):
            in_ = trim(in_[3:])
        if in_.startswith("the "):
            in_ = in_[4:]
        if in_ == "quit":
            self.done, self.result = True, "quit"
            return ""
        if in_ == "look" or not in_:
            self._refresh()
            return self._ending() or self.show()
        if self.notebook and in_.split(" ", 1)[0] in NOTEBOOK_WORDS:
            said = self.house.write(trim(text))
            self._refresh()
            return self._ending() or ("\n" + said + self.show())

        to, hits = None, 0
        for x in self.view["doors"]:
            nm = lower(x)
            if nm == in_:
                to, hits = x, 1
                break
            if nm.startswith(in_):
                to = x
                hits += 1
        if hits != 1:
            self._refresh()
            return self._ending() or (f'No path here leads to "{trim(text)}".' + self.show())
        v = self.house.walk(to)
        if v.get("error"):  # the door was not there after all: show the room as it is
            self._refresh()
            return self._ending() or (f'No path here leads to "{trim(text)}".' + self.show())
        self.view = v
        self.moves = max(self.moves, v.get("turn", 0))
        return self._ending() or self.show()


SETTING_TYPES = {"server": str, "ca_cert": str, "token_file": str, "mode": str}


def read_settings(path=None):
    """The optional settings file: `path`, else $HOUSE_CONFIG; either must exist.
    Problems go to stderr only and stop the program before the game begins."""
    path = path or os.environ.get("HOUSE_CONFIG")
    if not path:
        return {}
    path = os.path.expanduser(path)
    try:
        import tomllib
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except ImportError:
        sys.exit(f"settings: {path}: TOML needs Python 3.11 or newer")
    except (OSError, ValueError) as e:
        sys.exit(f"settings: {path}: {e}")
    for k, v in raw.items():
        want = SETTING_TYPES.get(k)
        if want is None or not isinstance(v, want) or isinstance(v, bool):
            sys.exit(f"settings: {path}: unusable setting {k!r}")
    if raw.get("mode", "none") not in ("none", "notebook"):
        sys.exit(f"settings: {path}: mode must be none or notebook")
    return {k: os.path.expanduser(v) if k in ("ca_cert", "token_file") else v for k, v in raw.items()}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="house.py", description="Find the Way Out of the house.")
    ap.add_argument("--config", help="settings file (default: $HOUSE_CONFIG if set)")
    ap.add_argument("--server", default=None)
    ap.add_argument("--token", default=None)
    ap.add_argument("--notebook", action="store_true")
    a = ap.parse_args(argv)
    cfg = read_settings(a.config)
    server = a.server or os.environ.get("HOUSE_SERVER") or cfg.get("server") or "http://127.0.0.1:7341"
    token = a.token or os.environ.get("HOUSE_TOKEN", "")
    if not token and cfg.get("token_file"):
        try:
            with open(cfg["token_file"]) as f:
                token = f.readline().strip()
        except OSError as e:
            sys.exit(f"settings: {e}")
    if cfg.get("ca_cert") and not os.environ.get("SSL_CERT_FILE"):
        os.environ["SSL_CERT_FILE"] = cfg["ca_cert"]
    notebook = a.notebook or cfg.get("mode") == "notebook"
    out, inp = sys.stdout.buffer, sys.stdin.buffer

    def say(s):
        if s:
            out.write(s.encode(ENC, "replace"))
            out.flush()

    if not token:
        sys.stderr.write("no key: set HOUSE_TOKEN or pass --token\n")
        return 2
    try:
        game = Game(House(server, token), notebook)
        say(game.intro())
        while not game.done:
            b = inp.readline()
            if not b:
                break
            text = b.decode(ENC)
            if text.endswith("\n"):
                text = text[:-1]
            say(game.send(text))
    except Gone as e:
        say("\nThe house has gone quiet. (" + str(e) + ")\n")
        return 3
    return 0 if game.result == "way_out" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(1)
