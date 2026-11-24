"""The ant's memory, kept only in its notebook in the house.

The house shows the ant nothing but the room it stands in and that room's doors. Everything the
ant remembers lives in its notebook, which the house keeps for it:

  ROOM: Coral Tower | doors: Iron Den; Jade Foyer; Way Out     written on arrival when the doors are new to it
  MOVE: Coral Tower -> Iron Den                               written after every move

Each turn the ant reads its pages back and works out, from those lines alone, which doors it has
tried, where it first came into each room and which rooms still have untried doors. It then
writes that up as the screen it was trained on, so the model needs no retraining.
"""
import re

LINE = re.compile(r"^\s*\d+\| (.*)$")


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
        self._write(f"MOVE: {frm} -> {to}")

    # ---- reading back ---------------------------------------------------------------
    def records(self):
        """The ant's notebook records in order, wrapped lines joined back up."""
        out = []
        for p in range(1, self.page + 1):
            for ln in self.house.read(p).splitlines():
                m = LINE.match(ln)
                if not m:
                    continue
                text = m.group(1)
                if text.startswith(("ROOM: ", "MOVE: ")) or not out:
                    out.append(text)
                else:
                    out[-1] += " " + text
        return out

    def state(self):
        doors, tried, came, visits, order = {}, {}, {}, {}, []
        for r in self.records():
            if r.startswith("ROOM: ") and " | doors: " in r:
                name, ds = r[6:].split(" | doors: ", 1)
                doors[name] = [d.strip() for d in ds.split(";") if d.strip()]
                if name not in visits:
                    visits[name] = 1
                    order.append(name)
            elif r.startswith("MOVE: ") and " -> " in r:
                frm, to = r[6:].split(" -> ", 1)
                tried.setdefault(frm, set()).add(to)
                tried.setdefault(to, set()).add(frm)
                if to not in visits:
                    came[to] = frm
                    order.append(to)
                visits[to] = visits.get(to, 0) + 1
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
