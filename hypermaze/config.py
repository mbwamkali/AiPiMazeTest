"""Optional TOML settings files for the keeper, viewer, runner and ant.

One file per component, by default ~/hypermaze/config/<component>.toml (used only if it exists;
--config PATH picks another, and then the file must exist). Precedence for every setting:

    command-line flag  >  environment variable  >  settings file  >  built-in default

Secrets are never values in these files, only paths to files that hold them (keys.json, a token
file, a CA certificate). Reading TOML needs Python 3.11+ (tomllib); without a settings file,
older Pythons work as before.
"""
import os
import sys

CONFIG_DIR = os.path.join("~", "hypermaze", "config")
COMPONENTS = ("keeper", "viewer", "runner", "ant")

# Known keys per component and their types; anything else in a file is an error, so typos show up.
SCHEMA = {
    "keeper": {"data": str, "contestant": str, "admin": str, "harness": str,
               "tls_cert": str, "tls_key": str, "keys_file": str},
    "viewer": {"listen": str, "keeper": str, "keys_file": str, "tls_cert": str, "tls_key": str,
               "public_host": str, "page": str, "flat_page": str, "matches_dir": str},
    "runner": {"server": str, "ca_cert": str, "token_file": str, "mode": str,
               "pages": int, "lines": int, "width": int},
    "ant": {"llm": str, "model": str, "llama_server": str, "threads": int, "ctx": int, "llm_port": int,
            "persona": str, "temperature": float, "max_turns": int, "house": str, "report": str,
            "harness_key_file": str, "token_file": str, "run_file": str, "ca_cert": str, "quiet": bool},
}
# Settings with no built-in default that a service needs in its file, checked by `hm config edit`.
REQUIRED = {"keeper": (), "viewer": (), "runner": (), "ant": ("model",)}
PATH_KEYS = {"data", "tls_cert", "tls_key", "keys_file", "page", "ca_cert", "token_file", "model",
             "llama_server", "harness_key_file", "run_file"}
CHOICES = {("runner", "mode"): ("none", "notes", "notebook"),
           ("ant", "persona"): ("NoMoe", "Harry", "Curlee")}


class ConfigError(Exception):
    pass


def default_path(component):
    return os.path.expanduser(os.path.join(CONFIG_DIR, f"{component}.toml"))


def parse(text, component, where="<text>"):
    """Parse and check one settings file's text. Returns a dict (paths expanded)."""
    try:
        import tomllib
    except ImportError:  # Python < 3.11
        raise ConfigError(f"{where}: reading TOML settings needs Python 3.11 or newer")
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{where}: {e}")
    schema = SCHEMA[component]
    out = {}
    for k, v in raw.items():
        if k not in schema:
            raise ConfigError(f"{where}: unknown setting {k!r} (known: {', '.join(sorted(schema))})")
        want = schema[k]
        ok = isinstance(v, want) and not (want is int and isinstance(v, bool))
        if want is float and isinstance(v, int) and not isinstance(v, bool):
            v, ok = float(v), True
        if not ok:
            raise ConfigError(f"{where}: {k} should be a {want.__name__}, not {type(v).__name__}")
        choices = CHOICES.get((component, k))
        if choices and v not in choices:
            raise ConfigError(f"{where}: {k} must be one of {', '.join(choices)}")
        out[k] = os.path.expanduser(v) if k in PATH_KEYS else v
    return out


def load(component, path=None):
    """Settings for a component: from `path` (must exist), else the default file if it exists, else {}."""
    if component not in SCHEMA:
        raise ConfigError(f"unknown component {component!r}")
    if path:
        path = os.path.expanduser(path)
        if not os.path.exists(path):
            raise ConfigError(f"{path}: no such settings file")
    else:
        path = default_path(component)
        if not os.path.exists(path):
            return {}
    with open(path, encoding="utf-8") as f:
        return parse(f.read(), component, path)


def pick(cli, env, cfg, key, default=None):
    """One setting by precedence: flag value (not None) > $env (non-empty) > cfg[key] > default."""
    if cli is not None:
        return cli
    if env and os.environ.get(env):
        return os.environ[env]
    if key in cfg:
        return cfg[key]
    return default


def missing(component, cfg):
    return [k for k in REQUIRED[component] if k not in cfg]


def read_secret(path):
    """First line of a file holding a key or token, without surrounding whitespace."""
    with open(os.path.expanduser(path), encoding="utf-8") as f:
        return f.readline().strip()


def load_or_exit(component, path=None):
    try:
        return load(component, path)
    except ConfigError as e:
        sys.exit(f"settings: {e}")
