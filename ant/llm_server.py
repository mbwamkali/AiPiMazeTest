"""Run the ant's llama-server from the ant settings file, and stop it when idle (stdlib only).

    python3 -m ant.llm_server [--config ~/hypermaze/config/ant.toml] [--idle-minutes 5] [--dry-run]

Uses model, llama_server, threads, ctx, llm_port and idle_minutes from the settings; llama-server
always listens on 127.0.0.1 only.

With idle_minutes > 0 (default 5) this stays as a small supervisor: it starts llama-server, asks its
/slots endpoint every 30 s whether it is working on a request, and after idle_minutes with no work it
stops llama-server and exits 0 (so systemd's Restart=on-failure leaves it down until the next ant run
starts it again). While the model is still loading, or if /slots can't be read, it counts as busy
when hypermaze-ant.service is active (or when systemctl isn't available), so a run is never cut off.
With idle_minutes = 0 the process is simply replaced by llama-server.
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANT_UNIT = "hypermaze-ant.service"
PORT_TAKEN = 3   # exit status when something else already listens on the port (systemd: don't restart)


def pidfile():
    base = os.environ.get("XDG_RUNTIME_DIR") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "hypermaze-llm.pid")


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def check_ours(wait=20.0, state=None, pidpath=None, sleep=time.sleep):
    """For hypermaze-ant's ExecStartPre: succeed only when this service's own llama-server is running.
    state(): hypermaze-llm's ActiveState (None = no systemctl). Returns (ok, message)."""
    state = state or llm_state
    pidpath = pidpath or pidfile()
    end = time.monotonic() + wait
    while True:
        st = state()
        if st == "failed":
            return False, "hypermaze-llm.service failed (see: hm logs ant); not starting the run"
        try:
            with open(pidpath) as f:
                pid = int(f.read().strip())
        except (OSError, ValueError):
            pid = None
        if pid and alive(pid):
            return True, f"model server running (pid {pid})"
        if time.monotonic() >= end:
            return False, "hypermaze-llm.service did not start its llama-server; not starting the run"
        sleep(0.5)


def llm_state():
    if not shutil.which("systemctl"):
        return None
    r = subprocess.run(["systemctl", "--user", "show", "-p", "ActiveState", "--value", "hypermaze-llm.service"],
                       capture_output=True, text=True, timeout=10)
    return r.stdout.strip() or None


def port_taken(port):
    import socket
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return True
    except OSError:
        return False


def command(cfg, model=None, server=None, threads=None, port=None, ctx=None):
    from hypermaze import config
    model = config.pick(model, "MODEL", cfg, "model")
    if not model:
        raise SystemExit("no model: set model in the ant settings, $MODEL or --model")
    server = config.pick(server, None, cfg, "llama_server", "llama-server")
    threads = config.pick(threads, None, cfg, "threads", 4)
    port = config.pick(port, None, cfg, "llm_port", 8095)
    ctx = config.pick(ctx, None, cfg, "ctx", 4096)
    return [os.path.expanduser(server), "-m", os.path.expanduser(model), "-t", str(threads), "-c", str(ctx),
            "-np", "1", "--jinja", "--host", "127.0.0.1", "--port", str(port), "--alias", "ant"]


# ---- idle decision (pure functions, tested with fake data) -------------------------------------

def slots_busy(slots):
    """True if any slot in a /slots answer is working, False if all are idle, None if unreadable."""
    if not isinstance(slots, list) or not slots:
        return None
    states = []
    for s in slots:
        if not isinstance(s, dict):
            return None
        if "is_processing" in s:
            states.append(bool(s["is_processing"]))
        elif "state" in s:              # older llama.cpp: 0 = idle, 1 = processing
            states.append(s["state"] != 0)
        else:
            return None
    return any(states)


def is_busy(slots_state, ant_active):
    """Busy now? slots_state: True/False/None from slots_busy (None = unknown or still loading);
    ant_active: True/False/None (None = can't tell). Unknown counts as busy, so nothing is cut off."""
    if slots_state is not None:
        return slots_state
    return ant_active is not False


def should_stop(last_busy, now, idle_minutes):
    """Stop llama-server? Only after idle_minutes with no work; idle_minutes 0 means never."""
    return idle_minutes > 0 and now - last_busy >= idle_minutes * 60


