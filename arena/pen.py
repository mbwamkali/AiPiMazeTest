"""The player's pen, and the surfaces it writes on (standard library only).

The pen carries two hidden numbers: ink (letters' worth of ink) and tip condition (1.0 = new).
Every line the player writes, in the notebook or on a wall, goes through render(), which decides
how it actually comes out:

  clean        as written
  faint        the pen is nearly dry: the line starts with "(faint)" or "(very faint)"
  skipped      a worn tip drops letters: "de_d end -> Ir_n D_n"
  smeared      a damp wall loses letters the same way
  nothing      the surface doesn't take ink (ice, glass, metal, tile), or the pen is dry or broken

The player is told none of this: the keeper says the same thing after every write, and the player
only ever sees the writing as it came out. Ink, tip and surface effects are logged for the Colosseum.

render() is deterministic: the same pen, surface, text and draw number give the same result, so a
keeper restart that replays the run's events rebuilds exactly the same notebook and walls.
"""
import random

PAPER = "paper"
DEFAULT_SURFACE = "wood"

# surface -> (ink multiplier on top of the per-letter cost, what happens to the letters)
#   ink: paper costs 1 per letter; walls cost wall_ink_per_letter x this multiplier (big letters)
#   takes: "clean" | "smear" (each letter lost with probability SMEAR) | "nothing"
SURFACES = {
    PAPER:        (1.0, "clean"),
    "wood":       (1.0, "clean"),
    "plaster":    (1.0, "clean"),
    "brick":      (1.5, "clean"),
    "stone":      (1.5, "clean"),
    "damp_stone": (1.2, "smear"),
    "ice":        (0.25, "nothing"),
    "glass":      (0.25, "nothing"),
    "metal":      (0.25, "nothing"),
    "tile":       (0.25, "nothing"),
}
WALL_SURFACES = tuple(s for s in SURFACES if s != PAPER)

# tip condition lost per letter written on each surface (rulebook "wear" can override any of these)
DEFAULT_WEAR = {
    PAPER: 0.0, "wood": 0.0, "plaster": 0.0,
    "brick": 0.01, "stone": 0.01, "damp_stone": 0.002,
    "ice": 0.0005, "glass": 0.0005, "metal": 0.002, "tile": 0.0005,
}

SMEAR = 0.5          # chance a letter is lost on a smearing surface
SKIP_BELOW = 0.5     # a tip worse than this starts dropping letters
FAINT = 0.05         # below this share of a full pen the writing is faint
VERY_FAINT = 0.01
LOST = "_"


class Pen:
    def __init__(self, ink, wall_ink_per_letter=10, wear=None):
        self.full = float(ink)
        self.ink = float(ink)
        self.tip = 1.0
        self.wall_cost = float(wall_ink_per_letter)
        self.wear = dict(DEFAULT_WEAR)
        self.wear.update(wear or {})
        self.draws = 0       # how many lines it has written (seeds each line's randomness)

    def state(self):
        return {"ink": round(self.ink, 3), "tip": round(self.tip, 4)}

    def render(self, text, surface, seed):
        """Write one line on a surface. Returns (what came out, log dict). Changes ink and tip."""
        mult, takes = SURFACES.get(surface, SURFACES[DEFAULT_SURFACE])
        per_letter = mult * (1.0 if surface == PAPER else self.wall_cost)
        wear = self.wear.get(surface, 0.0)
        rng = random.Random(f"pen:{seed}:{self.draws}")
        self.draws += 1
        before = self.state()
        faint_at = self.ink          # how full the pen was when the line started
        out = []
        for ch in text:
            if ch == " ":
                out.append(ch)
                continue
            if self.ink < per_letter or self.tip <= 0.0:
                break                # dry or broken: the rest of the line never appears
            self.ink -= per_letter
            self.tip = max(0.0, self.tip - wear)
            lost = False
            if takes == "smear" and rng.random() < SMEAR:
                lost = True
            if self.tip < SKIP_BELOW and rng.random() < (SKIP_BELOW - self.tip) / SKIP_BELOW:
                lost = True
            out.append(LOST if lost else ch)
        line = "".join(out).rstrip()
        if takes == "nothing" or not line.replace(LOST, "").strip():
            line = ""
        elif self.full > 0 and faint_at < VERY_FAINT * self.full:
            line = "(very faint) " + line
        elif self.full > 0 and faint_at < FAINT * self.full:
            line = "(faint) " + line
        log = {"surface": surface, "intended": text, "rendered": line,
               "ink_before": before["ink"], "ink_after": round(self.ink, 3),
               "tip_before": before["tip"], "tip_after": round(self.tip, 4)}
        return line, log


def assign_surfaces(n, seed, mix):
    """A wall material for each of n rooms. mix is {surface: share}, the rest is wood.
    Scattered at random by seed: a room's material says nothing about where it is."""
    out = [DEFAULT_SURFACE] * n
    if not mix:
        return out
    rooms = list(range(n))
    random.Random(f"surfaces:{seed}").shuffle(rooms)
    i = 0
    for surface in sorted(mix):
        k = int(round(n * float(mix[surface])))
        for c in rooms[i:i + k]:
            out[c] = surface
        i += k
    return out
