"""The keeper's three HTTP servers (standard library only).

Contestant port (open to the runner's machine):
  GET  /v1/room                       the room the session stands in
  POST /v1/walk   {"to": "<name>"}    go through the door to that room
  GET  /v1/device/notebook[?page=N]   the notebook page as text (the open page by default)
  POST /v1/device/notebook {"line": "<command>"}   one line into the notebook -> reply and open page
  POST /v1/wall   {"text": "<line>"}  write one line on this room's walls (costs a turn) -> reply and the room
  POST /v1/say    {"text": "<line>"}  say a line; only teammates in the same room hear it (free) -> reply and the room
Admin port (signed requests only; firewalled from the runner's machine):
  POST /admin/runs                    start a run -> run_id, token
  GET  /admin/runs                    list runs
  GET  /admin/runs/<id>/map           the whole maze as it stands
  GET  /admin/runs/<id>/state         position, seen rooms, counts
  GET  /admin/runs/<id>/feed?since=N  live events (server-sent events)
  GET  /admin/runs/<id>/log           every event so far (JSON lines)
  POST /admin/runs/<id>/edits         {"edit": {...}, "by": "director", "why": "..."}
  POST /admin/runs/<id>/edits/preview same, without applying
  POST /admin/runs/<id>/stop          {"farewell": "..."}
  POST /admin/matches                 {"size", "loops", "seed", "max_moves", "generator", "ants": [names]}
                                      one run per ant on the same maze, each waiting for that ant's agent
  GET  /admin/agents                  ants' agents heard from, and the runs waiting for them (no tokens)
  GET  /admin/teams/<match>           a team match's score: every ant's moves, and the total once all are out
Harness port (signed with the harness key; open to the harness on the runner's machine):
  POST /runs/<id>/turn                one turn of the LLM harness: what it was shown and what it sent
  POST /agents/<name>/next {"busy": b}  an ant's agent asks for work -> {"run_id", "token"} or {"run_id": null}
Each port only accepts its own key. Anything unknown, unauthorised or malformed in the URL gets the same plain 404.
With --tls-cert/--tls-key the contestant and harness ports speak HTTPS only (the admin port stays plain HTTP,
meant for 127.0.0.1). Without them nothing changes.
"""
import json
import re
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import auth
from .dispatch import Dispatch, MatchError, check_request
from .rulebook import RuleError
from .runs import Store

NOT_FOUND = b'{"error":"not found"}'
DEVICE_PATH = re.compile(r"^/v1/device/(notebook)(?:\?page=(\d{1,4}))?$")
RUN_PATH = re.compile(r"^/admin/runs/([0-9A-Za-z-]+)(/[a-z/]+)?$")


class Keeper:
    def __init__(self, data_dir, access_log=None, keys_file=None):
        self.store = Store(data_dir)
        self.keys = auth.load_keys(data_dir, path=keys_file)
        self.verifier = auth.Verifier()
        self.access_log = access_log
        self.log_lock = threading.Lock()
        self.dispatch = Dispatch(stop_run=self._missed, ended=self._ended)

    def _ended(self, run_id):
        run = self.store.runs.get(run_id)
        return run is None or run.ended is not None

    def _missed(self, run_id):
        run = self.store.runs.get(run_id)
        if run:
            run.stop("This game was called off.")

    def new_match(self, data):
        req = check_request(data)
        match_id = Dispatch.new_match_id()
        seed, made = req["seed"], []
        rb = {"generator": req["generator"], "max_moves": req["max_moves"], "team": req["team"]}
        for seat, ant in enumerate(req["ants"]):
            run, token = self.store.create(2, req["size"], req["loops"], seed, rb,
                                           {"match": match_id, "agent": ant, "seat": seat})
            seed = run.settings["seed"]
            made.append((ant, run, token))
        prev = None
        for ant, run, token in made:
            self.dispatch.add(ant, run.id, token, match_id, after=prev if req["team"] == "relay" else None)
            prev = run.id
        self.note(f"match {match_id}: {len(made)} run(s), size {req['size']} loops {req['loops']} seed {seed}")
        return {"match": match_id, "seed": seed, "team": req["team"],
                "runs": [{"run_id": r.id, "agent": a} for a, r, _ in made]}

    def note(self, line):
        if self.access_log:
            with self.log_lock:
                self.access_log.write(time.strftime("%Y-%m-%dT%H:%M:%SZ ", time.gmtime()) + line + "\n")
                self.access_log.flush()


