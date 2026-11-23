#!/usr/bin/env python3
"""Admin command line for the keeper (arena). Every request is signed with the admin key.

  tools/arena.py runs                                  list runs
  tools/arena.py new [--dims 2 --size 8 --loops 0 --seed N] [--rule max_moves=200 --rule seen_rooms=forbidden ...]
                                                       start a run: prints run id and the runner's token
  tools/arena.py state RUN                             position, moves, edits used...
  tools/arena.py map RUN [-o maze.json]                the whole maze as it stands (Colosseum shape)
  tools/arena.py edit RUN '{"type":"swap_rooms","a":"Coral Hall","b":"Iron Den"}' [--why "..."]
  tools/arena.py preview RUN '<edit json>'             check an edit without applying it
  tools/arena.py say RUN "A cold draft blows through." an in-world message on the player's next screen
  tools/arena.py stop RUN ["farewell text"]
  tools/arena.py feed RUN [--since 0]                  follow live events (Ctrl-C to stop)
  tools/arena.py log RUN                               every event so far (JSON lines)

Where:  --admin URL (or $ARENA_ADMIN, default http://127.0.0.1:7342)
Key:    $ARENA_ADMIN_KEY, else "admin" in --keys (default ~/hypermaze-keeper-data/keys.json)
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from arena.auth import sign  # noqa: E402


def rule_value(v):
    try:
        return json.loads(v)
    except ValueError:
        return v


class Admin:
    def __init__(self, url, key):
        self.url, self.key = url.rstrip("/"), key

    def call(self, method, path, body=None, raw=False):
        b = json.dumps(body).encode() if body is not None else b""
        ts, sig = sign(self.key, method, path, b)
        req = urllib.request.Request(self.url + path, b if method == "POST" else None,
                                     {"X-Time": ts, "X-Signature": sig, "Content-Type": "application/json"}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
        except urllib.error.HTTPError as e:
            data = e.read()
            if e.code == 404:
                sys.exit("not found (wrong run id, wrong key, or clock more than 60 s off)")
        return data.decode() if raw else json.loads(data)

    def feed(self, path):
        ts, sig = sign(self.key, "GET", path)
        sep = "&" if "?" in path else "?"
        with urllib.request.urlopen(f"{self.url}{path}{sep}ts={ts}&sig={sig}", timeout=None) as r:
            for line in r:
                line = line.decode().rstrip("\n")
                if line.startswith("data: "):
                    ev = json.loads(line[6:])
                    yield ev


def show_event(ev):
    t = ev["t"]
    if t == "walk":
        return f"#{ev['seq']} move {ev['n']}: -> {ev['to_name']} {ev['coord']} dist {ev['dist']} ({ev['result']})"
    if t == "edit":
        return f"#{ev['seq']} EDIT by {ev['by']}: {ev['summary']}" + (f"  ({ev['why']})" if ev.get("why") else "")
    if t == "refused":
        return f"#{ev['seq']} refused edit: {ev['reason']}"
    if t == "harness":
        return f"#{ev['seq']} turn {ev.get('turn')}: sent {ev.get('sent')!r} ({ev.get('where')})"
    if t == "end":
        return f"#{ev['seq']} END {ev['result']} after {ev['moves']} moves (shortest {ev['shortest']}, closest {ev['closest']})"
    return f"#{ev['seq']} {t}"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tools/arena.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--admin", default=os.environ.get("ARENA_ADMIN", "http://127.0.0.1:7342"))
    ap.add_argument("--keys", default=os.path.expanduser("~/hypermaze-keeper-data/keys.json"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("runs")
    n = sub.add_parser("new")
    n.add_argument("--dims", type=int, default=2)
    n.add_argument("--size", type=int, default=8)
    n.add_argument("--loops", type=float, default=0.0)
    n.add_argument("--seed", type=int)
    n.add_argument("--rule", action="append", default=[], help="rulebook setting, key=value (value as JSON if it parses)")
    for c in ("state", "log"):
        sub.add_parser(c).add_argument("run")
    m = sub.add_parser("map")
    m.add_argument("run")
    m.add_argument("-o", "--out")
    for c in ("edit", "preview"):
        e = sub.add_parser(c)
        e.add_argument("run")
        e.add_argument("edit", help="the edit as JSON")
        e.add_argument("--why", default="")
        e.add_argument("--by", default="director")
    s = sub.add_parser("say")
    s.add_argument("run")
    s.add_argument("text")
    s.add_argument("--why", default="")
    st = sub.add_parser("stop")
    st.add_argument("run")
    st.add_argument("farewell", nargs="?")
    f = sub.add_parser("feed")
    f.add_argument("run")
    f.add_argument("--since", type=int, default=0)
    f.add_argument("--json", action="store_true", help="print raw events")
    a = ap.parse_args(argv)

    key = os.environ.get("ARENA_ADMIN_KEY")
    if not key:
        with open(a.keys) as fh:
            key = json.load(fh)["admin"]
    adm = Admin(a.admin, key)
    pr = lambda o: print(json.dumps(o, indent=1))  # noqa: E731

    if a.cmd == "runs":
        for r in adm.call("GET", "/admin/runs")["runs"]:
            s = r["settings"]
            print(f"{r['run_id']}  {s['dims']}D size {s['size']} seed {s['seed']}  moves {r['moves']}  {r['ended'] or 'playing'}")
    elif a.cmd == "new":
        rb = {}
        for kv in a.rule:
            k, _, v = kv.partition("=")
            rb[k] = rule_value(v)
        body = {"dims": a.dims, "size": a.size, "loops": a.loops, "rulebook": rb}
        if a.seed is not None:
            body["seed"] = a.seed
        r = adm.call("POST", "/admin/runs", body)
        if "error" in r:
            sys.exit(r["error"])
        print(f"run    {r['run_id']}\nseed   {r['seed']}\nrules  {r['fingerprint'][:16]}…\ntoken  {r['token']}")
        print(f"\nOn the runner's machine:  HOUSE_TOKEN={r['token']} python3 play_llm.py --house http://<keeper>:7341 ...")
    elif a.cmd == "state":
        pr(adm.call("GET", f"/admin/runs/{a.run}/state"))
    elif a.cmd == "log":
        sys.stdout.write(adm.call("GET", f"/admin/runs/{a.run}/log", raw=True))
    elif a.cmd == "map":
        d = adm.call("GET", f"/admin/runs/{a.run}/map")
        if a.out:
            with open(a.out, "w") as fh:
                json.dump(d, fh)
            print(f"wrote {a.out}: {d['rooms_count']} rooms, map version {d['map_version']}")
        else:
            pr(d)
    elif a.cmd in ("edit", "preview"):
        sub_ = "/edits" if a.cmd == "edit" else "/edits/preview"
        pr(adm.call("POST", f"/admin/runs/{a.run}{sub_}", {"edit": json.loads(a.edit), "by": a.by, "why": a.why}))
    elif a.cmd == "say":
        pr(adm.call("POST", f"/admin/runs/{a.run}/edits", {"edit": {"type": "message", "text": a.text}, "why": a.why}))
    elif a.cmd == "stop":
        pr(adm.call("POST", f"/admin/runs/{a.run}/stop", {"farewell": a.farewell}))
    elif a.cmd == "feed":
        try:
            for ev in adm.feed(f"/admin/runs/{a.run}/feed?since={a.since}"):
                print(json.dumps(ev) if a.json else show_event(ev), flush=True)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
