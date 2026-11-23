#!/usr/bin/env python3
"""Let the local Qwen (llama-server) play the text maze.
Usage: ./play_llm.py [--max-moves 200] [--keep 60] -- [maze options, e.g. --dims 2 --size 8 --seed 7]
Plays the Python maze.py by default; --game <program> plays any other build of the same text game.
Move picking: uses the last "GO: ..." in the visible reply (else in the thinking), takes the text
after the last "GO:" on that line, skips the prompt's echoed "GO: <command or room name>",
and never sends reasoning: if nothing usable is found it sends "look", which costs no move.
Against a keeper (arena): --house http://<keeper>:7341 plays runner/house.py instead; give the run's token
in $HOUSE_TOKEN. The keeper's rulebook sets any move limit, so --max-moves is not passed on.
  --report http://<keeper>:7343 --run <run_id> posts each turn to the keeper's harness port, signed with
  the key in $HARNESS_KEY (never passed to the game).
Watch it:  tail -f maze.log        (moves, hidden coordinates, distance to goal)
           tail -f llm.log         (what the model said each turn)
           tail -f speed.log       (tokens per second each turn)
"""
import argparse, json, os, re, shlex, subprocess, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hypermaze.harness import pick_move

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://127.0.0.1:8080/v1/chat/completions")
ap.add_argument("--max-moves", type=int, default=200)
ap.add_argument("--keep", type=int, default=40, help="recent turns kept in context")
ap.add_argument("--llm-log", default="llm.log")
ap.add_argument("--speed-log", default="speed.log")
ap.add_argument("--game", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "maze.py"),
                help="game program (a .py file runs with this Python; a command line is split like a shell)")
ap.add_argument("--max-unusable", type=int, default=50, help="stop after this many replies in a row with no usable move")
ap.add_argument("--mode", choices=["notes", "notebook", "none"], default="notes", help="notes: game keeps notes; notebook: model writes its own; none: memory only")
ap.add_argument("--house", help="keeper contestant URL: play runner/house.py (token in $HOUSE_TOKEN)")
ap.add_argument("--report", help="keeper harness URL: post each turn there (key in $HARNESS_KEY)")
ap.add_argument("--run", help="run id for --report")
ap.add_argument("maze_args", nargs="*")
a = ap.parse_args()
if a.report and not (a.run and os.environ.get("HARNESS_KEY")):
    ap.error("--report needs --run and $HARNESS_KEY")

mode_flags = {"notes": ["--notes"], "notebook": ["--notebook"], "none": []}[a.mode]
child_env = {k: v for k, v in os.environ.items() if k != "HARNESS_KEY"}
if a.house:
    house = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runner", "house.py")
    game_argv = [sys.executable, house, "--server", a.house] + mode_flags + a.maze_args
else:
    game_cmd = [sys.executable, a.game] if a.game.endswith(".py") else shlex.split(a.game)
    game_argv = game_cmd + ["--max-moves", str(a.max_moves)] + mode_flags + a.maze_args
game = subprocess.Popen(game_argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=0, env=child_env)


def report(**turn):
    """Post one turn to the keeper's harness port; a failure is noted and play goes on."""
    if not a.report:
        return
    from arena.auth import sign
    path = f"/runs/{a.run}/turn"
    body = json.dumps(turn).encode()
    ts, sig = sign(os.environ["HARNESS_KEY"], "POST", path, body)
    req = urllib.request.Request(a.report.rstrip("/") + path, body,
                                 {"Content-Type": "application/json", "X-Time": ts, "X-Signature": sig})
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except OSError as e:
        print(f"[report failed: {e}]", file=sys.stderr, flush=True)

def read_screen():
    buf = ""
    while True:
        ch = game.stdout.read(1)
        if ch == "":
            return buf, True
        buf += ch
        if buf.endswith("\n> "):
            return buf[:-2].strip(), False

BASE = ("You are exploring a house of rooms, trying to reach the room called 'Way Out'. "
        "Each room has a fixed name. The Paths line lists the rooms you can walk to directly. ")
