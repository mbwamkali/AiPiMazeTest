"""The ant's agent: asks for the next game every few seconds and starts the ant when one is waiting.

Each ask is a signed POST to <report>/agents/<agent_name>/next saying whether a game is already being
played here. The answer is either nothing, or a game id and its token. The agent writes those to
run_file and token_file (readable only by this user) and starts the hypermaze-ant service, which plays
that one game. It never starts a second game while one is being played.

    python3 -m ant.agent --config ~/hypermaze/config/ant.toml
"""
import argparse
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
ID = re.compile(r"^[0-9A-Za-z-]{1,64}$")
TOKEN = re.compile(r"^[0-9A-Za-z_-]{16,128}$")
ANT_UNIT = "hypermaze-ant"


def busy(unit=ANT_UNIT):
    """True while the ant service is starting, playing or stopping."""
    r = subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True, text=True)
    return r.stdout.strip() not in ("inactive", "failed", "unknown", "")


def start(unit=ANT_UNIT):
    return subprocess.run(["systemctl", "--user", "start", "--no-block", unit]).returncode == 0


def write_private(path, text):
    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text + "\n")
    os.replace(tmp, path)


def ask(url, key, name, is_busy, timeout=10):
    """One ask. Returns (game id, token) or None."""
    from hypermaze.signing import sign
    path = f"/agents/{name}/next"
    # n makes every ask unique: the house accepts each signature only once
    body = json.dumps({"busy": bool(is_busy), "n": secrets.token_hex(4)}).encode()
    ts, sig = sign(key, "POST", path, body)
    req = urllib.request.Request(url.rstrip("/") + path, body,
                                 {"Content-Type": "application/json", "X-Time": ts, "X-Signature": sig})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        got = json.loads(r.read() or b"{}")
    rid, tok = got.get("run_id"), got.get("token")
    if isinstance(rid, str) and isinstance(tok, str) and ID.match(rid) and TOKEN.match(tok):
        return rid, tok
    return None


def once(url, key, name, run_file, token_file, is_busy=busy, do_start=start, log=print):
    """One round: ask, and start the ant if a game came back. Returns the game id started, or None."""
    b = is_busy()
    got = ask(url, key, name, b)
    if not got:
        return None
    if b:                      # the house only hands out a game when we said we were free
        log(f"agent: got game {got[0]} while busy; not starting it")
        return None
    rid, tok = got
    write_private(token_file, tok)
    write_private(run_file, rid)
    if do_start():
        log(f"agent: started game {rid}")
        return rid
    log(f"agent: could not start {ANT_UNIT} for game {rid}")
    return None


def main(argv=None):
    from hypermaze import config
    ap = argparse.ArgumentParser(prog="python3 -m ant.agent", description=__doc__.split("\n")[0])
    ap.add_argument("--config", help="settings file (default ~/hypermaze/config/ant.toml if it exists)")
    ap.add_argument("--once", action="store_true", help="ask one time and exit")
    a = ap.parse_args(argv)
    cfg = config.load_or_exit("ant", a.config)
    url = cfg.get("report")
    if not url:
        sys.exit("agent: set report in the settings")
    name = cfg.get("agent_name") or socket.gethostname().split(".")[0].lower()
    if not NAME.match(name):
        sys.exit(f"agent: agent_name {name!r} must be lowercase letters, digits and dashes (up to 32)")
    key = os.environ.get("HARNESS_KEY") or \
        (config.read_secret(cfg["harness_key_file"]) if cfg.get("harness_key_file") else "")
    if not key:
        sys.exit("agent: set harness_key_file in the settings (or $HARNESS_KEY)")
    if cfg.get("ca_cert") and not os.environ.get("SSL_CERT_FILE"):
        os.environ["SSL_CERT_FILE"] = cfg["ca_cert"]
    run_file = cfg.get("run_file", "~/hypermaze/run.id")
    token_file = cfg.get("token_file", "~/hypermaze/run.token")
    every = max(2, int(cfg.get("poll_seconds", 5)))
    print(f"agent: {name} asking {url} every {every} s", flush=True)
    failing = False
    while True:
        try:
            once(url, key, name, run_file, token_file, log=lambda s: print(s, flush=True))
            if failing:
                print("agent: reachable again", flush=True)
            failing = False
        except (OSError, ValueError) as e:     # network blips: say so once, keep trying
            if not failing:
                print(f"agent: ask failed ({e}); will keep trying", flush=True)
            failing = True
        if a.once:
            return 0
        time.sleep(every)


if __name__ == "__main__":
    sys.exit(main())
