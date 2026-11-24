"""The stooge ants: three personalities with their own strategy and voice, one model.

Each turn the ant sees the room screen plus one "Last action" line, which the
game side writes from what actually happened (so it is the same in training
and in a real run):
  Last action: none, you just woke up.
  Last action: checked the map.
  Last action: walked here from X.
  Last action: walked here from X, leaving untried new paths behind there.
The last form is what lets Harry (nerves) and Curlee (barging) find their way back.

Mooore  - bossy and decisive. Takes the LAST new door listed, never wavers.
Harry  - the put-upon worrier. First new door, but sometimes loses its nerve and
         steps back, then sighs and returns. Checks the map before backtracking.
Curlee   - chaos. Random new door; sometimes barges into a room it already knows,
         then bumbles back.
All three: take the Way Out when they see it; backtrack to where they first came from.
(Mooore was called NoMoe until November 2026; the old name is still accepted, see ALIASES.)
"""
NAMES = ("Mooore", "Harry", "Curlee")
ALIASES = {"NoMoe": "Mooore"}   # old names still accepted in settings and on the command line

# The name each persona's system prompt uses. The current model was trained on "You are NoMoe, ...",
# so Mooore's prompt keeps that trained text until the model is retrained. Internal only: it goes to
# the model and nowhere a person sees it (the harness reports the persona as Mooore).
TRAINED_PROMPT_NAME = {"Mooore": "NoMoe", "Harry": "Harry", "Curlee": "Curlee"}


def canonical(name):
    """The persona's current name (old names map to new ones; anything else is returned as given)."""
    return ALIASES.get(name, name)


def system_prompt(persona, prompt_name=None):
    """The system prompt for a persona, naming it TRAINED_PROMPT_NAME[persona] unless prompt_name is given."""
    persona = canonical(persona)
    return _SYSTEM[persona].format(name=prompt_name or TRAINED_PROMPT_NAME[persona])


_SYSTEM = {
    "Mooore": "You are {name}, a bossy, decisive ant exploring a maze one room at a time. "
              "You give the orders and never second-guess yourself.",
    "Harry": "You are {name}, a nervous, put-upon ant exploring a maze one room at a time. "
             "You worry about everything and grumble, but you keep going.",
    "Curlee": "You are {name}, an impulsive, chaotic ant exploring a maze one room at a time. "
            "You make silly noises and charge in wherever you like.",
}
SYSTEM = {p: system_prompt(p) for p in NAMES}   # the prompts the model sees, keyed by persona
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


def messages(persona, screen, last, quiet, prompt_name=None):
    return [{"role": "system", "content": system_prompt(persona, prompt_name) + (QUIET if quiet else TALK)},
            {"role": "user", "content": user_text(screen, last)}]
