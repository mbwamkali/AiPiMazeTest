"""Credentials for the keeper's channels.

  run token     sent as "Authorization: Bearer <token>" by the runner; sees one run's current room
  admin key     never sent: each admin request is signed (HMAC-SHA256) with a timestamp
  harness key   signs the same way, on the harness port only (posting turns to /runs/<id>/turn)

Signature = hex HMAC-SHA256(key, METHOD + "\\n" + PATH + "\\n" + TIMESTAMP + "\\n" + sha256hex(BODY)),
sent as headers X-Time and X-Signature (or ?ts=&sig= query parameters, for a browser's
EventSource, which can't set headers; then PATH excludes those two parameters).
A signature is good for 60 seconds and only once.
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from urllib.parse import urlsplit, parse_qsl, urlencode

from hypermaze.signing import sign  # noqa: F401  (re-exported: arena.auth.sign)

WINDOW = 60


def load_keys(data_dir, create=True, rotate=False, path=None):
    """keys.json in data_dir (or at `path`), created on first use."""
    path = path or os.path.join(data_dir, "keys.json")
    if os.path.exists(path) and not rotate:
        with open(path) as f:
            return json.load(f)
    if not create and not rotate:
        raise FileNotFoundError(path)
    keys = {"admin": secrets.token_hex(32), "harness": secrets.token_hex(32)}
    os.makedirs(os.path.dirname(os.path.abspath(path)), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(keys, f, indent=1)
    return keys


def split_signed_path(raw_path):
    """(path to verify, ts, sig) from a request path that may carry ?ts=&sig=."""
    parts = urlsplit(raw_path)
    q = parse_qsl(parts.query, keep_blank_values=True)
    ts = next((v for k, v in q if k == "ts"), None)
    sig = next((v for k, v in q if k == "sig"), None)
    rest = [(k, v) for k, v in q if k not in ("ts", "sig")]
    path = parts.path + ("?" + urlencode(rest) if rest else "")
    return path, ts, sig


class Verifier:
    def __init__(self):
        self.used = {}
        self.lock = threading.Lock()

    def check(self, key, method, path, ts, sig, body=b""):
        if not ts or not sig:
            return False
        try:
            t = int(ts)
        except ValueError:
            return False
        now = time.time()
        if abs(now - t) > WINDOW:
            return False
        _, want = sign(key, method, path, body, ts)
        if not hmac.compare_digest(want, sig):
            return False
        with self.lock:
            for s, at in list(self.used.items()):
                if now - at > 2 * WINDOW:
                    del self.used[s]
            if sig in self.used:
                return False  # replayed
            self.used[sig] = now
        return True
