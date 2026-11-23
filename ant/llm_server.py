"""Start the ant's llama-server from the ant settings file (stdlib only).

    python3 -m ant.llm_server [--config ~/hypermaze/config/ant.toml] [--dry-run]

Uses model, llama_server, threads, ctx and llm_port from the settings; llama-server always
listens on 127.0.0.1 only. The process is replaced by llama-server (handy under systemd).
"""
import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
    ap.add_argument("--dry-run", action="store_true", help="print the command instead of running it")
    a = ap.parse_args(argv)
    cfg = config.load_or_exit("ant", a.config)
    cmd = command(cfg, a.model, a.llama_server, a.threads, a.port, a.ctx)
    if a.dry_run:
        print(" ".join(cmd))
        return 0
    exe = cmd[0] if os.sep in cmd[0] else shutil.which(cmd[0])
    if not exe:
        sys.exit(f"{cmd[0]} not found: set llama_server in the ant settings")
    os.execv(exe, cmd)


if __name__ == "__main__":
    sys.exit(main())
