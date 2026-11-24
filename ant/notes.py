"""The ant's memory, kept only in its notebook in the house.

The house shows the ant nothing but the room it stands in and that room's doors. Everything the
ant remembers lives in its notebook, which the house keeps for it:

  ROOM: Coral Tower | doors: Iron Den; Jade Foyer; Way Out     written on arrival when the doors are new to it
  > Iron Den                                                  written after every move (from where it stood)

(Older notebooks wrote "MOVE: Coral Tower -> Iron Den"; both are read.)

Each turn the ant reads its pages back and works out, from those lines alone, which doors it has
tried, where it first came into each room and which rooms still have untried doors. It then
writes that up as the screen it was trained on, so the model needs no retraining.

The pen can fail: a nearly dry pen writes "(faint) ...", a worn tip drops letters ("Ir_n D_n"), and
a line that didn't take at all is blank. The ant reads what is there: the faint mark is ignored, a
name with lost letters counts only if exactly one name it has seen fits it, and the rest is lost.
"""
import re

LINE = re.compile(r"^\s*\d+\| (.*)$")
FAINT = re.compile(r"^\((?:very )?faint\) ")
LOST = "_"
# record starts, allowing for lost letters
ROOM_START = re.compile(r"^[R_][O_][O_][M_]: ")
MOVE_START = re.compile(r"^[M_][O_][V_][E_]: ")
STEP_START = re.compile(r"^> ")


def _fits(pattern, name):
    """True if a name with lost letters ("Ir_n D_n") could be this name."""
    return len(pattern) == len(name) and all(p == LOST or p.lower() == n.lower() for p, n in zip(pattern, name))


class AntNotes:
    def __init__(self, house):
        self.house = house
        self.page = 1          # the page the ant is writing on (it fills pages in order)
        self.known = {}        # room -> doors as last written, to skip rewriting the same room

    # ---- writing --------------------------------------------------------------------
    def _write(self, text):
        said = self.house.write("write " + text)
        if said.startswith("There isn't room"):
            self.house.write("next")
            self.page += 1
            self.house.write("write " + text)

    def room(self, name, doors):
        if self.known.get(name) != list(doors):
            self._write(f"ROOM: {name} | doors: " + "; ".join(doors))
            self.known[name] = list(doors)

    def move(self, frm, to):
        self._write(f"> {to}")      # where it came from is where it last stood: no need to spend ink on it

    # ---- reading back ---------------------------------------------------------------
    def records(self):
        """The ant's notebook records in order, wrapped lines joined back up, faint marks dropped."""
        out = []
        for p in range(1, self.page + 1):
            for ln in self.house.read(p).splitlines():
                m = LINE.match(ln)
                if not m:
                    continue
                text = FAINT.sub("", m.group(1)).rstrip()
                if not text:
                    continue                  # a line that didn't take
                if ROOM_START.match(text) or MOVE_START.match(text) or STEP_START.match(text) or not out:
                    out.append(text)
                else:
                    out[-1] += " " + text
        return out

    @staticmethod
    def _parse(rec):
        """("room", name, [doors]) | ("move", from or None, to) | None, names possibly with lost letters."""
        if ROOM_START.match(rec):
            body = rec[6:]
            if " | doors: " not in body:
                return None
            name, ds = body.split(" | doors: ", 1)
            return "room", name.strip(), [d.strip() for d in ds.split(";") if d.strip()]
        if MOVE_START.match(rec) and " -> " in rec:
            frm, to = rec[6:].split(" -> ", 1)
            return "move", frm.strip(), to.strip()
        if STEP_START.match(rec):
            return "move", None, rec[2:].strip()
        return None

    def state(self):
        parsed = [p for p in (self._parse(r) for r in self.records()) if p]
        names = set()
        for p in parsed:                      # every name written without a loss
            for nm in ([p[1]] + p[2] if p[0] == "room" else [p[1], p[2]]):
                if nm and LOST not in nm:
                    names.add(nm)

        def resolve(nm):
            if not nm or LOST not in nm:
                return nm
            hits = [n for n in names if _fits(nm, n)]
            return hits[0] if len(hits) == 1 else None

        doors, tried, came, visits, order = {}, {}, {}, {}, []
        pos = None
        for p in parsed:
            if p[0] == "room":
                name = resolve(p[1])
                if not name:
                    continue
                doors[name] = [d for d in (resolve(x) for x in p[2]) if d]
                if name not in visits:
                    visits[name] = 1
                    order.append(name)
                pos = name
            else:
                frm, to = (resolve(p[1]) if p[1] else pos), resolve(p[2])
                if not to:
                    pos = None                # lost track of where it went until the next room line
                    continue
                if frm:
                    tried.setdefault(frm, set()).add(to)
                    tried.setdefault(to, set()).add(frm)
                if to not in visits:
                    if frm:
                        came[to] = frm
                    order.append(to)
                visits[to] = visits.get(to, 0) + 1
                pos = to
        return {"doors": doors, "tried": tried, "came": came, "visits": visits, "order": order}

    @staticmethod
    def untried(st, room):
        t = st["tried"].get(room, set())
        return [d for d in st["doors"].get(room, []) if d not in t]

    def screen(self, view, st=None):
        """The notes screen the ant was trained on, written from its notebook alone."""
        st = st or self.state()
        cur, here = view["room"], view["doors"]
        u = self.untried(st, cur)
        open_ = sum(1 for r in st["order"] if r != cur and self.untried(st, r))
        o = ["\nYou are in the ", cur, ".\nPaths: ", ", ".join(here), "\nNotes: untried from here: "]
        o.append("none" if not u else ", ".join(d + (" (visited)" if st["visits"].get(d) else " (new)") for d in u))
        if cur in st["came"]:
            o.append(". You first came here from " + st["came"][cur])
        o.append(f". Other visited rooms with untried paths: {open_} (type map).\n> ")
        return "".join(o)
