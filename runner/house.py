#!/usr/bin/env python3
"""A text adventure: find the Way Out of the house.

The house itself lives on a server. Each time you walk through a door, this program asks
the house what the next room looks like; it only ever knows the rooms you have walked into.

Usage: house.py [--server URL] [--notes | --notebook] [--pages 100] [--lines 35] [--width 60]
  --server URL   where the house is (default: $HOUSE_SERVER, else http://127.0.0.1:7341)
  --token T      your key to the house (default: $HOUSE_TOKEN, else token_file in the settings)
  --config FILE  settings file (default: ~/hypermaze/config/runner.toml if present)
Commands: <room name>, look, quit, plus the mode's commands (map / map all; notebook commands).
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ENC = "latin-1"
_WS = re.compile(r"[ \t\n\v\f\r]+")
_TRIM = " \t\r\n.\"'"
_LOWER = {c: c + 32 for c in range(ord("A"), ord("Z") + 1)}
PATIENCE = 120  # seconds to keep knocking before giving up


def lower(s):
    return s.translate(_LOWER)


def trim(s):
    return s.strip(_TRIM)


def atoi(s):
    m = re.match(r"[ \t\n\v\f\r]*([+-]?\d+)", s)
    if not m:
        return 0
    return max(-2 ** 31, min(2 ** 31 - 1, int(m.group(1))))


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


class Game:
    def __init__(self, house, notes=False, notebook=False, pages=100, lines=35, width=60):
        self.house = house
        self.notes, self.notebook = notes, notebook
        self.pages, self.plines, self.pwidth = pages, lines, width
        self.view = house.room()
        here = self.view["room"]
        self.order = [here]            # rooms in the order first visited
        self.visits = {here: 1}
        self.doors = {here: list(self.view["doors"])}  # doors as last seen in each room
        self.tried = {here: set()}
        self.came_from = {}
        self.moves = 0
        self.book = [[""] * lines for _ in range(pages)]
        self.tabs = [""] * pages
        self.open_page = 0
        self.done = False
        self.result = None

    @property
    def cur(self):
        return self.view["room"]

    def _refresh(self):
        self.view = self.house.room()
        self.doors[self.cur] = list(self.view["doors"])

    # ---- notes -----------------------------------------------------------
    def untried(self, r):
        t = self.tried.get(r, set())
        return [x for x in self.doors.get(r, []) if x not in t]

    def _tag(self, x):
        return x + (" (visited)" if self.visits.get(x) else " (new)")

    def show(self):
        cur = self.cur
        o = ["\nYou are in the ", cur, ".\nPaths:"]
        for i, x in enumerate(self.view["doors"]):
            o.append((", " if i else " ") + x)
        if self.notes:
            u = self.untried(cur)
            open_ = sum(1 for r in self.order if r != cur and self.untried(r))
            o.append("\nNotes: untried from here: ")
            o.append("none" if not u else ", ".join(self._tag(x) for x in u))
            if cur in self.came_from:
                o.append(". You first came here from " + self.came_from[cur])
            o.append(f". Other visited rooms with untried paths: {open_} (type map).")
        for m in self.view.get("messages") or []:
            o.append("\n" + m)
        self.view["messages"] = []
        o.append("\n> ")
        return "".join(o)

    def show_map(self, all_):
        o = [f"\nNotebook: {len(self.order)} rooms visited.", "" if all_ else " Rooms that still have untried paths:"]
        for r in self.order:
            u = self.untried(r)
            if not all_ and not u:
                continue
            o.append("\n- " + r + (" [you are here]" if r == self.cur else ""))
            if r in self.came_from:
                o.append(" [first came from " + self.came_from[r] + "]")
            o.append(" untried: " + ("none" if not u else ", ".join(self._tag(x) for x in u)))
            if all_:
                t = self.tried.get(r, set())
                seen = [x for x in self.doors.get(r, []) if x in t] + sorted(t - set(self.doors.get(r, [])))
                o.append(" | tried: " + (", ".join(seen) if seen else "none"))
        return "".join(o) + self.show()

    # ---- notebook --------------------------------------------------------
    def _used(self):
        return sum(1 for pg in self.book for ln in pg if ln)

    def _show_page(self, p):
        self.open_page = p
        o = [f"\nNotebook, page {p + 1} of {self.pages}"]
        if self.tabs[p]:
            o.append(f"  [tab: {self.tabs[p]}]")
        if p == 0:
            o.append("  (printed at the top: CONTENTS)")
        last = -1
        for i in range(self.plines):
            if self.book[p][i]:
                last = i
        if last < 0:
            o.append("\n(blank page)")
        for i in range(last + 1):
            o.append("\n" + (" " if i < 9 else "") + f"{i + 1}| " + self.book[p][i])
        free = sum(1 for ln in self.book[p] if not ln)
        o.append(f"\n({free} of {self.plines} lines free on this page)")
        return "".join(o)

    def _wrap(self, s):
        out, line, w_ = [], "", self.pwidth
        for w in _WS.split(s):
            if not w:
                continue
            while len(w) > w_:
                if line:
                    out.append(line)
                    line = ""
                out.append(w[:w_])
                w = w[w_:]
            if not line:
                line = w
            elif len(line) + 1 + len(w) <= w_:
                line += " " + w
            else:
                out.append(line)
                line = w
        if line:
            out.append(line)
        return out

    def _notebook_cmd(self, text, in_):
        raw = trim(text)
        P = self.pages
        if in_ in ("read", "read page"):
            return self._show_page(self.open_page)
        if in_.startswith("read page ") or in_.startswith("page ") or in_.startswith("read "):
            p = atoi(in_[in_.rfind(" ") + 1:])
            if 1 <= p <= P:
                return self._show_page(p - 1)
            return f"\nThe notebook has pages 1 to {P}."
        if in_ in ("next", "next page"):
            return self._show_page(self.open_page + 1) if self.open_page + 1 < P else "\nThat was the last page."
        if in_ in ("back", "previous", "prev", "previous page"):
            return self._show_page(self.open_page - 1) if self.open_page > 0 else "\nThis is the first page."
        if in_ == "tabs":
            o = ["\nRunning your thumb along the edge, you feel tabs on:"]
            any_ = False
            for p in range(P):
                if self.tabs[p]:
                    o.append(f"\n- page {p + 1}: {self.tabs[p]}")
                    any_ = True
            if not any_:
                o.append(" none yet.")
            return "".join(o)
        if in_.startswith("tab "):
            lbl = trim(raw[4:])[:24]
            self.tabs[self.open_page] = lbl
            return f'\nYou stick a tab on page {self.open_page + 1} and label it "{lbl}".'
        if in_.startswith("write "):
            parts = self._wrap(trim(raw[6:]))
            pg = self.book[self.open_page]
            free = [i for i in range(self.plines) if not pg[i]]
            if not parts:
                return "\nYou hold the pencil over the page, but write nothing."
            if len(parts) > len(free):
                return (f"\nThere isn't room on page {self.open_page + 1} ({len(free)} lines free, "
                        f"that needs {len(parts)}). Turn to another page.")
            for k, part in enumerate(parts):
                pg[free[k]] = part
            o = f"\nYou write it on page {self.open_page + 1}, line" + ("s " if len(parts) > 1 else " ") + str(free[0] + 1)
            if len(parts) > 1:
                o += f"-{free[len(parts) - 1] + 1}"
            return o + f". ({self._used()} of {P * self.plines} lines used in the notebook)"
        if in_.startswith("erase "):
            k = atoi(in_[6:])
            pg = self.book[self.open_page]
            if 1 <= k <= self.plines and pg[k - 1]:
                pg[k - 1] = ""
                return f"\nYou rub out line {k} on page {self.open_page + 1}."
            return f"\nThere is nothing on line {k} of page {self.open_page + 1}."
        return None

    # ---- the game --------------------------------------------------------
    def intro(self):
        o = ["You wake in a room with no memory of how you got here. Your mind is foggy: you can only\n"
             "hold the last little while in your head before it slips away."]
        if self.notebook:
            o.append(" Apart from your clothes, all\n"
                     "you have is a small backpack, and in it a notebook with plenty of paper and a pencil.\n"
                     "What you do with it is yours.")
        o.append("\nSomewhere in this house is the Way Out. Type the name of a path to walk to that room.\n"
                 "Commands: look, quit")
        if self.notes:
            o.append(", map, map all")
        if self.notebook:
            o.append(f".\nThe notebook is graph paper: {self.pages} pages of {self.plines} lines, {self.pwidth} characters to a line.\n"
                     "In this fog you can only make out one page at a time, so you may want to keep the first page\n"
                     "as a contents, and stick tabs on pages so you can find your way back to them.\n"
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
        return said + self._closing(why, moves)

    def _closing(self, why, moves):
        v = self.view
        if why == "way_out":
            return f"\nYou step through into daylight. You found the Way Out in {moves} moves (shortest: {v.get('shortest')}).\n"
        if why == "exhausted":
            return f"\nYou are exhausted after {moves} moves.\n"
        return "\n" + str(v.get("farewell") or "The lights go out. The game is over.") + "\n"

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
        if self.notes and in_ in ("map", "map all"):
            self._refresh()
            return self._ending() or self.show_map(in_ == "map all")
        if self.notebook:
            s = self._notebook_cmd(text, in_)
            if s is not None:
                self._refresh()
                return self._ending() or (s + self.show())

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

        frm = self.cur
        v = self.house.walk(to)
        if v.get("error"):  # the door was not there after all: show the room as it is
            self._refresh()
            return self._ending() or (f'No path here leads to "{trim(text)}".' + self.show())
        self.view = v
        new = v["room"]
        if v.get("turn", 0) > self.moves:
            self.moves = v["turn"]
            self.tried.setdefault(frm, set()).add(to)
            self.tried.setdefault(new, set()).add(frm)
            if not self.visits.get(new):
                self.came_from[new] = frm
                self.order.append(new)
            self.visits[new] = self.visits.get(new, 0) + 1
        self.doors[new] = list(v["doors"])
        return self._ending() or self.show()


SETTINGS = os.path.join("~", "hypermaze", "config", "runner.toml")
SETTING_TYPES = {"server": str, "ca_cert": str, "token_file": str, "mode": str,
                 "pages": int, "lines": int, "width": int}


def read_settings(path=None):
    """The optional settings file: `path` (must exist), else ~/hypermaze/config/runner.toml if present.
    Problems go to stderr only and stop the program before the game begins."""
    if path is None:
        path = os.path.expanduser(SETTINGS)
        if not os.path.exists(path):
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
    if raw.get("mode", "none") not in ("none", "notes", "notebook"):
        sys.exit(f"settings: {path}: mode must be none, notes or notebook")
    return {k: os.path.expanduser(v) if k in ("ca_cert", "token_file") else v for k, v in raw.items()}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="house.py", description="Find the Way Out of the house.")
    ap.add_argument("--config", help="settings file (default ~/hypermaze/config/runner.toml if present)")
    ap.add_argument("--server", default=None)
    ap.add_argument("--token", default=None)
    ap.add_argument("--notes", action="store_true")
    ap.add_argument("--notebook", action="store_true")
    ap.add_argument("--pages", type=int, default=None)
    ap.add_argument("--lines", type=int, default=None)
    ap.add_argument("--width", type=int, default=None)
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
    if a.notes or a.notebook:
        notes, notebook = a.notes, a.notebook
    else:
        notes, notebook = cfg.get("mode") == "notes", cfg.get("mode") == "notebook"
    pages = a.pages if a.pages is not None else cfg.get("pages", 100)
    lines = a.lines if a.lines is not None else cfg.get("lines", 35)
    width = a.width if a.width is not None else cfg.get("width", 60)
    out, inp = sys.stdout.buffer, sys.stdin.buffer

    def say(s):
        if s:
            out.write(s.encode(ENC, "replace"))
            out.flush()

    if not token:
        sys.stderr.write("no key: set HOUSE_TOKEN or pass --token\n")
        return 2
    try:
        game = Game(House(server, token), notes, notebook, pages, lines, width)
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
