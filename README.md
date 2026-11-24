# AiPiMazeTest

An arena where small LLM "ants", running on Raspberry Pis, try to solve 2D mazes **blind**.
An ant never sees the map: it is told only the room it stands in and the doors out of it, and it
has to find the Way Out by walking. A separate machine holds the real maze, keeps score, and lets
people watch every step live.

Standard library Python 3.9+ for everything except training (PyTorch, Transformers, PEFT);
the optional TOML settings files need Python 3.11+.

## The four parts

| Part | Code | What it does |
|---|---|---|
| **Map keeper** | `arena/` (`python3 -m arena serve`) | Holds the whole maze, the run's rulebook and every run's event log. Hands each contestant only its current room. Admins can watch, talk into the game, edit the maze under rules, or stop a run. |
| **Colosseum viewer** | `arena/viewer.py` + `viewer/index.html` | A read-only web relay and page: the maze as a flat grid, the ant's trail, its spoken lines, distance to the exit over time. It signs keeper calls itself so the admin key never reaches a browser. |
| **The house** | `runner/house.py` | The player's whole interface: the room it stands in and that room's doors (alphabetical), plus an optional notebook the keeper stores. Nothing about the maze's shape, by design: see *The blind interface* below. It imports nothing from this project, so a player machine needs only this file. |
| **The LLMs** | `ant/`, `play_llm.py` | The ants and the harness that drives them (below). |

### The ants (`ant/`)

- `personas.py` – three ants sharing one model, each with its own strategy and voice:
  **Mooore** (bossy, always takes the last new door), **Harry** (a worrier who sometimes loses its
  nerve and steps back) and **Curlee** (chaos: random doors, barges into known rooms).
- `lessons.py`, `compact.py` – a strategy bot plays thousands of mazes and every move becomes a
  training example (screen → short reason + `GO:` line). `compact.py` is the faster v2 format:
  numbered doors, the move first, a comment only at interesting moments.
- `train.py` – LoRA fine-tune of [Qwen/Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B) on those
  lessons (one GPU is plenty).
- `score.py` – plays held-out mazes and compares the model with the bot.
- `pi_runner.py` – plays one live run through the keeper on a Pi, using a local `llama-server`,
  and posts each turn to the keeper so the viewer can show what the ant said.
- `notes.py` – the ant's memory. The house tells the ant nothing but its current room, so the ant
  writes every room's doors and every move into its notebook on the keeper, reads its pages back
  each turn and works out from them alone which doors it has tried. The viewer shows the notebook.

`play_llm.py` is the general harness for a larger model behind `llama-server` (any OpenAI-style
chat endpoint): it plays the offline game or, with `--house`, a keeper run.

**Model.** The quantised Pi ant model (a Q4_0 `.gguf`) will be published on this repo's GitHub
Releases page, not in the repository itself. It is a fine-tune of Qwen/Qwen3-0.6B, which is
licensed Apache-2.0.

### The blind interface (hard rule)

A player, whether an ant or a larger model, sees the maze only through `runner/house.py` and the
keeper's contestant port. That interface lists the current room and the doors out of it, each door
labelled with the room it leads to, in alphabetical order, plus what the room's walls are made of and
anything written on them. Nothing else: no size, shape, coordinates,
directions, distances, room counts, shortest route or ready-made notes. Everything a player remembers
is in its own context or in its notebook. The keeper stores the notebook so the Colosseum can watch it,
but the notebook is plain paper: it returns only what the player wrote and treats any text the same.

### The pen and the walls

The player writes with one pen, in its notebook and on walls (`write on wall <text>` in `house.py`,
`POST /v1/wall`; a wall line costs a turn, the notebook never does). Three things decide how a line
comes out, and the player is told none of them: the pen's **ink**, its **tip** condition, and the
**surface**. Paper and wood take ink cleanly; brick and rough stone use more ink and wear the tip
(a worn tip drops letters, shown as `_`); damp stone smears; ice, glass, metal and tile take nothing.
A nearly dry pen writes `(faint) ...`, a dry or broken one writes nothing, in the notebook too. The
player sees the material ("The walls are brick.") and the writing as it came out, never what the
material does or how much ink is left; the keeper gives the same reply to every write. The keeper
logs the true numbers (surface, ink, tip, what was meant, what came out) for the Colosseum only.

Defaults leave runs as they were: every wall is wood and a full pen (200,000 letters on paper) easily
outlasts a run's notebook. Rulebook settings (see `arena/pen.py` for the surface table):

