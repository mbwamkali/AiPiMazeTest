"""Matches started from the Colosseum, and handing their runs to the ants' agents.

A match is one run per chosen ant, all on the same maze. Each run's token waits here, in memory only,
until that ant's agent asks for work (harness port: POST /agents/<name>/next). An agent gets a run only
when it says it is free, and gets nothing but the run id and token: no settings, no maze.
A run nobody picks up within EXPIRE seconds is stopped and marked "missed". Tokens are never written
to disk, so a keeper restart forgets waiting runs: they stay in the run list, unstarted.
"""
import re
import secrets
import threading
import time

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
EXPIRE = 600          # seconds a run waits for its ant
LISTENING = 30        # an agent heard from within this many seconds counts as listening
MAX_ANTS = 8
LIMITS = {"size": (2, 60), "loops": (0.0, 0.5), "max_moves": (0, 100000)}


class MatchError(ValueError):
    pass


def check_request(d):
    """Normalise a new-match request from the Colosseum; raises MatchError."""
    if not isinstance(d, dict):
        raise MatchError("bad request")
    out = {}
    try:
        out["size"] = int(d.get("size", 8))
        out["loops"] = float(d.get("loops", 0.0))
        out["max_moves"] = int(d.get("max_moves", 0) or 0)
        seed = d.get("seed")
        out["seed"] = None if seed in (None, "") else int(seed)
    except (TypeError, ValueError):
        raise MatchError("size, loops, seed and max_moves must be numbers")
    if isinstance(d.get("size"), bool) or isinstance(d.get("max_moves"), bool):
        raise MatchError("size and max_moves must be numbers")
    for k, (lo, hi) in LIMITS.items():
        if not lo <= out[k] <= hi:
            raise MatchError(f"{k} must be between {lo} and {hi}")
    if out["seed"] is not None and not 0 <= out["seed"] < 2 ** 32:
        raise MatchError("seed must be between 0 and 4294967295")
    out["generator"] = str(d.get("generator") or "dfs")
    out["team"] = str(d.get("team") or "solo")
    if out["team"] not in ("solo", "together", "relay"):
        raise MatchError("team is solo, together or relay")
    ants = d.get("ants")
    if not isinstance(ants, list) or not 1 <= len(ants) <= MAX_ANTS or \
            not all(isinstance(a, str) and NAME.match(a) for a in ants):
        raise MatchError(f"pick 1 to {MAX_ANTS} ants")
    out["ants"] = list(dict.fromkeys(ants))
    return out


class Dispatch:
    def __init__(self, stop_run=None, clock=time.time, ended=None):
        self.lock = threading.Lock()
        self.agents = {}       # name -> {"seen": t, "busy": bool, "run": run id or None}
        self.queue = []        # {"run_id", "token", "agent", "match", "made", "status", "claimed"}
        self.stop_run = stop_run
        self.clock = clock
        self.ended = ended or (lambda run_id: True)   # has this run ended? (a relay run waits for the one before)

    @staticmethod
    def new_match_id():
        return time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-m" + secrets.token_hex(2)

    def add(self, agent, run_id, token, match_id, after=None):
        """Queue a run for an ant. With after (a run id), the run is handed out only once that run has ended."""
        with self.lock:
            self.queue.append({"run_id": run_id, "token": token, "agent": agent, "match": match_id,
                               "made": self.clock(), "status": "waiting", "claimed": None, "after": after})

    def _ready(self, q, now):
        """A waiting run can be handed out (a relay run once the run before it has ended); its expiry clock
        starts when it becomes ready."""
        if q["after"] and not self.ended(q["after"]):
            q["made"] = now
            return False
        return True

    def _expire(self, now):
        gone = []
        for q in self.queue:
            if q["status"] == "waiting" and self._ready(q, now) and now - q["made"] > EXPIRE:
                q["status"], q["token"] = "missed", None
                gone.append(q["run_id"])
        cutoff = now - 24 * 3600
        self.queue = [q for q in self.queue if q["status"] == "waiting" or (q["claimed"] or q["made"]) > cutoff]
        return gone

    def _stop(self, gone):
        for rid in gone:
            if self.stop_run:
                self.stop_run(rid)

    def take(self, name, busy):
        """An agent asks for work. Returns {"run_id", "token"} or {"run_id": None}."""
        now = self.clock()
        with self.lock:
            a = self.agents.setdefault(name, {"seen": now, "busy": False, "run": None})
            a["seen"], a["busy"] = now, bool(busy)
            gone = self._expire(now)
            got = None
            if not busy:
                got = next((q for q in self.queue if q["agent"] == name and q["status"] == "waiting"
                            and self._ready(q, now)), None)
                if got:
                    got["status"], got["claimed"] = "claimed", now
                    a["run"] = got["run_id"]
                    tok, got["token"] = got["token"], None     # handed over once, then forgotten
        self._stop(gone)
        return {"run_id": got["run_id"], "token": tok} if got else {"run_id": None}

    def view(self):
        """For the Colosseum: agents and the queue, never tokens."""
        now = self.clock()
        with self.lock:
            gone = self._expire(now)
            agents = [{"name": n, "seen_ago": round(now - a["seen"], 1), "listening": now - a["seen"] <= LISTENING,
                       "busy": a["busy"], "run": a["run"]} for n, a in sorted(self.agents.items())]
            queue = [{"run_id": q["run_id"], "agent": q["agent"], "match": q["match"], "status": q["status"],
                      "age": round(now - q["made"], 1)} for q in self.queue]
        self._stop(gone)
        return {"agents": agents, "queue": queue}