END = " Think briefly, then end your reply with one line exactly like:  GO: <command or room name>"
MODES = {
 "notes": ("You do not need to remember where you have been: the game keeps notes for you, in two ways. "
           "1) The Notes line under each room shows which paths from this room you have not tried yet, whether each leads to a new "
           "or already visited room, and which room you first came here from. 2) Type  map  to list every visited room that still has "
           "untried paths, or  map all  for every visited room with its tried and untried paths. Looking at the map does not cost a move. "
           "A good strategy: prefer untried paths to new rooms; when a room has none left, walk back toward the room you first came from "
           "until you reach a room with untried paths."),
 "notebook": ("Your mind is foggy: your memory of this conversation is short and older turns will be forgotten. "
           "But you carry a graph-paper notebook and a pencil, and the notebook does not forget. "
           "It has a fixed number of pages and you can only see one page at a time. "
           "Commands, none of which cost a move:  read  (the open page),  read page <n>,  next,  back,  "
           "write <text>  (adds it on the open page),  erase <line>,  tab <label>  (sticks a labelled tab on the open page),  tabs  (lists your tabs). "
           "You may want to keep page 1 as a contents page and tab the pages you use. How you use the notebook is up to you."),
 "none": "Remember where you have been and avoid going round in circles.",
}
SYSTEM = BASE + MODES[a.mode] + END


def ask(messages):
    body = json.dumps({"messages": messages, "temperature": 0.6, "max_tokens": 1500}).encode()
    req = urllib.request.Request(a.url, body, {"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        j = json.load(r)
    msg = j["choices"][0]["message"]
    return (msg.get("content") or ""), (msg.get("reasoning_content") or ""), j.get("timings", {}), j.get("usage", {}), time.time() - t0

history = []
log = open(a.llm_log, "w", buffering=1)
speed = open(a.speed_log, "w", buffering=1)
speed.write("#turn\tprompt_tok\tprompt_tok_s\tgen_tok\tgen_tok_s\twall_s\tavg_gen_tok_s\n")
tot_gen = tot_gen_ms = 0.0
screen, done = read_screen()
turn = 0
unusable = 0
while not done:
    turn += 1
    history.append({"role": "user", "content": screen})
    if len(history) > a.keep:          # trim in big chunks so the server's prompt cache stays valid
        history = history[-(a.keep // 2):]
        if history[0]["role"] != "user": history = history[1:]
    msgs = [{"role": "system", "content": SYSTEM}] + history
    reply, reasoning, tm, usage, wall = ask(msgs)
    pn, pps = tm.get("prompt_n", usage.get("prompt_tokens", 0)), tm.get("prompt_per_second", 0)
    gn, gps = tm.get("predicted_n", usage.get("completion_tokens", 0)), tm.get("predicted_per_second", 0)
    tot_gen += gn; tot_gen_ms += tm.get("predicted_ms", 0)
    avg = tot_gen / (tot_gen_ms / 1000) if tot_gen_ms else 0
    speed.write(f"{turn}\t{pn}\t{pps:.1f}\t{gn}\t{gps:.1f}\t{wall:.2f}\t{avg:.1f}\n")
    reply_vis = re.sub(r"<think>.*?</think>", "", reply, flags=re.S).strip()
    choice, where = pick_move(reply_vis, reasoning, reply)
    if where == "thinking":
        reply_vis = (reply_vis + "\n" if reply_vis else "") + "[from thinking] GO: " + choice
    elif where == "fallback":
        reply_vis = (reply_vis + "\n" if reply_vis else "") + "[harness] no usable GO line; sent look"
    unusable = unusable + 1 if where == "fallback" else 0
    history.append({"role": "assistant", "content": "GO: " + choice})
    log.write(f"--- turn {turn}  ({gn} tok @ {gps:.1f} tok/s)\n{screen}\n>>> {reply_vis}\n")
    print(f"[{turn}] {choice}", flush=True)
    report(turn=turn, sent=choice, reply=reply_vis, where=where, screen=screen,
           speed={"prompt_tok": pn, "prompt_tok_s": pps, "gen_tok": gn, "gen_tok_s": gps, "wall_s": round(wall, 2)})
    game.stdin.write(choice + "\n"); game.stdin.flush()
    screen, done = read_screen()
    if not done and a.max_unusable and unusable >= a.max_unusable:
        log.write(f"[harness] {unusable} replies in a row with no usable move; stopping\n")
        game.stdin.write("quit\n"); game.stdin.flush()
        screen, done = read_screen()
        break
print(screen)
log.write(screen + "\n")
speed.write(f"# done turns={turn} avg_gen_tok_s={avg:.1f}\n")
