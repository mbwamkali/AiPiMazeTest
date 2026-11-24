"""Teams: the runs of one match playing the same maze as one team (standard library only).

A match's rulebook "team" setting decides how its ants play:
  solo      (default) each run is on its own, as before: its own walls, nobody else in the house
  together  all ants are in the house at once. They share the walls (a note one writes, the others
            can read), a room shows who else is standing in it ("here"), and an ant can say a line
            that only the ants in the same room hear, on their next screen.
  relay     ants come in one after another: each run is handed to its ant only once the run before
            it has ended. They share the walls, so a later ant can read what an earlier one wrote.

Each ant keeps its own pen and its own notebook. The team's score is every ant's moves added
together, counted when the last ant is out.
"""
import threading

TEAM_MODES = ("solo", "together", "relay")
MAX_SAY = 200


class Team:
    def __init__(self, match_id, mode):
        self.id = match_id
        self.mode = mode
        self.members = []          # runs, in the order the match made them
        self.marks = {}            # room -> lines on its walls, shared by every member
        self.lock = threading.RLock()

    def add(self, run):
        with self.lock:
            self.members.append(run)
            run.team = self
            run.marks = self.marks

    def rebuild_marks(self, max_marks):
        """After a keeper restart: the shared walls from every member's wall events, in time order."""
        evs = sorted((e for r in self.members for e in r.events if e.get("t") == "wall" and e.get("rendered")),
                     key=lambda e: (e.get("t_wall", 0), e.get("seq", 0)))
        with self.lock:
            self.marks.clear()
            for e in evs:
                ms = self.marks.setdefault(e["room"], [])
                ms.append(e["rendered"])
                del ms[:-max_marks]

    def others_here(self, run):
        """The other members standing in the same room right now (together mode only)."""
        if self.mode != "together":
            return []
        return [r for r in self.members if r is not run and not r.ended and r.cur == run.cur]

    def name_of(self, run):
        return run.settings.get("agent") or f"Ant {self.members.index(run) + 1}"

    def score(self):
        with self.lock:
            members = [{"run_id": r.id, "agent": self.name_of(r), "moves": r.moves, "ended": r.ended}
                       for r in self.members]
        return {"match": self.id, "mode": self.mode, "members": members,
                "all_out": all(m["ended"] == "way_out" for m in members),
                "all_ended": all(m["ended"] for m in members),
                "total_moves": sum(m["moves"] for m in members)}