| Setting | Default | Meaning |
|---|---|---|
| `surfaces` | `{}` | share of rooms per material, e.g. `{"brick":0.2,"damp_stone":0.1,"ice":0.05}`; the rest are wood; scattered by seed, never by location |
| `pen_ink` | `200000` | a full pen, in letters on paper |
| `wall_ink_per_letter` | `10` | ink per letter on a wall (big letters), times the material's own factor (brick 1.5) |
| `wear` | see `pen.py` | tip lost per letter per material; overrides merge into the defaults |
`house.py` imports nothing from this project, so a player's machine needs only that one file (and
should hold nothing else from this repository). `tests/test_arena.py` checks all of this.

## Ports

| Port | Who reaches it | Credential |
|---|---|---|
| 7341 contestant | the ant's machine | the run's token (`Authorization: Bearer`); sees one room of one run |
| 7342 admin | your own machines only | admin key: every request HMAC-signed, valid 60 s, single use |
| 7343 harness | the ant's machine | harness key: may only post turns to `/runs/<id>/turn` |
| 7344 viewer | anyone you give the viewer key | viewer key as `?key=…`; read-only |

Anything wrong (unknown path, bad key, wrong port for a key) gets the same plain 404. Every port
binds to `127.0.0.1` by default; see [docs/setup.md](docs/setup.md) to open them to a LAN.

## Quick start (one machine, no GPU, fake LLM)

Run from the repository folder. Each long-running command goes in its own terminal.

```bash
python3 -m arena keys --data hypermaze-keeper-data
```

Add a viewer key (the keeper only makes the admin and harness keys):

```bash
python3 -c "import json,secrets;p='hypermaze-keeper-data/keys.json';k=json.load(open(p));k.setdefault('viewer',secrets.token_hex(16));json.dump(k,open(p,'w'),indent=1)"
```

Start the keeper, the viewer and a stand-in LLM:

```bash
python3 -m arena serve --data hypermaze-keeper-data
```

```bash
python3 -m arena.viewer --keys hypermaze-keeper-data/keys.json
```

```bash
python3 tests/fake_llm.py 8099
```

