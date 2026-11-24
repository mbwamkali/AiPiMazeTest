"""The rulebook: per-run policy the keeper holds itself to.

A rulebook is a small JSON object fixed when a run starts. Its fingerprint (SHA-256 of the
canonical JSON) is logged with the run, so anyone can check afterwards which rules applied.
The invariants in world.py always hold on top of it.
"""
import hashlib
import json

from hypermaze.core import GENERATORS
from .pen import DEFAULT_WEAR, SURFACES, WALL_SURFACES
from .team import TEAM_MODES
from .world import EDIT_TYPES

DEFAULTS = {
    "generator": "dfs",                 # dfs (bit-exact with maze.cpp) | wilson | prim
    "allowed_edits": list(EDIT_TYPES),
    "seen_rooms": "allowed",            # allowed: edits may touch seen rooms (shown on revisit) | forbidden
    "max_edits": None,                  # None = no budget
    "min_turns_between_edits": 0,
    "editors": ["director"],            # who may send edits: director, script, audience
    "max_moves": 0,                     # 0 = no limit
    "rulebook_locked": True,
    # the pen and the walls (see pen.py); the player is never told any of these numbers
    "surfaces": {},                     # {surface: share of rooms}, the rest wood; {} = every wall wood
    "pen_ink": 200000,                  # a full pen, in letters written on paper
    "wall_ink_per_letter": 10,          # a letter on a wall uses this much ink (times the surface's own factor)
    "wear": dict(DEFAULT_WEAR),         # tip condition lost per letter, per surface (overrides merge into this)
    # the runs of one match: solo | together (shared walls, see each other, talk in the same room) | relay
    "team": "solo",
}
PEN_KEYS = ("surfaces", "pen_ink", "wall_ink_per_letter", "wear", "team")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def complete(rb):
    """A stored rulebook with any settings it predates filled in from the defaults (its fingerprint is
    still the stored one: runs made before the pen existed play exactly as they did)."""
    out = dict(rb)
    for k in PEN_KEYS:
        out.setdefault(k, DEFAULTS[k])
    return out


class RuleError(ValueError):
    pass


def make(overrides=None):
    """A complete rulebook from defaults plus overrides; unknown keys or bad values raise RuleError."""
    rb = dict(DEFAULTS)
    for k, v in (overrides or {}).items():
        if k not in DEFAULTS:
            raise RuleError(f"unknown rulebook setting {k!r}")
        if k == "wear" and isinstance(v, dict):
            v = {**DEFAULTS["wear"], **v}
        rb[k] = v
    if rb["generator"] not in GENERATORS:
        raise RuleError(f"generator must be one of {GENERATORS}")
    bad = set(rb["allowed_edits"]) - set(EDIT_TYPES)
    if bad:
        raise RuleError(f"unknown edit types {sorted(bad)}")
    if rb["seen_rooms"] not in ("allowed", "forbidden"):
        raise RuleError("seen_rooms is allowed or forbidden")
    if rb["max_edits"] is not None and (not isinstance(rb["max_edits"], int) or rb["max_edits"] < 0):
        raise RuleError("max_edits is a whole number or null")
    for k in ("min_turns_between_edits", "max_moves"):
        if not isinstance(rb[k], int) or rb[k] < 0:
            raise RuleError(f"{k} is a whole number >= 0")
    mix = rb["surfaces"]
    if not isinstance(mix, dict) or any(s not in WALL_SURFACES or not _num(x) or x < 0 for s, x in mix.items()):
        raise RuleError(f"surfaces is {{surface: share}} with surfaces from {', '.join(WALL_SURFACES)}")
    if sum(mix.values()) > 1.0 + 1e-9:
        raise RuleError("surfaces: the shares add up to more than 1")
    if not _num(rb["pen_ink"]) or rb["pen_ink"] < 0:
        raise RuleError("pen_ink is a number >= 0")
    if not _num(rb["wall_ink_per_letter"]) or rb["wall_ink_per_letter"] < 0:
        raise RuleError("wall_ink_per_letter is a number >= 0")
    if rb["team"] not in TEAM_MODES:
        raise RuleError(f"team is one of {', '.join(TEAM_MODES)}")
    wear = rb["wear"]
    if not isinstance(wear, dict) or any(s not in SURFACES or not _num(x) or not 0 <= x <= 1 for s, x in wear.items()):
        raise RuleError("wear is {surface: tip lost per letter, 0..1}")
    return rb


def fingerprint(rb):
    return hashlib.sha256(json.dumps(rb, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def check(rb, plan, run, by):
    """Policy check for a planned edit. Returns None if allowed, else the reason."""
    t = plan.edit["type"]
    if by not in rb["editors"]:
        return f"{by!r} may not edit in this run (editors: {', '.join(rb['editors'])})"
    if t not in rb["allowed_edits"]:
        return f"{t} is not allowed in this run"
    if rb["max_edits"] is not None and run.edits_used >= rb["max_edits"]:
        return f"edit budget used up ({rb['max_edits']})"
    gap = rb["min_turns_between_edits"]
    if gap and run.last_edit_turn is not None and run.moves - run.last_edit_turn < gap:
        return f"too soon: {gap} turns between edits, last edit at turn {run.last_edit_turn}"
    if rb["seen_rooms"] == "forbidden":
        seen = run.seen
        for a, b in plan.doors_touched:
            if a in seen or b in seen:
                return "touches a door of a room the player has seen"
        w = run.world
        for c in plan.renamed:
            if c in seen or any(x in seen for x in w.adj[c]):
                return "renames a room the player has seen or seen a door to"
    return None