class _Base(BaseHTTPRequestHandler):
    server_version = "house"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    keeper = None  # set on the subclass made by serve()

    def version_string(self):
        return self.server_version

    def log_message(self, fmt, *args):  # quiet; refusals are noted separately
        pass

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            raise ValueError("too big")
        return self.rfile.read(n) if n else b""

    def _send(self, code, obj=None, raw=None):
        data = raw if raw is not None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _404(self, why=""):
        if why:
            self.keeper.note(f"refused {self.client_address[0]} {self.command} {self.path} ({why})")
        self._send(404, raw=NOT_FOUND)

    def do_PUT(self):
        self._404("method")

    do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = do_PUT


class ContestantHandler(_Base):
    def _run(self):
        h = self.headers.get("Authorization", "")
        if not h.startswith("Bearer "):
            return None
        return self.keeper.store.by_token(h[7:].strip())

    def do_GET(self):
        dev = DEVICE_PATH.match(self.path)
        known = self.path == "/v1/room" or bool(dev)
        run = self._run() if known else None
        if not run:
            return self._404("contestant" if known else "path")
        if dev:
            return self._send(200, run.device_view(dev.group(1), int(dev.group(2)) if dev.group(2) else None))
        self._send(200, run.room_view())

    def do_POST(self):
        try:
            body = self._body()
        except ValueError:
            return self._404("body")
        dev = DEVICE_PATH.match(self.path)
        if dev and dev.group(2):
            dev = None
        known = self.path in ("/v1/walk", "/v1/wall", "/v1/say") or bool(dev)
        run = self._run() if known else None
        if not run:
            return self._404("contestant" if known else "path")
        if dev:
            try:
                line = json.loads(body or b"{}")["line"]
                if not isinstance(line, str) or len(line) > 1000:
                    raise TypeError
            except (ValueError, KeyError, TypeError):
                return self._send(400, {"error": "bad_request"})
            return self._send(200, run.device_input(dev.group(1), line))
        if self.path == "/v1/say":
            try:
                text = json.loads(body or b"{}")["text"]
                if not isinstance(text, str) or len(text) > 1000:
                    raise TypeError
            except (ValueError, KeyError, TypeError):
                return self._send(400, {"error": "bad_request"})
            return self._send(200, run.say(text))
        if self.path == "/v1/wall":
            try:
                text = json.loads(body or b"{}")["text"]
                if not isinstance(text, str) or len(text) > 1000:
                    raise TypeError
            except (ValueError, KeyError, TypeError):
                return self._send(400, {"error": "bad_request"})
            return self._send(200, run.wall_write(text))
        try:
            to = json.loads(body or b"{}")["to"]
            if not isinstance(to, str):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            return self._send(400, {"error": "bad_request"})
        try:
            self._send(200, run.walk(to))
        except LookupError:
            self._send(400, {"error": "no_door"})


