"""Pulling the move out of a model's reply (used by play_llm.py).

Fix for the old harness: it sent "<command or room name>" echoed from the prompt, and
when no GO: line was found it sent the last line of the reply, i.e. reasoning, as a move.
"""

MAX_MOVE_LEN = 60        # a room name or command is short; anything longer is reasoning
LONG_OK = ("write ", "tab ")  # notebook commands may carry longer text

def candidates(text):
    """Usable moves from every line with GO: in it, in order."""
    out = []
    for line in text.splitlines():
        if "GO:" not in line:
            continue
        first = line.split("GO:", 1)[1].strip().strip("*`'\" ")
        if first.lower().startswith(LONG_OK):   # notebook text may itself contain "GO:"
            out.append(first)
            continue
        val = line.rsplit("GO:", 1)[1].strip(" \t*`'\".")
        if not val or val.startswith("<"):          # the prompt's own "GO: <command or room name>"
            continue
        if len(val) > MAX_MOVE_LEN and not val.lower().startswith(LONG_OK):
            continue
        out.append(val)
    return out

def pick_move(reply_vis, reasoning, reply):
    """(move, where) with where in visible / thinking / fallback."""
    c = candidates(reply_vis)
    if c:
        return c[-1], "visible"
    c = candidates(reasoning + "\n" + reply)   # the choice sometimes ends up only inside the thinking
    if c:
        return c[-1], "thinking"
    return "look", "fallback"
