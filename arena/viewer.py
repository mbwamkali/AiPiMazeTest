"""Viewer relay for the Colosseum (standard library only).

Serves the Colosseum pages and passes read-only keeper calls through, signing each one with the admin
key so the key never reaches a browser:

  GET /                          the 3D Colosseum (viewer/colosseum.html)
  GET /flat                      the flat grid view (viewer/index.html)
  GET /api/runs                  list runs (id, settings, moves, ended)
  GET /api/runs/<id>/map         the whole maze as it stands
  GET /api/runs/<id>/log         every event of the run so far, for replays
  GET /api/runs/<id>/feed?since=N  live events (server-sent events); honours Last-Event-ID on reconnect

Saved matches live on the viewer's own machine (matches_dir), never in the browser and never with the
contestant. Saving copies the maze and every event of the chosen runs, so a saved match replays even
after the keeper's run data is gone:

  GET  /api/matches              list saved matches
  GET  /api/matches/<id>         one saved match (maze + events of every run)
  POST /api/matches              {"name": "...", "runs": ["<run id>", ...]}  save a match

Starting matches (only with allow_new_matches = true in viewer.toml, or --allow-new-matches):

  GET  /api/agents               the ants' agents the keeper has heard from, and runs waiting for them
  POST /api/new-match            {"size", "loops", "seed", "max_moves", "generator", "ants": [names]}
                                 the keeper makes one run per ant on the same maze; each ant's agent picks
                                 its run up within a few seconds

Apart from that nothing reaches the keeper but reads: no edits, no stop. Every other path is a plain 404
and every other method is refused.

Every request must carry the viewer key as ?key=... (the "viewer" entry in keys.json, or
$ARENA_VIEWER_KEY); without it the answer is 401 and no maze data. The key only lets someone watch.
With allow_new_matches it also lets them start matches.
It is left out of the log. With --tls-cert/--tls-key the viewer speaks HTTPS.

  python3 -m arena.viewer --keeper http://127.0.0.1:7342 --listen 0.0.0.0:7344 \
      --tls-cert ~/hypermaze-keeper-data/tls/viewer-chain.crt --tls-key ~/hypermaze-keeper-data/tls/viewer.key
"""
import argparse
import hmac
import json
import os
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .auth import sign

KEY_IN_QUERY = re.compile(r"(?:^|&)key=([^&]*)")
REDACT = re.compile(r"key=[^&\s\"]*")
# Shown when the key is missing: no maze data, but a reload of a page opened with ?key= finds the key
# the page kept for this tab and comes back with it.
NEED_KEY = (b"<!doctype html><meta charset=utf-8><title>Arena viewer</title>"
            b"<script>try{const k=sessionStorage.getItem('vkey');if(k){const u=new URL(location.href);"
            b"u.searchParams.set('key',k);location.replace(u)}}catch(e){}</script>"
            b"<p style='font-family:sans-serif'>This viewer needs its key: open it as <code>/?key=...</code></p>")
RUN = re.compile(r"^/api/runs/([0-9A-Za-z-]{1,64})/(map|feed|log)$")
MATCH = re.compile(r"^/api/matches/([0-9A-Za-z-]{1,80})$")
MAX_MATCH_RUNS = 8
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Relay:
    def __init__(self, keeper, key, page, flat_page=None, matches_dir=None, allow_new_matches=False):
        self.keeper, self.key, self.page = keeper.rstrip("/"), key, page
        self.allow_new_matches = allow_new_matches
        self.flat_page = flat_page or page
        self.matches = Matches(matches_dir) if matches_dir else None
        self.lock, self.last = threading.Lock(), {}

    def _sign(self, path, method="GET", body=b""):
        # The keeper accepts each signature once, and a signature covers (method, path, whole second).
        # Two viewers asking for the same path in the same second would collide, so the second one is stamped
        # a second later (the keeper accepts stamps up to 60 s off).
        with self.lock:
            now = max(int(time.time()), self.last.get(path, -1) + 1)
            self.last[path] = now
            if len(self.last) > 500:
                self.last = {k: v for k, v in self.last.items() if v >= now - 120}
        return sign(self.key, method, path, body, ts=now)

    def get(self, path, timeout=20):
        ts, sig = self._sign(path)
        req = urllib.request.Request(self.keeper + path, headers={"X-Time": ts, "X-Signature": sig})
        return urllib.request.urlopen(req, timeout=timeout)

    def post_json(self, path, obj, timeout=60):
        body = json.dumps(obj).encode()
        ts, sig = self._sign(path, "POST", body)
        req = urllib.request.Request(self.keeper + path, body, {"Content-Type": "application/json",
                                                                "X-Time": ts, "X-Signature": sig})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    def get_json(self, path):
        with self.get(path) as r:
            return json.loads(r.read())

    def log(self, run):
        with self.get(f"/admin/runs/{run}/log") as r:
            return [json.loads(l) for l in r.read().decode("utf-8").splitlines() if l.strip()]

    def feed(self, path):
        ts, sig = self._sign(path)
        sep = "&" if "?" in path else "?"
        # the keeper sends a ping every 15 s, so 60 s of silence means the link is dead
        return urllib.request.urlopen(f"{self.keeper}{path}{sep}ts={ts}&sig={sig}", timeout=60)