class AdminHandler(_Base):
    role = "admin"

    def _auth(self, body):
        """(True if signed with this port's key, path without ts/sig)."""
        path, ts, sig = auth.split_signed_path(self.path)
        ts = self.headers.get("X-Time") or ts
        sig = self.headers.get("X-Signature") or sig
        k = self.keeper
        return bool(sig) and k.verifier.check(k.keys[self.role], self.command, path, ts, sig, body), path

    def _route(self, path):
        base = path.split("?", 1)[0]
        if base == "/admin/runs":
            return None, ""
        m = RUN_PATH.match(base)
        if not m:
            return False, None
        run = self.keeper.store.runs.get(m.group(1))
        return (run, m.group(2) or "") if run else (False, None)

    def do_GET(self):
        ok, path = self._auth(b"")
        if not ok:
            return self._404("admin auth")
        if path == "/admin/agents":
            return self._send(200, self.keeper.dispatch.view())
        if path.startswith("/admin/teams/"):
            team = self.keeper.store.teams.get(path[len("/admin/teams/"):])
            return self._send(200, team.score()) if team else self._404("path")
        run, sub = self._route(path)
        if run is False:
            return self._404("path")
        if run is None:
            return self._send(200, {"runs": [{"run_id": r.id, "settings": r.settings, "moves": r.moves,
                                               "ended": r.ended} for r in self.keeper.store.runs.values()]})
        if sub == "/map":
            return self._send(200, run.map_dict())
        if sub == "/state":
            return self._send(200, run.state())
        if sub == "/log":
            with run.lock:
                data = "".join(json.dumps(e) + "\n" for e in run.events).encode()
            return self._send(200, raw=data)
        if sub == "/feed":
            return self._feed(run, path)
        self._404("path")

    def _feed(self, run, path):
        m = re.search(r"[?&]since=(\d+)", path)
        i = int(m.group(1)) if m else 0
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            while True:
                with run.cond:
                    while i >= len(run.events):
                        if not run.cond.wait(timeout=15):
                            break
                    batch = run.events[i:]
                if not batch:
                    self.wfile.write(b": ping\n\n")
                for ev in batch:
                    self.wfile.write(f"id: {ev['seq']}\nevent: {ev['t']}\ndata: {json.dumps(ev)}\n\n".encode())
                    i = ev["seq"] + 1
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def do_POST(self):
        try:
            body = self._body()
        except ValueError:
            return self._404("body")
        ok, path = self._auth(body)
        if not ok:
            return self._404("admin auth")
        if path == "/admin/matches":
            try:
                return self._send(200, self.keeper.new_match(json.loads(body or b"{}")))
            except (MatchError, RuleError, ValueError, TypeError) as e:
                return self._send(400, {"error": str(e)})
        run, sub = self._route(path)
        if run is False:
            return self._404("path")
        try:
            data = json.loads(body or b"{}")
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            return self._send(400, {"error": "bad_request"})
        if run is None:
            try:
                new, token = self.keeper.store.create(data.get("dims", 2), data.get("size", 8), data.get("loops", 0.0),
                                                      data.get("seed"), data.get("rulebook"))
            except (RuleError, ValueError, TypeError) as e:
                return self._send(400, {"error": str(e)})
            return self._send(200, {"run_id": new.id, "token": token, "seed": new.settings["seed"],
                                    "fingerprint": new.fingerprint, "rulebook": new.rulebook})
        if sub in ("/edits", "/edits/preview"):
            res = run.try_edit(data.get("edit"), str(data.get("by", "director")), str(data.get("why", "")),
                               apply=sub == "/edits")
            return self._send(200, res)
        if sub == "/stop":
            run.stop(data.get("farewell"))
            return self._send(200, {"status": "stopped", "ended": run.ended})
        self._404("path")


class HarnessHandler(AdminHandler):
    """Only POST /runs/<id>/turn, signed with the harness key."""
    role = "harness"
    TURN = re.compile(r"^/runs/([0-9A-Za-z-]+)/turn$")
    AGENT = re.compile(r"^/agents/([a-z0-9][a-z0-9-]{0,31})/next$")

    def do_GET(self):
        self._404("path")

    def do_POST(self):
        try:
            body = self._body()
        except ValueError:
            return self._404("body")
        ok, path = self._auth(body)
        ag = self.AGENT.match(path) if ok else None
        if ag:
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError
            except ValueError:
                return self._send(400, {"error": "bad_request"})
            return self._send(200, self.keeper.dispatch.take(ag.group(1), data.get("busy", True)))
        m = self.TURN.match(path) if ok else None
        run = self.keeper.store.runs.get(m.group(1)) if m else None
        if not run:
            return self._404("harness auth" if not ok else "path")
        try:
            data = json.loads(body or b"{}")
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            return self._send(400, {"error": "bad_request"})
        ev = run.harness(data)
        self._send(200, {"status": "ok", "seq": ev["seq"]})


class TLSServer(ThreadingHTTPServer):
    """HTTPS only. The handshake runs in the connection's own thread, so a slow client can't hold up the others;
    a connection that doesn't start with a TLS hello is closed."""
    ctx = None

    def finish_request(self, request, client_address):
        try:
            request.settimeout(10)
            request = self.ctx.wrap_socket(request, server_side=True)
            request.settimeout(None)
        except (OSError, ssl.SSLError):
            return
        super().finish_request(request, client_address)

    def shutdown_request(self, request):
        try:
            super().shutdown_request(request)
        except OSError:
            pass


def tls_context(cert, key=None):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)
    return ctx


