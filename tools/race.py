#!/usr/bin/env python3
"""Practice race: several ants on the same maze at once, played by the teacher bots.

Starts one keeper run per ant on the same maze (same dims, size, loops and seed), then plays every run
at the same time with the strategy bot each ant was trained on (ant/bots.py), so the Colosseum has
a race to show without any Pis or models. Each move posts the bot's line to the harness port, exactly
like ant/pi_runner.py, so the viewer shows speech and names.

  python3 tools/race.py --size 8 --seed 1234 [--loops 0.1] [--ants NoMoe,Harry,Curlee] [--pace 0.6]

Where:  --admin URL      keeper admin port   (or $ARENA_ADMIN, default http://127.0.0.1:7342)
        --house URL      contestant port     (default http://127.0.0.1:7341)
        --report URL     harness port        (default http://127.0.0.1:7343)
Keys:   "admin" and "harness" from --keys (default ~/hypermaze-keeper-data/keys.json),
        or $ARENA_ADMIN_KEY and $HARNESS_KEY. For an HTTPS keeper set SSL_CERT_FILE to its CA.

The bots read the maze from its seed, so this is a test and show tool only: a real contestant never
gets the seed. Real ants race with scripts/race.sh.
"""
import argparse
import json
import os
import random
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "runner"))
from arena.auth import sign  # noqa: E402
from ant import bots as B  # noqa: E402
from ant import personas as P  # noqa: E402
from hypermaze.core import Maze  # noqa: E402
from hypermaze.game import Game  # noqa: E402


_stamp_lock, _last_stamp = threading.Lock(), {}


def signed(url, key, method, path, body=None):
    # the keeper takes each signature once, and a signature covers (method, path, body, whole second):
    # two identical requests in one second get the second stamped a second later (60 s of slack)
    b = json.dumps(body).encode() if body is not None else b""
    with _stamp_lock:
        k = (method, path, b)
        now = max(int(time.time()), _last_stamp.get(k, -1) + 1)
        _last_stamp[k] = now
    ts, sig = sign(key, method, path, b, ts=now)
    req = urllib.request.Request(url.rstrip("/") + path, b if method == "POST" else None,
                                 {"X-Time": ts, "X-Signature": sig, "Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def walk(house, token, name):
    req = urllib.request.Request(house.rstrip("/") + "/v1/walk", json.dumps({"to": name}).encode(),
                                 {"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def play(persona, run, a, hkey, seed_rng, out):
    rng = random.Random(seed_rng)
    s = run["settings"]
    g = Game(Maze(s["dims"], s["size"], s["loops"], s["seed"], s.get("generator", "dfs")), mode="notes")
    B.room_screen(g.intro())
    last, turn, t0 = None, 0, time.time()
    time.sleep(rng.random() * a.pace)
    while not g.done and turn < a.max_turns:
        turn += 1
        cmd, kind = B.decide(persona, g, last, rng)
        if cmd is None:
            break
        line = B.say(persona, kind, cmd, rng)
        if cmd != "map":
            r = walk(a.house, run["token"], cmd)
            if r.get("ended"):
                g.send(cmd)
                signed(a.report, hkey, "POST", f"/runs/{run['run_id']}/turn",
                       {"turn": turn, "sent": cmd, "reply": line, "where": persona, "speed": a.pace})
                break
        _, last = B.step(g, cmd, last)
        signed(a.report, hkey, "POST", f"/runs/{run['run_id']}/turn",
               {"turn": turn, "sent": cmd, "reply": line, "where": persona, "speed": a.pace})
        time.sleep(a.pace * (0.7 + 0.6 * rng.random()) * (1.6 if kind in ("map", "nerves") else 1))
    out[persona] = (g.moves, round(time.time() - t0, 1), g.result)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tools/race.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--admin", default=os.environ.get("ARENA_ADMIN", "http://127.0.0.1:7342"))
    ap.add_argument("--house", default="http://127.0.0.1:7341")
    ap.add_argument("--report", default="http://127.0.0.1:7343")
    ap.add_argument("--keys", default=os.path.expanduser("~/hypermaze-keeper-data/keys.json"))
    ap.add_argument("--size", type=int, default=8)
    ap.add_argument("--loops", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--ants", default=",".join(P.NAMES), help="comma-separated: NoMoe,Harry,Curlee")
    ap.add_argument("--pace", type=float, default=0.6, help="seconds between moves (default 0.6)")
    ap.add_argument("--max-moves", type=int, default=600)
    a = ap.parse_args(argv)
    a.max_turns = a.max_moves * 2
    ants = [x.strip() for x in a.ants.split(",") if x.strip()]
    bad = [x for x in ants if x not in P.NAMES]
    if bad:
        sys.exit(f"unknown ant(s) {', '.join(bad)}; choose from {', '.join(P.NAMES)}")
    keys = {}
    if os.path.exists(a.keys):
        with open(a.keys) as f:
            keys = json.load(f)
    akey = os.environ.get("ARENA_ADMIN_KEY") or keys.get("admin")
    hkey = os.environ.get("HARNESS_KEY") or keys.get("harness")
    if not akey or not hkey:
        sys.exit("need the admin and harness keys (--keys, or $ARENA_ADMIN_KEY and $HARNESS_KEY)")
    seed = a.seed if a.seed is not None else random.randrange(1, 2**31)
    runs = {}
    for who in ants:
        body = {"dims": 2, "size": a.size, "loops": a.loops, "seed": seed, "rulebook": {"max_moves": a.max_moves}}
        r = signed(a.admin, akey, "POST", "/admin/runs", body)
        if "error" in r:
            sys.exit(r["error"])
        r["settings"] = {"dims": 2, "size": a.size, "loops": a.loops, "seed": seed}
        runs[who] = r
        print(f"{who:7s} run {r['run_id']}")
    print(f"race: 2D size {a.size} seed {seed} loops {a.loops}, {len(ants)} ants, pace {a.pace}s", flush=True)
    out, threads = {}, []
    for k, who in enumerate(ants):
        t = threading.Thread(target=play, args=(who, runs[who], a, hkey, seed + k, out), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    for who in sorted(out, key=lambda w: (out[w][2] != "SOLVED", out[w][0])):
        moves, secs, result = out[who]
        print(f"{who:7s} {result or 'stopped':8s} {moves:4d} moves  {secs:6.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