Start a run (prints the run id and the contestant's token):

```bash
tools/arena.py --keys hypermaze-keeper-data/keys.json new --dims 2 --size 8 --seed 7 --rule max_moves=200
```

Play it with the fake LLM (it answers like a sloppy model, so expect a long walk):

```bash
HOUSE_TOKEN=<token> HARNESS_KEY=$(python3 -c "import json;print(json.load(open('hypermaze-keeper-data/keys.json'))['harness'])") \
  ./play_llm.py --url http://127.0.0.1:8099/v1/chat/completions --mode notebook \
  --house http://127.0.0.1:7341 --report http://127.0.0.1:7343 --run <run id>
```

Watch it at `http://127.0.0.1:7344/?key=<viewer key>` (the `viewer` entry in `keys.json`).

The fake LLM speaks the general harness's format only; `ant.pi_runner` needs the trained ant model.
With that model and `llama-server` built, `scripts/run-ant.sh` does a whole ant run:

```bash
MODEL=/path/to/ant-Q4_0.gguf KEYS_FILE=hypermaze-keeper-data/keys.json scripts/run-ant.sh Mooore 2 8 7
```

## Across machines

Keeper on one machine, ants on Pis, viewer anywhere. With the keeper at `<keeper-host>`:

```bash
# on the keeper (open the contestant and harness ports to the LAN; keep admin local)
python3 -m arena serve --data hypermaze-keeper-data --contestant 0.0.0.0:7341 --harness 0.0.0.0:7343
# from a machine that can reach the admin port
tools/arena.py --admin http://127.0.0.1:7342 new --dims 2 --size 8 --rule max_moves=200
# on the Pi
HOUSE_TOKEN=<token> HARNESS_KEY=<harness key> python3 -m ant.pi_runner --persona Harry \
  --house http://<keeper-host>:7341 --report http://<keeper-host>:7343 --run <run id> \
  --llm http://127.0.0.1:8095
```

Full setup, including building `llama.cpp` on a Pi and making your own keys and TLS
certificates: [docs/setup.md](docs/setup.md).

## Settings files and `scripts/hm`

Every entry point takes `--config PATH` (default `~/hypermaze/config/<component>.toml` if it exists):
`python3 -m arena serve` (keeper), `python3 -m arena.viewer` (viewer), `runner/house.py` (runner),
`python3 -m ant.pi_runner` and `python3 -m ant.llm_server` (ant). Commented examples are in
[`config/`](config). Precedence: **command-line flag > environment variable > settings file >
built-in default**; `HOUSE_TOKEN`, `HOUSE_SERVER`, `HARNESS_KEY`, `SSL_CERT_FILE`, `ARENA_ADMIN_KEY`
and `ARENA_VIEWER_KEY` work as before. Settings files never hold secrets, only paths to files
that do (`keys_file`, `token_file`, `harness_key_file`, `ca_cert`).

`scripts/hm` sets a machine up with systemd **user** services:

```bash
scripts/hm install keeper        # clone/pull to ~/hypermaze/app, copy the example settings if missing, write the unit
scripts/hm config edit keeper    # edit ~/hypermaze/config/keeper.toml, then check it
scripts/hm start keeper          # also: stop, restart, status, logs
scripts/hm update                # git pull and restart installed services
scripts/hm config                # show every settings file, key/token-like values masked
```

`hm install ant` writes `hypermaze-ant.service` (plays the run named in `run_file`/`token_file`) and
`hypermaze-llm.service` (llama-server, started by each run and stopped after `idle_minutes`, default 5,
with no work). `--dry-run` shows what any command would do.
Details: [docs/setup.md](docs/setup.md#6-settings-files-and-services-hm).

**Ant machines get only the ant.** Instead of `hm install ant` on a Pi, run on a machine with the full
checkout:

```bash
scripts/hm deploy ant <ssh host>   # push only the ant's files (no installer, no git, nothing of the keeper
                                   # or Colosseum), write its three units, (re)start its agent
```

It refuses while that ant is playing, and keeps the previous app folder as `~/hypermaze/app.prev-<time>`.
`hypermaze-ant-agent.service` (starts at boot) asks the keeper's harness port every `poll_seconds`
(default 5) whether a run is waiting for `agent_name` (default: the host name), and if so writes
`run_file`/`token_file` and starts `hypermaze-ant`.

## Starting matches from the Colosseum

With `allow_new_matches = true` in `viewer.toml`, the Colosseum shows **New match**: size, loops, seed
(blank = random), max moves, generator, and the ants whose agents have checked in. The keeper makes one
run per ant on the same maze (`POST /admin/matches`) and keeps each run's token, in memory only, until
that ant's agent asks for it; the agent gets the run id and token and nothing else. A run nobody picks
up within 10 minutes is called off.

## Admin tools

```bash
tools/arena.py runs                                  # list runs
tools/arena.py feed <run id>                         # follow live events
tools/arena.py say <run id> "A cold draft blows through."
tools/arena.py edit <run id> '{"type":"swap_rooms","a":"Coral Hall","b":"Iron Den"}' --why "test"
tools/arena.py map <run id> -o maze.json
tools/arena.py stop <run id> "The lights go out."
tools/race.py --size 10 --seed 777 --pace 0.5      # practice race: the teacher bots on one maze
```

`tools/race.py` starts one run per ant on the same maze and plays them at the same time with the
strategy bots the ants learned from, so the Colosseum has a race to show without Pis or models.
It reads the maze from its seed, so it is a test and show tool, never a contestant. A real ant can
join the same race: start it on the same size, seed and loops within 20 minutes and the Colosseum
puts them in one match.

Edits: `remove_door`, `add_door`, `swap_rooms`, `shift_block`, `rename_room`, `message`. The keeper
always refuses an edit that changes the room the player stands in or the doors it shows, moves or
renames the Way Out, or cuts any room off from it. The rulebook fixed at the start of a run
(its SHA-256 fingerprint is logged) can limit more: `allowed_edits`, `seen_rooms`, `max_edits`,
`min_turns_between_edits`, `editors`, `max_moves`, `generator` (`dfs`, `wilson`, `prim`), and the
pen and wall settings above.

## Offline play and the Python API

```bash
./maze.py --seed 7 --notes          # play a 2D 8x8 maze yourself
./play_llm.py --mode notes -- --dims 2 --size 8 --seed 7   # a model on 127.0.0.1:8080 plays it
```

```python
from hypermaze import Maze, Game
m = Maze(dims=2, size=8, loops=0.0, seed=7)
m.to_dict(); m.shortest_path(); m.dist[room]
g = Game(m, mode="notes"); g.intro(); g.send("Coral Study")
```

Other tools: `tools/export.py` (maze to JSON), `tools/solve.py` (print the solution),
`tools/log2json.py` (run log to viewer JSON), `tools/inject.py` (write into a running offline
game's inbox). Plugins: `maze.py --plugin plugins/example_signposts.py`.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## License

MIT, see [LICENSE](LICENSE). The base model Qwen/Qwen3-0.6B is Apache-2.0.