def serve(data_dir, contestant=("0.0.0.0", 7341), admin=("127.0.0.1", 7342), harness=("127.0.0.1", 7343),
          access_log=None, tls=None, keys_file=None):
    """Start the three servers. Returns (keeper, contestant_server, admin_server, harness_server).
    tls: an ssl.SSLContext; when given, the contestant and harness ports serve HTTPS (admin stays plain)."""
    keeper = Keeper(data_dir, access_log, keys_file)
    servers = []
    for addr, cls, secure in ((contestant, ContestantHandler, True), (admin, AdminHandler, False),
                              (harness, HarnessHandler, True)):
        base = type("S", (TLSServer,), {"ctx": tls}) if tls and secure else ThreadingHTTPServer
        s = base(addr, type(cls.__name__, (cls,), {"keeper": keeper}))
        s.daemon_threads = True
        threading.Thread(target=s.serve_forever, daemon=True).start()
        servers.append(s)
    return (keeper, *servers)


def main(argv=None):
    import argparse
    import os
    from hypermaze import config
    ap = argparse.ArgumentParser(prog="python3 -m arena", description="Run the hypermaze keeper.")
    ap.add_argument("--config", help="settings file (default ~/hypermaze/config/keeper.toml if it exists)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve", help="start the servers")
    sv.add_argument("--config", default=argparse.SUPPRESS, help="settings file (same as before 'serve')")
    sv.add_argument("--data", help="data folder (default ~/hypermaze-keeper-data)")
    sv.add_argument("--contestant", help="host:port the runner talks to (default 127.0.0.1:7341)")
    sv.add_argument("--admin", help="host:port for the Colosseum and tools (default 127.0.0.1:7342)")
    sv.add_argument("--harness", help="host:port the LLM harness reports turns to (default 127.0.0.1:7343)")
    sv.add_argument("--tls-cert", help="certificate (chain) file: contestant and harness ports serve HTTPS")
    sv.add_argument("--tls-key", help="private key for --tls-cert")
    sv.add_argument("--keys-file", help="keys.json (default <data>/keys.json)")
    ky = sub.add_parser("keys", help="create (or --rotate) the admin and harness keys and print where they are")
    ky.add_argument("--config", default=argparse.SUPPRESS, help="settings file (same as before 'keys')")
    ky.add_argument("--data", help="data folder (default ~/hypermaze-keeper-data)")
    ky.add_argument("--keys-file", help="keys.json (default <data>/keys.json)")
    ky.add_argument("--rotate", action="store_true")
    a = ap.parse_args(argv)
    cfg = config.load_or_exit("keeper", a.config)
    data = os.path.expanduser(config.pick(a.data, None, cfg, "data", "~/hypermaze-keeper-data"))
    keys_file = config.pick(a.keys_file, None, cfg, "keys_file")
    keys_file = os.path.expanduser(keys_file) if keys_file else None
    if a.cmd == "keys":
        auth.load_keys(data, rotate=a.rotate, path=keys_file)
        print(keys_file or os.path.join(data, "keys.json"))
        return 0
    contestant = config.pick(a.contestant, None, cfg, "contestant", "127.0.0.1:7341")
    admin = config.pick(a.admin, None, cfg, "admin", "127.0.0.1:7342")
    harness = config.pick(a.harness, None, cfg, "harness", "127.0.0.1:7343")
    tls_cert = config.pick(a.tls_cert, None, cfg, "tls_cert")
    tls_key = config.pick(a.tls_key, None, cfg, "tls_key")
    hp = lambda s: (s.rsplit(":", 1)[0], int(s.rsplit(":", 1)[1]))
    if tls_key and not tls_cert:
        ap.error("--tls-key needs --tls-cert")
    tls = tls_context(os.path.expanduser(tls_cert), os.path.expanduser(tls_key) if tls_key else None) \
        if tls_cert else None
    os.makedirs(data, mode=0o700, exist_ok=True)
    log = open(os.path.join(data, "access.log"), "a")
    keeper, *_ = serve(data, hp(contestant), hp(admin), hp(harness), log, tls, keys_file)
    print(f"keeper: contestant on {contestant}, admin on {admin}, harness on {harness}"
          f"{' (contestant and harness over TLS)' if tls else ''}, data in {data}, "
          f"{len(keeper.store.runs)} runs loaded", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
