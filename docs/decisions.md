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
- Step counter (Jared card, 2026-11-25 11:51 CT): YES, a helper for everyone (people and ants): the screen shows "Moves so far: N" (the player's own move count). The teacher may use it to decide when marking the way back is worth it; re-run team numbers.
- Notebook holds ONLY what the player writes. No auto-logged moves (2026-11-25).
- Auto-notes switch exists but is OFF by default (easy-mode tests only). Fixed 2026-11-25: the fair teacher types its own notes ("write A -> B", "write ME: ..."); those writes are training targets in lesson 0.
- Training (2026-11-25, after sloppy-writer test): the teacher writes a note after every move so writing is a habit; some lessons deliberately drop notes so the ant learns to notice gaps and recover (fall back on memory, re-explore) instead of looping.
- "Your notes about this room" (notebook lines that mention it) shown on entry: YES (2026-11-25).
- Teachers: every taught move must be explainable from the screen + memory + own notes.
- Team relays: told only "You are one of N ants" once at start.

## Folder tidy on the workstation (Jared card, 2026-11-25 13:33 CT): YES
- After lesson 2 is pushed: make ONE working folder ~/hypermaze-dev holding the repo (move ~/projects/aipimazetest-notebook there, all branches kept).
- Move stale copies into ~/old-maze/ (NOT deleted): ~/projects/aipimazetest, ~/hypermaze-py, ~/hypermaze-text.
- Leave ~/hypermaze (installed app, configs, play script) and ~/ant-work as they are.
- Before moving: check no running process, systemd unit, cron, script (incl. ~/ant-work/*.sh, hm configs, play script) points at a folder being moved; fix references first. List what moved and anything that referenced it.

## Project goal
- The point is Jared learning to build, train, constrain and run small models in Python, on Pis, in short iterations. Any model type is fair game. Learning plan: lessons 0-9, format may change. Fair screen, teacher, explainability test and lesson 0 are done (walls-fair 96ad02f). Lesson 1 done (walls-fair 7750cf3: 94.5% turn match, 42% all out). Step counter done (--moves; mark the way when others x moves >= 500). CURRENT GO (Jared card, 2026-11-25 13:30 CT): LESSON 2 = the lesson 1 model rewritten in PyTorch (tensors, autograd, optimizer, training loop), same data and same game scoring, side by side with lesson 1 so the differences are visible; CPU only; short commented file + explainer section. 
