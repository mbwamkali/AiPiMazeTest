# Hypermaze decisions

Decisions Jared made, newest first. Each says what was decided and why, so later work can be checked
against it. (Design discussion lives in the project thread; this file is the short record.)

## 2026-11-25 — The ant's memory is foggy: about 10 rooms

- An ant remembers only the last **10 rooms** it walked through, the way the house's intro already says:
  "your mind is foggy: you can only hold the last little while in your head". Anything older has to come
  from its notebook.
- It is shown as the ant's own memory, nothing worked out: *"You remember: Coral Hall, Iron Den, …, then
  here (Velvet Loft)"*, oldest first; the oldest drops off as it walks on.
- **The human is just human.** A person playing brings their own memory: some remember more, some need
  reminding what colour their trousers are. The interface adds nothing for them by default; a switch can show
  the same "You remember" line, so a human can play with exactly the ant's crutch.
- Why 10: a round number, about what a person holds; cheap on the Pi (~50 tokens). In bot tests the
  notebook is what makes a 16×16 maze solvable fairly; memory alone (no notes) finishes 29% of games.

## 2026-11-24 — Human-equal interface (the hard rule)

- The ant's screen must be what a human player would get: the room, its doors, the room's description (wall
  material and anything written on the walls), anyone in the same room and what they say. The notebook is a
  helper offered equally to a human.
- No worked-out door tags, counts or routes ("new", "done", "untried", "you first came from here", "rooms
  elsewhere with untried paths", "toward …"). Any helper must exist in the shared interface (house.py or a
  picture view) so a human can switch on the same one.
- The only promise: a door leads to another room, and some chain of doors leads to the Way Out. Never assume
  doors are two-way, nor direction, distance or shape. Door order or position must not hint at direction.
- Helper levels (level for the next ant **not yet chosen**): 0 bare · 1 perception ("you came from room A",
  never "this door leads back") · 2 notebook helper (notes kept for you, "your notes about this room") ·
  3 notebook summary.
- Rules stay consistent within a fair run (training, scoring); the director changing things mid-run is a show
  choice, not a promise. Nothing the player sees, and no lesson, reveals how the maze works.

## 2026-11-24 — Team briefing: team size only

- Each ant is told once, as it comes in, "you are one of N ants" (not its place in line); it writes that in
  its notebook and knows it only from there.

## 2026-11-24 — Scoring a team

- All ants out, total moves (every ant's moves added up, counted when the last one is out). Waiting and
  talking are not moves; writing on a wall is.

## 2026-11-23/24 — Pen and walls

- One pen per ant with hidden ink and tip condition, for the notebook and the walls. Rooms show their wall
  material ("The walls are brick."), never what it does; the player learns by writing. Default: every wall
  wood, full pen. Other materials are difficulty settings in the run's rulebook.
- Ants can talk only when in the same room. Writing on walls costs a turn.

## 2026-11-23 — Project scope and goal

- Mazes are 2D (and later stacked 2D floors). Lessons and scoring use 2D.
- The point of the project (for its whole length, not one day): Jared learning how models are built,
  trained, constrained and used, in Python, starting with models small enough for the Pis.
