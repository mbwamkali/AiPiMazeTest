"""The player's notebook, kept by the keeper (standard library only).

A device the player uses one line at a time:
  view(page)   the whole page as text (the open page if page is None)
  input(line)  one command line -> a short reply (navigation commands reply with the page)

It is plain paper: it stores exactly what the player writes and knows nothing about the house.
Any text gets the same treatment, so writing can't be used to learn anything about the rooms.
Commands (none of them cost a move): read, read page <n>, page <n>, next, back, write <text>,
erase <line>, tab <label>, tabs.
"""
import re

_WS = re.compile(r"[ \t\n\v\f\r]+")
_TRIM = " \t\r\n.\"'"
MAX_INPUT = 1000


def _atoi(s):
    m = re.match(r"\s*([+-]?\d+)", s)
    return int(m.group(1)) if m else 0


class Notebook:
    def __init__(self, pages=100, lines=35, width=60):
        self.pages, self.lines, self.width = pages, lines, width
        self.book = [[""] * lines for _ in range(pages)]
        self.tabs = [""] * pages
        self.open_page = 0

    # ---- reading -------------------------------------------------------------------
    def used(self):
        return sum(1 for pg in self.book for ln in pg if ln)

    def view(self, page=None):
        """The whole page (1-based page number, default the open page), with line numbers."""
        p = self.open_page if page is None else page - 1
        if not 0 <= p < self.pages:
            return f"The notebook has pages 1 to {self.pages}."
        o = [f"Notebook, page {p + 1} of {self.pages}"]
        if self.tabs[p]:
            o.append(f"  [tab: {self.tabs[p]}]")
        if p == 0:
            o.append("  (printed at the top: CONTENTS)")
        last = max((i for i in range(self.lines) if self.book[p][i]), default=-1)
        if last < 0:
            o.append("\n(blank page)")
        for i in range(last + 1):
            o.append("\n" + (" " if i < 9 else "") + f"{i + 1}| " + self.book[p][i])
        free = sum(1 for ln in self.book[p] if not ln)
        o.append(f"\n({free} of {self.lines} lines free on this page)")
        return "".join(o)

    # ---- writing -------------------------------------------------------------------
    def _wrap(self, s):
        out, line, w_ = [], "", self.width
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

    def _open(self, p):
        self.open_page = p
        return self.view()

    def input(self, text):
        """One command line. Returns the reply text; anything that isn't a notebook command says so."""
        raw = str(text)[:MAX_INPUT].strip(_TRIM)
        cmd = raw.lower()
        P = self.pages
        if cmd in ("read", "read page", "notebook"):
            return self.view()
        if cmd.startswith(("read page ", "page ", "read ")):
            p = _atoi(cmd[cmd.rfind(" ") + 1:])
            return self._open(p - 1) if 1 <= p <= P else f"The notebook has pages 1 to {P}."
        if cmd in ("next", "next page"):
            return self._open(self.open_page + 1) if self.open_page + 1 < P else "That was the last page."
        if cmd in ("back", "previous", "prev", "previous page"):
            return self._open(self.open_page - 1) if self.open_page > 0 else "This is the first page."
        if cmd == "tabs":
            found = [f"\n- page {p + 1}: {self.tabs[p]}" for p in range(P) if self.tabs[p]]
            return "Running your thumb along the edge, you feel tabs on:" + ("".join(found) if found else " none yet.")
        if cmd.startswith("tab "):
            lbl = raw[4:].strip(_TRIM)[:24]
            self.tabs[self.open_page] = lbl
            return f'You stick a tab on page {self.open_page + 1} and label it "{lbl}".'
        if cmd.startswith("write "):
            parts = self._wrap(raw[6:].strip(_TRIM))
            pg = self.book[self.open_page]
            free = [i for i in range(self.lines) if not pg[i]]
            if not parts:
                return "You hold the pencil over the page, but write nothing."
            if len(parts) > len(free):
                return (f"There isn't room on page {self.open_page + 1} ({len(free)} lines free, "
                        f"that needs {len(parts)}). Turn to another page.")
            for k, part in enumerate(parts):
                pg[free[k]] = part
            o = f"You write it on page {self.open_page + 1}, line" + ("s " if len(parts) > 1 else " ") + str(free[0] + 1)
            if len(parts) > 1:
                o += f"-{free[len(parts) - 1] + 1}"
            return o + f". ({self.used()} of {P * self.lines} lines used in the notebook)"
        if cmd.startswith("erase "):
            k = _atoi(cmd[6:])
            pg = self.book[self.open_page]
            if 1 <= k <= self.lines and pg[k - 1]:
                pg[k - 1] = ""
                return f"You rub out line {k} on page {self.open_page + 1}."
            return f"There is nothing on line {k} of page {self.open_page + 1}."
        return ("The notebook commands are: read, read page <n>, next, back, write <text>, "
                "erase <line>, tab <label>, tabs.")

