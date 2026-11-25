# Current decisions (kept by Claude from Jared's answers)

Jared answers decision cards in the project thread; Claude records the answers here.
Before each step, pull this branch and follow what is below. Newest first.
Merging to main and deploying to the keeper or the ant machines still need Jared's own typed words.

## Working rules
- Pushing work branches often is fine without asking (2026-11-25). Merges to main and deploys: ask Jared, he types the go.
- Post progress every 2-3 minutes on long runs; check processes (pgrep), not just logs.
- No hostnames, IPs, keys or names in the public repo.

## Contestant screen (human-parity rule)
- The screen equals what a human player sees: room name, wall material, wall notes, doors (alphabetical), others in the room and what they say. Nothing worked out about the maze: no door tags, counts, routes, "untried", "done", "came from here" on a door.
- The maze promises only: a door leads somewhere, and some chain of doors reaches an exit. Way back is not promised. Never tell the player doors are two-way, never explain mechanics (curtain stays closed). Fair runs keep rules fixed; the director may break them in show runs.
- Ant memory is FOGGY: last 10 rooms only ("You came here from X", recent rooms list). Never "this door leads back". The human brings their own memory; the human screen has no memory line by default.
- Notebook holds ONLY what the player writes. No auto-logged moves (2026-11-25).
- Auto-notes switch exists but is OFF by default (easy-mode tests only). FIX NEEDED (2026-11-25, walls-fair 01ae4ad): the fair teacher in ant/relay3.py sets fair_notes = auto-notes, so the notebook writes "A -> B" for it. That breaks "notebook holds only what the player writes". The teacher must WRITE its move notes as its own typed command (e.g. `write A -> B`), and those writes must appear as training targets in lesson 0 data (the model has to learn to choose to write). Lesson 0 data and scoring run with --auto-notes OFF. Re-run the fair-teacher numbers that way.
- Training (2026-11-25, after sloppy-writer test): the teacher writes a note after every move so writing is a habit; some lessons deliberately drop notes so the ant learns to notice gaps and recover (fall back on memory, re-explore) instead of looping.
- "Your notes about this room" (notebook lines that mention it) shown on entry: YES (2026-11-25).
- Teachers: every taught move must be explainable from the screen + memory + own notes.
- Team relays: told only "You are one of N ants" once at start.

## Project goal
- The point is Jared learning to build, train, constrain and run small models in Python, on Pis, in short iterations. Any model type is fair game. Learning plan: lessons 0-9, format may change. Current go: fair screen + teacher bot + explainability test + lesson 0 (games -> training data).
