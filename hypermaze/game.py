"""The text game: rules, modes and screens, as an engine with no I/O of its own.

    g = Game(Maze(4, 3, seed=7), mode="notes")
    screen = g.intro()          # first screen, ends with "\\n> "
    screen = g.send("Coral")    # one line of player input -> what the game prints
    g.done, g.result            # finished?  "SOLVED" / "GAVE UP" / "QUIT"

The text is byte-for-byte what maze.cpp prints, so play_llm.py and the bots work
unchanged. The one intended difference: in notes mode a path counts as tried
from both ends (notes_both_ways=True). Pass notes_both_ways=False to get the
old C++ notes exactly.

Logs: pass maze_log (a text file) to get <run>-maze.log in the C++ format, and
moves_log (a text file) to get the JSON-lines replay log. Every event is also
kept in g.events.
"""
import json
import re

from .core import Maze

MODES = ("none", "notes", "notebook", "both")  # "both" = --notes --notebook together, as the C++ allows
_WS = re.compile(r"[ \t\n\v\f\r]+")
_TRIM = " \t\r\n.\"'"
HOOKS = ("command", "move", "invalid", "end", "screen")


def lower(s):
    """ASCII-only lowercase, like tolower() in the C locale."""
    return s.translate(_LOWER)


_LOWER = {c: c + 32 for c in range(ord("A"), ord("Z") + 1)}


def trim(s):
    return s.strip(_TRIM)


def atoi(s):
    """C atoi: skip leading whitespace, optional sign, digits; 0 if none."""
    m = re.match(r"[ \t\n\v\f\r]*([+-]?\d+)", s)
    if not m:
        return 0
    v = int(m.group(1))
    return max(-2 ** 31, min(2 ** 31 - 1, v))


def _cfmt(buf, s):
    """snprintf into a char buf[buf]: output cut to buf-1 characters."""
    return s[:buf - 1]


