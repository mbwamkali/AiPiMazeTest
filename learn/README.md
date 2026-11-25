# Learning track: build an ant brain from scratch

Small steps, each one a short Python file you can read top to bottom, run, change and run again.
Every step plays the same fair maze (the screen a person sees in `runner/house.py`) and ends with
"your model against the teacher bot" on mazes it has never seen.

| Step | File | What you learn |
|---|---|---|
| 0 | `lesson0_data.py` | Turning games into training data: examples, features, labels, train/test split |
| 1 | (next) | Your first model by hand in NumPy: weights, loss, gradient descent |
| 2 | | The same in PyTorch: tensors, autograd, optimiser, training loop |
| 3 | | A small neural network: hidden layers, overfitting, validation |
| 4 | | Constraining it: legal moves only, size limits, 8-bit weights, running on a Pi |
| 5 | | Memory: a recurrent network over recent moves |
| 6 | | Text: a tokenizer and a tiny transformer |
| 7 | | Learning from the score alone: reinforcement learning |

## Lesson 0 in one page

**The idea.** A model learns from examples. Here an example is: *the ant saw this screen, and the good
move was that.* The "good move" comes from a teacher: a hand-written bot that plays fairly, using only
what is on the screen and in its notebook.

**Run it.**

```bash
python3 learn/lesson0_data.py --mazes 50 --show 2
```

**What it does.**

1. **Plays games.** The teacher plays relays of 1 to 3 ants on 2D mazes, with the agreed setup: its notebook
   holds only what it writes, its notes about a room are shown when it walks in, and it remembers the last 10
   rooms. At every turn we save the screen, the commands it was allowed to type (door names, `look`,
   `write <note>`, sometimes `write on wall ...`) and what it typed. About half its turns are writing notes.
2. **Makes features.** A model can't read a screen the way you do, so we turn each possible move into a few
   numbers read off the text: is this door the Way Out? Is it the room I was just in? Is it in my memory,
   and how long ago? Do my notes say I already went that way? Did someone write "out <door>" on the wall?
3. **Labels.** For each possible move, label 1 if it is what the teacher typed, else 0.
4. **Splits by maze.** Test mazes are different mazes, never just later turns of the same ones (those look
   too alike and would make a model look better than it is).

**Things to notice in the output.**

- About 70% of the rows are "not the teacher's move". A model that always says "no" is 70% right and plays
  terribly. Being right per row is not the goal; getting out of the maze is. We will always score by games.
- Look at a screen and the teacher's move, and ask: *could I have known that from the screen?* If not, no
  model can learn it either. (That is exactly why an earlier ant failed.)
- The features are a choice. Change `FEATURES()` in the file and the data changes with it; the lesson format
  is expected to change as we go.

**Files** go to `learn/data/` (not saved in git): `train.jsonl` / `test.jsonl` (one turn per line, readable)
and `train_rows.csv` / `test_rows.csv` (one row per possible move: features plus the 0/1 label). Open the
CSV in a spreadsheet to look at it.

**Try.** Make `--mazes` bigger or smaller and see how the counts change. Add a feature (say, "the door's name
is in a note on the wall at all") and look at how often it is 1 for the teacher's move.

## Lesson 1 in one page: your first model, by hand

**The idea.** Give every possible move a score, `score = w · x + b`: each feature `x` times a weight `w`, added
up, plus a bias `b`. Play the move with the highest score. Training finds weights that make the teacher's
moves score highest. That is *logistic regression*, the simplest real model there is.

**Run it** (after lesson 0 has made `learn/data/`):

```bash
python3 learn/lesson0_data.py --mazes 40
python3 learn/lesson1_logreg.py --play 12
```

**The pieces, all in about 40 lines of NumPy:**

- **Sigmoid** turns a score into a probability: `p = 1 / (1 + e^-score)`.
- **Loss** (how wrong it is): binary cross-entropy, averaged over all rows.
- **Gradient** (which way is downhill): for this model, `(p - y) · x` averaged. You can see it in the code.
- **Gradient descent**: take a small step (`--lr`) against the gradient, repeat (`--epochs`). Watch the loss fall.

**What a first run shows (40 training mazes, 12 test mazes):**

| | On paper | Playing new mazes |
|---|---|---|
| Model | top move = teacher's on 94.5% of test turns | everyone out in 42% of games, 626 moves per ant |
| Teacher | — | everyone out in 100%, 195 moves per ant |

**The big lesson: 95% right per turn is not 95% good.** A game is hundreds of turns. A few wrong turns in the
wrong places (skipping a note, walking back into a loop) compound until the ant wanders. Always judge a model
by playing, never only by its score on paper.

**Read its weights.** The model prints one weight per feature: you can see exactly what it learned. For example
`is_look` is strongly negative (the teacher never types "look"), `is_write` strongly positive (it writes a note
after every move). One looks wrong: `is_way_out` comes out *negative*. Why? When the Way Out is in sight the
teacher often writes its note *first*, so in those rows the Way Out door is "not the move". A score that just
adds features can't say "write first, then take the Way Out". That kind of *interaction* is exactly what a small
neural network (lesson 3) can learn.

**Also notice:** some features are the same for every move on a turn (`already_noted`, `n_doors`). They can't help
choose between moves, only shift all the scores together. Good features differ between the moves.

**Try.** Change `--lr` (0.05, 0.5, 5) and watch the loss: too small crawls, too big jumps around. Add a feature in
lesson 0 (for example "this door is written in a ROUTE note here") and see whether play improves.
