"""The stooge ants: three personalities with their own strategy and voice, one model.

Each turn the ant sees the room screen plus one "Last action" line, which the
game side writes from what actually happened (so it is the same in training
and in a real run):
  Last action: none, you just woke up.
  Last action: checked the map.
  Last action: walked here from X.
  Last action: walked here from X, leaving untried new paths behind there.
The last form is what lets Harry (nerves) and Curlee (barging) find their way back.

NoMoe   - bossy and decisive. Takes the LAST new door listed, never wavers.
Harry  - the put-upon worrier. First new door, but sometimes loses its nerve and
         steps back, then sighs and returns. Checks the map before backtracking.
Curlee   - chaos. Random new door; sometimes barges into a room it already knows,
         then bumbles back.
All three: take the Way Out when they see it; backtrack to where they first came from.
"""
NAMES = ("NoMoe", "Harry", "Curlee")

SYSTEM = {
    "NoMoe": "You are NoMoe, a bossy, decisive ant exploring a maze one room at a time. "
            "You give the orders and never second-guess yourself.",
    "Harry": "You are Harry, a nervous, put-upon ant exploring a maze one room at a time. "
             "You worry about everything and grumble, but you keep going.",
    "Curlee": "You are Curlee, an impulsive, chaotic ant exploring a maze one room at a time. "
            "You make silly noises and charge in wherever you like.",
}
TALK = " Read the room screen, say one short sentence in character, then answer GO: <room name or map>."
QUIET = " Read the room screen and answer only GO: <room name or map>."

def last_text(last):
    if last is None:
        return "Last action: none, you just woke up."
    if last[0] == "map":
        return "Last action: checked the map."
    if last[0] == "invalid":
        return "Last action: tried a path that isn't here."
    _, frm, left_new = last
    return f"Last action: walked here from {frm}" + (", leaving untried new paths behind there." if left_new else ".")


def user_text(screen, last):
    return screen + "\n" + last_text(last)


def messages(persona, screen, last, quiet):
    return [{"role": "system", "content": SYSTEM[persona] + (QUIET if quiet else TALK)},
            {"role": "user", "content": user_text(screen, last)}]
