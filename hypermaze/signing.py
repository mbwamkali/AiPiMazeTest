"""Request signing: hex HMAC-SHA256 over method, path, timestamp and the body's hash."""
import hashlib
import hmac
import time


def sign(key, method, path, body=b"", ts=None):
    """(timestamp, signature) for one request; key is hex."""
    ts = str(int(time.time()) if ts is None else ts)
    msg = "\n".join([method.upper(), path, ts, hashlib.sha256(body).hexdigest()]).encode()
    return ts, hmac.new(bytes.fromhex(key), msg, hashlib.sha256).hexdigest()