class Game:
    def __init__(self, maze, mode="none", max_moves=0, pages=100, lines=35, width=60,
                 notes_both_ways=True, maze_log=None, moves_log=None):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.m = maze
        self.mode = mode
        self.notes = mode in ("notes", "both")
        self.notebook = mode in ("notebook", "both")
        self.max_moves = max_moves
        self.pages, self.plines, self.pwidth = pages, lines, width
        self.notes_both_ways = notes_both_ways
        self.maze_log, self.moves_log = maze_log, moves_log
        self.events = []

        n = maze.n
        self.cur = maze.start
        self.visits = [0] * n
        self.visits[self.cur] = 1
        self.moves = self.bad = 0
        self.best = maze.dist[self.cur]
        self.tried = [set() for _ in range(n)]
        self.came_from = [-1] * n
        self.path = [self.cur]  # every room stood in, in order
        self.book = [[""] * lines for _ in range(pages)]
        self.tabs = [""] * pages
        self.open_page = 0
        self.done = False
        self.result = None
        # extension points (see hypermaze/inject.py); all empty by default, so output matches the C++
        self.hooks = {k: [] for k in HOOKS}
        self.commands = {}
        self._pending = []
        self.stop_requested = None  # set by an injected "stop"

        self._log(maze.header() + "\n")
        self._log(_cfmt(256, "%-5s %-22s %-12s %-22s %-12s %4s %6s  %s\n" %
                        ("#move", "from", "from_xyzw", "to", "to_xyzw", "dist", "visits", "result")))
        self._event({"t": "start", "dims": maze.dims, "size": maze.size, "loops": maze.loops,
                     "seed": maze.seed, "rooms": maze.n, "start": maze.start, "goal": maze.goal,
                     "shortest": maze.shortest, "mode": mode, "max_moves": max_moves,
                     "notes_both_ways": notes_both_ways,
                     "notebook": {"pages": pages, "lines": lines, "width": width} if self.notebook else None})

    # ---- logging ---------------------------------------------------------
    def _log(self, s):
        if self.maze_log:
            self.maze_log.write(s)
            self.maze_log.flush()

    def _event(self, e):
        self.events.append(e)
        if self.moves_log:
            self.moves_log.write(json.dumps(e) + "\n")
            self.moves_log.flush()

    def _nlog(self, what):
        self._log(_cfmt(512, "%-5s %-22s %-12s %s\n" % ("-", self.m.names[self.cur], "", what)))
        self._event({"t": "note", "at": self.cur, "what": what})

    # ---- notes -----------------------------------------------------------
    def untried(self, r):
        return [x for x in self.m.adj[r] if x not in self.tried[r]]

    def _tag(self, x):
        return self.m.names[x] + (" (visited)" if self.visits[x] else " (new)")

    def show(self):
        m, cur = self.m, self.cur
        o = ["\nYou are in the ", m.names[cur], ".\nPaths:"]
        for i, x in enumerate(m.adj[cur]):
            o.append((", " if i else " ") + m.names[x])
        if self.notes:
            u = self.untried(cur)
            open_ = sum(1 for r in range(m.n) if self.visits[r] and r != cur and self.untried(r))
            o.append("\nNotes: untried from here: ")
            o.append("none" if not u else ", ".join(self._tag(x) for x in u))
            if self.came_from[cur] >= 0:
                o.append(". You first came here from " + m.names[self.came_from[cur]])
            o.append(f". Other visited rooms with untried paths: {open_} (type map).")
        if self._pending:
            o.extend(self._pending)
            self._pending = []
        o.append("\n> ")
        return "".join(o)

    def show_map(self, all_):
        m = self.m
        k = sum(1 for v in self.visits if v)
        o = [f"\nNotebook: {k} rooms visited.", "" if all_ else " Rooms that still have untried paths:"]
        for r in range(m.n):
            if not self.visits[r]:
                continue
            u = self.untried(r)
            if not all_ and not u:
                continue
            o.append("\n- " + m.names[r] + (" [you are here]" if r == self.cur else ""))
            if self.came_from[r] >= 0:
                o.append(" [first came from " + m.names[self.came_from[r]] + "]")
            o.append(" untried: " + ("none" if not u else ", ".join(self._tag(x) for x in u)))
            if all_:
                t = sorted(self.tried[r])
                o.append(" | tried: " + (", ".join(m.names[x] for x in t) if t else "none"))
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
        out, cur2, w_ = [], "", self.pwidth
        for w in _WS.split(s):
            if not w:
                continue
            while len(w) > w_:
                if cur2:
                    out.append(cur2)
                    cur2 = ""
                out.append(w[:w_])
                w = w[w_:]
            if not cur2:
                cur2 = w
            elif len(cur2) + 1 + len(w) <= w_:
                cur2 += " " + w
            else:
                out.append(cur2)
                cur2 = w
        if cur2:
            out.append(cur2)
        return out

    def _notebook_cmd(self, line, in_):
        """Returns the text for a notebook command, or None if it isn't one."""
        raw = trim(line)
        P = self.pages
        if in_ in ("read", "read page"):
            return self._show_page(self.open_page)
        if in_.startswith("read page ") or in_.startswith("page ") or in_.startswith("read "):
            p = atoi(in_[in_.rfind(" ") + 1:])
            if 1 <= p <= P:
                s = self._show_page(p - 1)
                self._nlog(f"READ page {p}")
                return s
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
            self._nlog(f"TAB page {self.open_page + 1} {lbl}")
            return f'\nYou stick a tab on page {self.open_page + 1} and label it "{lbl}".'
        if in_.startswith("write "):
            text = trim(raw[6:])
            parts = self._wrap(text)
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
            o += f". ({self._used()} of {P * self.plines} lines used in the notebook)"
            self._nlog(f"WRITE p{self.open_page + 1} {text}")
            return o
        if in_.startswith("erase "):
            k = atoi(in_[6:])
            pg = self.book[self.open_page]
            if 1 <= k <= self.plines and pg[k - 1]:
                pg[k - 1] = ""
                self._nlog(f"ERASE p{self.open_page + 1} line {k}")
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
        return "".join(o) + self.show()

    def _finish(self, result, text):
        m = self.m
        if result == "SOLVED":
            self._log(f"# SOLVED moves={self.moves} shortest={m.shortest} invalid={self.bad}\n")
        else:
            self._log(f"# {result} moves={self.moves} closest={self.best} invalid={self.bad}\n")
        self._event({"t": "end", "result": result, "moves": self.moves, "shortest": m.shortest,
                     "closest": self.best, "invalid": self.bad})
        self.done, self.result = True, result
        for fn in self.hooks["end"]:
            fn(self, result)
        if self._pending:
            text += "".join(m.lstrip("\n") + "\n" for m in self._pending)
            self._pending = []
        return text

    def stop(self, reason="stopped"):
        """End the game from outside (e.g. the showrunner). Logs '# STOPPED ...'."""
        return "" if self.done else self._finish("STOPPED", f"\n{reason}\n")

    def quit(self):
        """End the game as the C++ does on 'quit' or end of input."""
        return "" if self.done else self._finish("QUIT", "")

    def send(self, line):
        """One line of player input (without the newline) -> what the game prints."""
        if self.done:
            return ""
        out = None
        for fn in self.hooks["command"]:  # a hook may answer a line itself
            out = fn(self, line)
            if out is not None:
                break
        if out is None and self.commands:
            word, _, rest = lower(trim(line)).partition(" ")
            if word in self.commands:
                out = self.commands[word](self, rest.strip()) + self.show()
        if out is None:
            out = self._play(line)
        for fn in self.hooks["screen"]:
            r = fn(self, out)
            if r is not None:
                out = r
        return out

    # ---- extension API -----------------------------------------------------
    def on(self, hook, fn):
        """Register fn for a hook:
        command(game, line) -> text or None   answer a line before the game does
        move(game, frm, to)                   after every move (frm, to are room numbers)
        invalid(game, line)                   after an unmatched input
        end(game, result)                     when the game ends
        screen(game, text) -> text or None    rewrite any screen before it is printed"""
        self.hooks[hook].append(fn)
        return fn

    def add_command(self, word, fn):
        """New player command: typing '<word> <args>' calls fn(game, args) -> text; the room screen follows.
        Keep words that no room name starts with."""
        self.commands[word.lower()] = fn

    def say(self, text):
        """Show text to the player on the next screen, just above the prompt."""
        self._pending.append("\n" + text)

    def _play(self, line):
        m = self.m
        in_ = lower(trim(line))
        if in_.startswith("go "):
            in_ = trim(in_[3:])
        if in_.startswith("the "):
            in_ = in_[4:]
        if in_ == "quit":
            return self.quit()
        if in_ == "look" or not in_:
            self._event({"t": "cmd", "at": self.cur, "cmd": "look"})
            return self.show()
        if self.notes and in_ in ("map", "map all"):
            self._event({"t": "cmd", "at": self.cur, "cmd": in_})
            return self.show_map(in_ == "map all")
        if self.notebook:
            s = self._notebook_cmd(line, in_)
            if s is not None:
                return s + self.show()

        to, hits = -1, 0
        for x in m.adj[self.cur]:
            nm = lower(m.names[x])
            if nm == in_:
                to, hits = x, 1
                break
            if nm.startswith(in_):
                to = x
                hits += 1
        if hits != 1:
            self.bad += 1
            self._log(f"-\t{m.names[self.cur]}\t{m.coord_str(self.cur)}\t{trim(line)}\t-\t-\t-\tINVALID\n")
            self._event({"t": "invalid", "at": self.cur, "input": trim(line), "matches": hits})
            for fn in self.hooks["invalid"]:
                fn(self, line)
            return f'No path here leads to "{trim(line)}".' + self.show()

        frm = self.cur
        self.tried[frm].add(to)
        if self.notes_both_ways:
            self.tried[to].add(frm)
        if not self.visits[to]:
            self.came_from[to] = frm
        self.cur = to
        self.moves += 1
        self.visits[to] += 1
        self.path.append(to)
        self.best = min(self.best, m.dist[to])
        result = "revisit" if self.visits[to] > 1 else "new"
        self._log(_cfmt(256, "%-5d %-22s %-12s %-22s %-12s %4d %6d  %s\n" % (
            self.moves, m.names[frm], m.coord_str(frm), m.names[to], m.coord_str(to),
            m.dist[to], self.visits[to], result)))
        self._event({"t": "move", "n": self.moves, "from": frm, "to": to,
                     "from_name": m.names[frm], "to_name": m.names[to],
                     "from_coord": list(m.coord(frm)), "to_coord": list(m.coord(to)),
                     "dist": m.dist[to], "visits": self.visits[to], "result": result})
        for fn in self.hooks["move"]:
            fn(self, frm, to)
        if to == m.goal:
            return self._finish("SOLVED", f"\nYou step through into daylight. You found the Way Out in "
                                          f"{self.moves} moves (shortest: {m.shortest}).\n")
        if self.max_moves and self.moves >= self.max_moves:
            return self._finish("GAVE UP", f"\nYou are exhausted after {self.moves} moves.\n")
        return self.show()