# ---- probes ---------------------------------------------------------------------------------------

def read_slots(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/slots", timeout=5) as r:
            return slots_busy(json.load(r))
    except (OSError, ValueError):     # 503 while loading, 501 if slots are off, refused while starting
        return None


def ant_active():
    if not shutil.which("systemctl"):
        return None
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", ANT_UNIT], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() in ("active", "activating", "reloading")


def supervise(cmd, port, idle_minutes, poll=30.0, idle_seconds=None):
    child = subprocess.Popen(cmd)
    stopping = []
    pid_path = pidfile()
    try:
        os.makedirs(os.path.dirname(pid_path), exist_ok=True)
        with open(pid_path, "w") as f:
            f.write(str(child.pid))
    except OSError:
        pid_path = None
    try:
        return _serve_until_idle(child, stopping, port, idle_minutes, poll, idle_seconds)
    finally:
        if pid_path:
            try:
                os.remove(pid_path)
            except OSError:
                pass


def _serve_until_idle(child, stopping, port, idle_minutes, poll, idle_seconds):

    def forward(sig, _frame):
        stopping.append(sig)
        child.terminate()

    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)
    limit = idle_seconds if idle_seconds is not None else idle_minutes * 60
    last_busy = time.monotonic()
    while True:
        try:
            code = child.wait(timeout=poll)
            return 0 if stopping else code     # stopped by us/systemd: clean; crashed: its own code
        except subprocess.TimeoutExpired:
            pass
        now = time.monotonic()
        if is_busy(read_slots(port), ant_active()):
            last_busy = now
        elif limit > 0 and now - last_busy >= limit:
            print(f"llama-server idle for {limit / 60:g} min: stopping it", file=sys.stderr, flush=True)
            child.terminate()
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            return 0


def main(argv=None):
    sys.path.insert(0, HERE)
    from hypermaze import config
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", help="settings file (default ~/hypermaze/config/ant.toml if it exists)")
    ap.add_argument("--model")
    ap.add_argument("--llama-server")
    ap.add_argument("--threads", type=int)
    ap.add_argument("--port", type=int)
    ap.add_argument("--ctx", type=int)
    ap.add_argument("--idle-minutes", type=int, help="stop after this long with no work (default 5; 0 = never)")
    ap.add_argument("--poll", type=float, default=30.0, help=argparse.SUPPRESS)
    ap.add_argument("--idle-seconds", type=float, help=argparse.SUPPRESS)   # for tests
    ap.add_argument("--dry-run", action="store_true", help="print the command instead of running it")
    ap.add_argument("--check-ours", action="store_true",
                    help="exit 0 only if hypermaze-llm.service's own llama-server is running (used by the ant unit)")
    a = ap.parse_args(argv)
    if a.check_ours:
        ok, msg = check_ours()
        print(msg, file=sys.stderr if not ok else sys.stdout, flush=True)
        return 0 if ok else 1
    cfg = config.load_or_exit("ant", a.config)
    cmd = command(cfg, a.model, a.llama_server, a.threads, a.port, a.ctx)
    idle = config.pick(a.idle_minutes, None, cfg, "idle_minutes", 5)
    if a.dry_run:
        print(" ".join(cmd))
        print(f"idle stop: {'after ' + str(idle) + ' min' if idle > 0 else 'never'}")
        return 0
    exe = cmd[0] if os.sep in cmd[0] else shutil.which(cmd[0])
    if not exe:
        sys.exit(f"{cmd[0]} not found: set llama_server in the ant settings")
    cmd[0] = exe
    port = int(cmd[cmd.index("--port") + 1])
    if port_taken(port):
        print(f"port {port} on 127.0.0.1 is already in use (another llama-server?); not starting a second one. "
              f"The ant can use the one that is there; stop it or change llm_port to use this service.",
              file=sys.stderr, flush=True)
        return PORT_TAKEN
    if idle <= 0 and a.idle_seconds is None:
        try:   # exec keeps this pid, so the pid file names llama-server itself
            os.makedirs(os.path.dirname(pidfile()), exist_ok=True)
            with open(pidfile(), "w") as f:
                f.write(str(os.getpid()))
        except OSError:
            pass
        os.execv(exe, cmd)
    return supervise(cmd, port, idle, a.poll, a.idle_seconds)


if __name__ == "__main__":
    sys.exit(main())