class Matches:
    """Saved matches: one JSON file per match in a folder on the viewer's machine."""

    def __init__(self, folder):
        self.dir = os.path.expanduser(folder)
        self.lock = threading.Lock()

    def _path(self, mid):
        return os.path.join(self.dir, mid + ".json")

    def list(self):
        out = []
        if os.path.isdir(self.dir):
            for fn in sorted(os.listdir(self.dir), reverse=True):
                if not fn.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(self.dir, fn), encoding="utf-8") as f:
                        m = json.load(f)
                    out.append({k: m.get(k) for k in ("id", "name", "saved_at", "settings", "contestants")})
                except (OSError, ValueError):
                    continue
        return out

    def read(self, mid):
        with open(self._path(mid), "rb") as f:
            return f.read()

    def save(self, relay, name, runs):
        maps, logs, settings = [], [], None
        for run in runs:
            maps.append(relay.get_json(f"/admin/runs/{run}/map"))
            logs.append(relay.log(run))
        s0 = maps[0]
        settings = {k: s0.get(k) for k in ("dims", "size", "loops", "seed", "generator")}
        for m in maps[1:]:
            if {k: m.get(k) for k in settings} != settings:
                raise ValueError("runs are on different mazes")
        contestants = []
        for run, ev in zip(runs, logs):
            who = next((e.get("where") for e in ev if e.get("t") == "harness" and e.get("where")), None)
            end = next((e for e in ev if e.get("t") == "end"), None)
            contestants.append({"run": run, "name": who, "result": end and end.get("result"),
                                "moves": end and end.get("moves")})
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "match"
        with self.lock:
            os.makedirs(self.dir, exist_ok=True)
            mid, n = f"{stamp}-{slug}", 2
            while os.path.exists(self._path(mid)):
                mid, n = f"{stamp}-{slug}-{n}", n + 1
            doc = {"format": "hypermaze-match", "version": 1, "id": mid, "name": name,
                   "saved_at": round(time.time(), 3), "settings": settings, "contestants": contestants,
                   "map": maps[0], "runs": [{"run": r, "map": m, "events": ev} for r, m, ev in zip(runs, maps, logs)]}
            tmp = self._path(mid) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(doc, f)
            os.replace(tmp, self._path(mid))
        return {"id": mid, "name": name, "saved_at": doc["saved_at"], "settings": settings, "contestants": contestants}


