"""The rulebook: per-run policy the keeper holds itself to.

A rulebook is a small JSON object fixed when a run starts. Its fingerprint (SHA-256 of the
canonical JSON) is logged with the run, so anyone can check afterwards which rules applied.
The invariants in world.py always hold on top of it.
"""
import hashlib
import json

from hypermaze.core import GENERATORS
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
}


class RuleError(ValueError):
    pass


def make(overrides=None):
    """A complete rulebook from defaults plus overrides; unknown keys or bad values raise RuleError."""
    rb = dict(DEFAULTS)
    for k, v in (overrides or {}).items():
        if k not in DEFAULTS:
            raise RuleError(f"unknown rulebook setting {k!r}")
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
