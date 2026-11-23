"""Play one stooge ant blind through the keeper, and show its lines in the Colosseum viewer.

The ant only sees what the keeper hands out (runner/house.py in notes mode). Each screen
becomes the v2 numbered-door view; the "Last action" line is worked out from the screens
alone, the same way as in training. Every turn is posted to the keeper's harness port
(signed with $HARNESS_KEY), which is what the viewer shows as the ant's speech.

    HOUSE_TOKEN=<run token> HARNESS_KEY=<key> python3 -m ant.pi_runner --persona NoMoe \
        --house http://127.0.0.1:7341 --report http://127.0.0.1:7343 --run <run id> \
        --llm http://127.0.0.1:8095

or with everything in ~/hypermaze/config/ant.toml (token_file, run_file, harness_key_file, ...):

    python3 -m ant.pi_runner --config ~/hypermaze/config/ant.toml
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

from . import compact as C
from . import personas as P
from .lessons import room_screen


def ask(llm, msgs, temperature, max_tokens=40):
    body = json.dumps({"messages": msgs, "max_tokens": max_tokens, "temperature": temperature,
                       "chat_template_kwargs": {"enable_thinking": False}}).encode()
    req = urllib.request.Request(llm.rstrip("/") + "/v1/chat/completions", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["choices"][0]["message"]["content"]


def wait_for_llm(llm, seconds=120):
    """Wait until llama-server answers /health (it says 503 while the model loads). Returns True if up."""
    end = time.time() + seconds
    while True:
        try:
            urllib.request.urlopen(llm.rstrip("/") + "/health", timeout=5).read()
            return True
        except urllib.error.HTTPError as e:
            if e.code != 503:
                return True          # something answering that has no /health: go ahead
        except OSError:
            pass
        if time.time() > end:
            return False
        time.sleep(1)


def next_last(prev_screen, prev_last, cmd, new_screen):
    """The 'Last action' after sending cmd, from the two screens only."""
    if cmd == "map":
        return ("map",)
    room0, doors0, untried0, came0, _ = C.parse_screen(prev_screen)
    try:
        room1, _, _, came1, _ = C.parse_screen(new_screen)
    except AttributeError:          # no room screen (the game ended)
        return prev_last
    if room1 == room0:
        return ("invalid",)
    had_new = any(v == "new" for v in untried0.values())
    was_visited = untried0.get(cmd) != "new"      # a tried door, or an untried one to a known room
    flagged_before = bool(prev_last and prev_last[0] == "walk" and prev_last[2])
    left = had_new and was_visited and came1 != room0 and not flagged_before
    return ("walk", room0, left)


def main(argv=None):
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "runner"))
    sys.path.insert(0, os.path.dirname(HERE))
    from hypermaze import config
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", help="settings file (default ~/hypermaze/config/ant.toml if it exists)")
    ap.add_argument("--persona", choices=P.NAMES, help="default NoMoe")
    ap.add_argument("--house", help="keeper contestant URL (default http://127.0.0.1:7341)")
    ap.add_argument("--llm", help="the ant's llama-server (default http://127.0.0.1:8095)")
    ap.add_argument("--report", help="keeper harness URL (key in $HARNESS_KEY or harness_key_file)")
    ap.add_argument("--run", help="run id, for --report (default: first line of run_file)")
    ap.add_argument("--temperature", type=float, help="default 0.7")
    ap.add_argument("--quiet", action="store_true", default=None)
    ap.add_argument("--max-turns", type=int, help="default 600")
    a = ap.parse_args(argv)
    cfg = config.load_or_exit("ant", a.config)
    a.persona = config.pick(a.persona, None, cfg, "persona", "NoMoe")
    a.house = config.pick(a.house, None, cfg, "house", "http://127.0.0.1:7341")
    a.llm = config.pick(a.llm, None, cfg, "llm", "http://127.0.0.1:8095")
    a.report = config.pick(a.report, None, cfg, "report")
    a.temperature = config.pick(a.temperature, None, cfg, "temperature", 0.7)
    a.quiet = config.pick(a.quiet, None, cfg, "quiet", False)
    a.max_turns = config.pick(a.max_turns, None, cfg, "max_turns", 600)
    if a.run is None and cfg.get("run_file"):
        a.run = config.read_secret(cfg["run_file"])
    token = os.environ.get("HOUSE_TOKEN") or (config.read_secret(cfg["token_file"]) if cfg.get("token_file") else "")
    if not token:
        sys.exit("no run token: set HOUSE_TOKEN or token_file in the settings")
    if cfg.get("ca_cert") and not os.environ.get("SSL_CERT_FILE"):
        os.environ["SSL_CERT_FILE"] = cfg["ca_cert"]
    from house import Game, House

    report = None
    if a.report:
        from arena.auth import sign
        key = os.environ.get("HARNESS_KEY") or \
            (config.read_secret(cfg["harness_key_file"]) if cfg.get("harness_key_file") else "")
        if not key or not a.run:
            sys.exit("--report needs a run id (--run or run_file) and $HARNESS_KEY or harness_key_file")

        def report(**turn):
            path = f"/runs/{a.run}/turn"
            body = json.dumps(turn).encode()
            ts, sig = sign(key, "POST", path, body)
            req = urllib.request.Request(a.report.rstrip("/") + path, body,
                                         {"Content-Type": "application/json", "X-Time": ts, "X-Signature": sig})
            try:
                urllib.request.urlopen(req, timeout=10).read()
            except OSError as e:
                print(f"[report failed: {e}]", flush=True)

    if not wait_for_llm(a.llm):
        sys.exit(f"no answer from the ant's llama-server at {a.llm}")
    game = Game(House(a.house, token), notes=True)
    screen = room_screen(game.intro())
    last = None
    t_start = time.time()
    for turn in range(1, a.max_turns + 1):
        if game.done:
            break
        msgs, doors = C.messages(a.persona, screen, last, a.quiet)
        t0 = time.time()
        reply = ask(a.llm, msgs, a.temperature, 16 if a.quiet else 40)
        secs = time.time() - t0
        cmd = C.parse(reply, doors) or "look"
        out = game.send(cmd)
        new_screen = room_screen(out) if "You are in the" in out else out
        last = next_last(screen, last, cmd, new_screen) if cmd != "look" else last
        said = "\n".join(l for l in reply.splitlines() if "GO:" not in l).strip()
        print(f"{turn:4d} {secs:5.2f}s  -> {cmd:22s} {said}", flush=True)
        if report:
            report(turn=turn, sent=cmd, reply=(said or f"GO: {cmd}"), where=a.persona, speed=round(secs, 2))
        screen = new_screen
    el = time.time() - t_start
    print(f"done: result={game.result} moves={game.moves} turns={turn} in {el:.0f}s ({el / max(1, turn):.2f} s/turn)", flush=True)
    return 0 if game.result == "way_out" else 1


if __name__ == "__main__":
    sys.exit(main())