class Handler(BaseHTTPRequestHandler):
    relay = None
    viewer_key = ""
    server_version = "arena-viewer"
    sys_version = ""

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.client_address[0]} {REDACT.sub('key=***', fmt % args)}\n")

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _key_ok(self, query):
        m = KEY_IN_QUERY.search(query)
        from urllib.parse import unquote
        given = unquote(m.group(1)) if m else ""
        return bool(self.viewer_key) and hmac.compare_digest(given.encode(), self.viewer_key.encode())

    def _404(self):
        self._send(404, b'{"error":"not found"}')

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if not self._key_ok(query):
            if path in ("/", "/index.html"):
                return self._send(401, NEED_KEY, "text/html; charset=utf-8")
            return self._send(401, b'{"error":"viewer key needed"}')
        try:
            if path in ("/", "/index.html", "/flat"):
                with open(self.relay.flat_page if path == "/flat" else self.relay.page, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if path == "/api/matches":
                if not self.relay.matches:
                    return self._404()
                return self._send(200, json.dumps({"matches": self.relay.matches.list()}).encode())
            m = MATCH.match(path)
            if m:
                if not self.relay.matches:
                    return self._404()
                try:
                    return self._send(200, self.relay.matches.read(m.group(1)))
                except OSError:
                    return self._404()
            if path == "/api/agents":
                if not self.relay.allow_new_matches:
                    return self._404()
                return self._send(200, json.dumps(self.relay.get_json("/admin/agents")).encode())
            if path == "/api/runs":
                with self.relay.get("/admin/runs") as r:
                    runs = json.loads(r.read())["runs"]
                keep = [{k: x.get(k) for k in ("run_id", "settings", "moves", "ended")} for x in runs]
                return self._send(200, json.dumps({"runs": keep}).encode())
            m = RUN.match(path)
            if not m:
                return self._404()
            run, what = m.groups()
            if what == "map":
                with self.relay.get(f"/admin/runs/{run}/map") as r:
                    return self._send(200, r.read())
            if what == "log":
                return self._send(200, json.dumps({"events": self.relay.log(run)}).encode())
            return self._feed(run, query)
        except urllib.error.HTTPError:
            return self._404()
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            try:
                self._send(502, b'{"error":"keeper unreachable"}')
            except OSError:
                pass

    def _feed(self, run, query):
        m = re.search(r"(?:^|&)since=(\d{1,9})(?:&|$)", query)
        since = int(m.group(1)) if m else 0
        last = self.headers.get("Last-Event-ID", "")
        if last.isdigit():
            since = max(since, int(last) + 1)
        upstream = self.relay.feed(f"/admin/runs/{run}/feed?since={since}")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(b"retry: 3000\n\n")
            for line in upstream:
                self.wfile.write(line)
                if line in (b"\n", b"\r\n"):
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            upstream.close()

    def do_POST(self):
        path, _, query = self.path.partition("?")
        if not self._key_ok(query):
            return self._send(401, b'{"error":"viewer key needed"}')
        if path == "/api/new-match" and self.relay.allow_new_matches:
            return self._new_match()
        if path != "/api/matches" or not self.relay.matches:
            return self._refuse()
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 10_000:
                raise ValueError
            body = json.loads(self.rfile.read(n) or b"{}")
            name = str(body.get("name") or "Match").strip()[:80]
            runs = body.get("runs")
            if not isinstance(runs, list) or not 1 <= len(runs) <= MAX_MATCH_RUNS or \
                    not all(isinstance(r, str) and re.fullmatch(r"[0-9A-Za-z-]{1,64}", r) for r in runs):
                raise ValueError
            runs = list(dict.fromkeys(runs))
        except (ValueError, TypeError, AttributeError):
            return self._send(400, b'{"error":"bad request"}')
        try:
            saved = self.relay.matches.save(self.relay, name, runs)
        except urllib.error.HTTPError:
            return self._404()
        except ValueError as e:
            return self._send(400, json.dumps({"error": str(e)}).encode())
        except (urllib.error.URLError, OSError, KeyError):
            return self._send(502, b'{"error":"keeper unreachable"}')
        return self._send(201, json.dumps(saved).encode())

    def _new_match(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 4000:
                raise ValueError
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
            keep = {k: body[k] for k in ("size", "loops", "seed", "max_moves", "generator", "ants") if k in body}
        except (ValueError, TypeError):
            return self._send(400, b'{"error":"bad request"}')
        try:
            made = self.relay.post_json("/admin/matches", keep)
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read()).get("error") or "refused"
            except (ValueError, OSError, AttributeError):
                err = "refused"
            return self._send(400 if e.code == 400 else 502, json.dumps({"error": err}).encode())
        except (urllib.error.URLError, OSError, ValueError):
            return self._send(502, b'{"error":"keeper unreachable"}')
        return self._send(201, json.dumps(made).encode())

    def _refuse(self):
        self._send(405, b'{"error":"read only"}')

    do_PUT = do_DELETE = do_PATCH = _refuse


class TLSServer(ThreadingHTTPServer):
    """HTTPS on one port. Each connection's first byte says what it is: a TLS hello is wrapped and served
    (in the connection's own thread, so a slow client can't hold up the others); anything else is a
    browser that typed http://, and gets a redirect to the same address on https://."""
    ctx = None
    public_host = "127.0.0.1:7344"  # host[:port] for the redirect when the browser's Host header is unusable

    def finish_request(self, request, client_address):
        try:
            request.settimeout(10)
            first = request.recv(1, socket.MSG_PEEK)
            if not first:
                return
            if first != b"\x16":
                return self._redirect(request)
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

    def _redirect(self, request):
        head = b""
        while b"\r\n\r\n" not in head and len(head) < 8192:
            chunk = request.recv(2048)
            if not chunk:
                break
            head += chunk
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        path = parts[1] if len(parts) > 1 and parts[1].startswith("/") else "/"
        host = next((l.split(":", 1)[1].strip() for l in lines[1:] if l.lower().startswith("host:")), "")
        if not re.fullmatch(r"[A-Za-z0-9.\-]+(:\d{1,5})?", host):
            host = self.public_host
        loc = f"https://{host}{path}".encode("latin-1", "replace")
        request.sendall(b"HTTP/1.1 301 Moved Permanently\r\nLocation: " + loc +
                        b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")


def main(argv=None):
    sys.path.insert(0, HERE)
    from hypermaze import config
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", help="settings file (default ~/hypermaze/config/viewer.toml if it exists)")
    ap.add_argument("--keeper", help="the keeper's admin URL (default http://127.0.0.1:7342)")
    ap.add_argument("--listen", help="host:port to serve the viewer on (default 127.0.0.1:7344)")
    ap.add_argument("--keys", help="keys.json with the admin and viewer keys (default ~/hypermaze-keeper-data/keys.json)")
    ap.add_argument("--page", help="the main page (default viewer/colosseum.html)")
    ap.add_argument("--flat-page", help="the flat grid page served at /flat (default viewer/index.html)")
    ap.add_argument("--matches-dir", help="where saved matches go (default ~/hypermaze/colosseum/matches)")
    ap.add_argument("--tls-cert", help="certificate (chain) file: serve HTTPS")
    ap.add_argument("--tls-key", help="private key for --tls-cert")
    ap.add_argument("--allow-new-matches", action="store_true", default=None,
                    help="let the page start matches (the keeper makes the runs; ants' agents pick them up)")
    ap.add_argument("--public-host", help="host[:port] to redirect http:// visitors to when their Host header is unusable")
    a = ap.parse_args(argv)
    cfg = config.load_or_exit("viewer", a.config)
    keeper = config.pick(a.keeper, None, cfg, "keeper", "http://127.0.0.1:7342")
    listen = config.pick(a.listen, None, cfg, "listen", "127.0.0.1:7344")
    keys_file = os.path.expanduser(config.pick(a.keys, None, cfg, "keys_file", "~/hypermaze-keeper-data/keys.json"))
    page = os.path.expanduser(config.pick(a.page, None, cfg, "page", os.path.join(HERE, "viewer", "colosseum.html")))
    flat_page = os.path.expanduser(config.pick(a.flat_page, None, cfg, "flat_page", os.path.join(HERE, "viewer", "index.html")))
    matches_dir = config.pick(a.matches_dir, None, cfg, "matches_dir", "~/hypermaze/colosseum/matches")
    tls_cert = config.pick(a.tls_cert, None, cfg, "tls_cert")
    tls_key = config.pick(a.tls_key, None, cfg, "tls_key")
    allow_new = bool(config.pick(a.allow_new_matches, None, cfg, "allow_new_matches", False))
    host, port = listen.rsplit(":", 1)
    public_host = config.pick(a.public_host, None, cfg, "public_host",
                              f"{'127.0.0.1' if host in ('', '0.0.0.0', '::') else host}:{port}")
    with open(keys_file) as f:
        keys = json.load(f)
    key = os.environ.get("ARENA_ADMIN_KEY") or keys["admin"]
    vkey = os.environ.get("ARENA_VIEWER_KEY") or keys.get("viewer", "")
    if not vkey:
        sys.exit("no viewer key: add \"viewer\" to keys.json or set ARENA_VIEWER_KEY")
    handler = type("H", (Handler,), {"relay": Relay(keeper, key, page, flat_page, matches_dir, allow_new), "viewer_key": vkey})
    scheme = "http"
    if tls_cert:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(os.path.expanduser(tls_cert), os.path.expanduser(tls_key) if tls_key else None)
        srv = type("S", (TLSServer,), {"ctx": ctx, "public_host": public_host})((host, int(port)), handler)
        scheme = "https"
    else:
        srv = ThreadingHTTPServer((host, int(port)), handler)
    srv.daemon_threads = True
    print(f"viewer relay on {scheme}://{listen} -> {keeper} (viewer key required"
          f"{'; can start matches' if allow_new else ''})", file=sys.stderr, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
