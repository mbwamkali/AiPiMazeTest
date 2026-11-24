"""Which settings each component's file may hold, and their types (read by config.py)."""

COMPONENTS = ("keeper", "viewer", "runner", "ant")

# Known keys per component and their types; anything else in a file is an error, so typos show up.
SCHEMA = {
    "keeper": {"data": str, "contestant": str, "admin": str, "harness": str,
               "tls_cert": str, "tls_key": str, "keys_file": str},
    "viewer": {"listen": str, "keeper": str, "keys_file": str, "tls_cert": str, "tls_key": str,
               "public_host": str, "page": str, "flat_page": str, "matches_dir": str,
               "allow_new_matches": bool},
    "runner": {"server": str, "ca_cert": str, "token_file": str, "mode": str,
               "pages": int, "lines": int, "width": int},
    "ant": {"llm": str, "model": str, "llama_server": str, "threads": int, "ctx": int, "llm_port": int,
            "persona": str, "temperature": float, "max_turns": int, "house": str, "report": str,
            "harness_key_file": str, "token_file": str, "run_file": str, "ca_cert": str, "quiet": bool,
            "idle_minutes": int, "agent_name": str, "poll_seconds": int},
}
# Settings with no built-in default that a service needs in its file, checked by `hm config edit`.
REQUIRED = {"keeper": (), "viewer": (), "runner": (), "ant": ("model",)}
PATH_KEYS = {"data", "tls_cert", "tls_key", "keys_file", "page", "ca_cert", "token_file", "model",
             "llama_server", "harness_key_file", "run_file"}
CHOICES = {("runner", "mode"): ("none", "notes", "notebook"),
           ("ant", "persona"): ("Mooore", "Harry", "Curlee")}
# Old values still accepted and read as their new ones (Mooore was called NoMoe until November 2026).
ALIASES = {("ant", "persona"): {"NoMoe": "Mooore"}}
