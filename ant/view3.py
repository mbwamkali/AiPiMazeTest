"""v3 ant screen: the v2 numbered door list plus the walls, notes on them, company and two extra moves.

What the ant sees each turn (all of it from what the house shows and its own notebook):

    Room: Coral Hall
    Walls: brick
    1 Iron Den - new
    2 Jade Foyer - done, a note here says done
    3 Cedar Orchard - untried, a note here says the Way Out is this way
    On the walls: X Jade Foyer | out Cedar Orchard
    Also here: Harry
    Harry says: "Iron Den is done"
    W - write on the wall: X Iron Den
    S - tell Harry: Iron Den is done
    Rooms elsewhere with untried paths: 3
    Last action: walked here from Iron Den.

The code works out the note it could write (W) and the tip it could pass on (S) from its own notebook;
the ant decides whether to. It answers GO: <door number>, GO: W, GO: S or GO: map. The material is
named, never what it does to a pen: the ant learns that from how its notes come out.

Notes it understands: "X <room>" (that way is done), "out <room>" (the Way Out is that way) and
"went <room>" (someone went that way). Names with lost letters count only if exactly one door fits.
"""
import re

from . import personas as P

NOTE = re.compile(r"^(?:\((?:very )?faint\) )?(X|out|went) (.+)$")
LOST = "_"

TALK3 = (" Answer GO: <door number, W, S or map>. Only when something interesting happens, add one short "
         "in-character line after it.")
QUIET3 = " Answer only GO: <door number, W, S or map>."


def _fits(pattern, name):
    return len(pattern) == len(name) and all(p == LOST or p.lower() == n.lower() for p, n in zip(pattern, name))


def read_notes(marks, doors):
    """{door: set of 'done' | 'out' | 'went'} from the notes on these walls that can be read."""
    out = {}
    for text in marks or []:
        m = NOTE.match(text.strip())
        if not m:
            continue
        kind, nm = m.group(1), m.group(2).strip()
        hits = [d for d in doors if _fits(nm, d)]
        if len(hits) == 1:
            out.setdefault(hits[0], set()).add({"X": "done", "out": "out", "went": "went"}[kind])
    return out


def last_text(last):
    """The "Last action" line: the v2 forms plus writing on a wall and deciding to mark the way."""
    if last and last[0] == "wrote":
        return ("Last action: wrote on the wall; " + (f'it came out: "{last[1]}"' if last[1] else "nothing showed") + ".")
    if last and last[0] == "mark":
        return "Last action: decided to go back and mark the way."
    return P.last_text(last)


def view(room, doors, untried, came, open_, last, walls="wood", marks=(), here=(), heard=(),
         write=None, tell=None):
    """(screen text, options): options maps an answer key ('1'.., 'W', 'S', 'map') to the action."""
    notes = read_notes(marks, doors)
    lines = [f"Room: {room}", f"Walls: {walls.replace('_', ' ')}"]
    options = {"map": ("map",)}
    for i, d in enumerate(doors, 1):
        if d == "Way Out":
            tag = "THE WAY OUT"
        elif untried.get(d) == "new":
            tag = "new"
        elif untried.get(d) == "visited":
            tag = "untried, leads to a known room"
        else:
            tag = "done"
        if d == came:
            tag += ", you first came from here"
        said = notes.get(d, set())
        if "out" in said:
            tag += ", a note here says the Way Out is this way"
        if "done" in said:
            tag += ", a note here says done"
        if "went" in said:
            tag += ", a note here says someone went this way"
        lines.append(f"{i} {d} - {tag}")
        options[str(i)] = ("go", d)
    if marks:
        lines.append("On the walls: " + " | ".join(marks))
    if here:
        lines.append("Also here: " + ", ".join(here))
    for h in heard:
        lines.append(h)
    if write:
        lines.append(f"W - write on the wall: {write}")
        options["W"] = ("write", write)
    if tell and here:
        lines.append(f"S - tell {', '.join(here)}: {tell}")
        options["S"] = ("say", tell)
    lines.append(f"Rooms elsewhere with untried paths: {open_}")
    lines.append(last_text(last))
    return "\n".join(lines), options


PROMPT_NAMES = {"NoMoe": "Mooore"}     # v3 is trained with the bossy ant's new name


def system_prompt(persona):
    text = P.SYSTEM[persona]
    for old, new in PROMPT_NAMES.items():
        text = text.replace(old, new)
    return text


def messages(persona, text, quiet):
    return [{"role": "system", "content": system_prompt(persona) + (QUIET3 if quiet else TALK3)},
            {"role": "user", "content": text}]


def answer(key, reason=None):
    return f"GO: {key}" if not reason else f"GO: {key}\n{reason}"


def parse(reply, options):
    """The action from a reply: ('go', door) | ('write', text) | ('say', text) | ('map',) | None."""
    for line in reply.splitlines():
        if "GO:" in line:
            v = line.split("GO:", 1)[1].strip().strip("*`'\". ")
            if v.lower().startswith("map"):
                return options["map"]
            k = v[:1].upper()
            if k in ("W", "S", "B") and k in options:
                return options[k]
            m = re.match(r"(\d+)", v)
            if m and m.group(1) in options:
                return options[m.group(1)]
            return None
    return None
