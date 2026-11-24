"""Score a v3 ant on held-out relay games (seeds never used for lessons), with the lesson bot as yardstick.

Each game is a relay of 1 to 3 ants in one maze with shared walls (ant/relay3.py). Every step the
screens of all unfinished games go into one batch and the model answers them together.

    python3 -m ant.score3 --backend hf --model runs/stooges-v3 --games 200 --json score-v3.json
    python3 -m ant.score3 --backend bot --games 200                       (the yardstick)
"""
import argparse
import json
import random
import time

from hypermaze.core import Maze
from . import view3 as V
from .lessons import maze_mix
from .lessons3 import MIXES, maze_2d
from .relay3 import RelayGame

HELDOUT_BASE = 950000


WALLS = {"wood": {}, "mixed": MIXES[1][0], "hard": MIXES[2][0]}


def heldout(n, size=None, walls=None, shapes="2d"):
    rng = random.Random(HELDOUT_BASE)
    out = []
    for i in range(n):
        dims, sz, loops, gen = (2, size, 0.25, "dfs") if size else maze_2d(rng) if shapes == "2d" else maze_mix(rng)
        ants = [1, 2, 3][i % 3]
        mix = WALLS[walls] if walls else MIXES[i % len(MIXES)][0]
        out.append((Maze(dims, sz, loops, seed=HELDOUT_BASE + i, generator=gen), ants, mix, HELDOUT_BASE + i))
    return out


class Game:
    def __init__(self, spec, quiet, briefing="team", teacher="tagged"):
        maze, ants, mix, seed = spec
        self.g = RelayGame(maze, ants=ants, mix=mix, seed=seed, briefing=briefing, teacher=teacher)
        self.quiet = quiet
        self.noparse = self.steps = self.agree = 0
        self.options = None

    @property
    def active(self):
        return not self.g.done

    def messages(self):
        text, self.options = self.g.screen()
        return V.messages(self.g.cur.persona, text, self.quiet)

    def apply(self, reply):
        self.steps += 1
        if self.options is None:
            self.messages()
        want, _ = self.g.teach()
        act = V.parse(reply, self.options)
        if act is None:
            self.noparse += 1
        elif act == self.options.get(want):
            self.agree += 1
        self.g.apply(act)
        self.options = None


def run_hf(games, model_dir, batch, max_new, temperature):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(model_dir)
    tok.padding_side = "left"
    model = AutoModelForCausalLM.from_pretrained(model_dir, dtype=torch.bfloat16).to("cuda").eval()
    t0, last, step = time.time(), 0, 0
    while True:
        act = [g for g in games if g.active][:batch]
        if not act:
            break
        step += 1
        if time.time() - last > 60:              # progress for long runs
            last = time.time()
            print(f"  [{time.time() - t0:.0f}s] step {step}: {sum(not g.active for g in games)}/{len(games)} games done",
                  flush=True)
        prompts = [tok.apply_chat_template(g.messages(), tokenize=False, add_generation_prompt=True,
                                           enable_thinking=False) for g in act]
        enc = tok(prompts, return_tensors="pt", padding=True, add_special_tokens=False).to("cuda")
        with torch.no_grad():
            samp = {"do_sample": True, "temperature": temperature, "top_p": 0.95} if temperature > 0 else {"do_sample": False}
            out = model.generate(**enc, max_new_tokens=max_new, **samp, pad_token_id=tok.pad_token_id or tok.eos_token_id)
        for g, seq in zip(act, out[:, enc["input_ids"].shape[1]:]):
            g.apply(tok.decode(seq, skip_special_tokens=True))


def run_bot(games):
    for g in games:
        while g.active:
            g.messages()
            key, _ = g.g.teach()
            g.apply(f"GO: {key}")


def summary(games):
    rs = [dict(g.g.result(), noparse=g.noparse, steps=g.steps, agree=g.agree) for g in games]
    out = {"games": len(rs), "all_out": sum(r["all_out"] for r in rs) / len(rs),
           "noparse": sum(r["noparse"] for r in rs) / max(1, sum(r["steps"] for r in rs)),
           "agree": sum(r["agree"] for r in rs) / max(1, sum(r["steps"] for r in rs))}
    for n in (1, 2, 3):
        sel = [r for r in rs if r["ants"] == n]
        if sel:
            out[f"per_ant_{n}"] = round(sum(r["total_moves"] / n for r in sel) / len(sel), 1)
            out[f"vs_shortest_{n}"] = round(sum(r["total_moves"] / n / r["shortest"] for r in sel) / len(sel), 2)
            out[f"writes_{n}"] = round(sum(r["writes"] for r in sel) / len(sel), 1)
    return out, rs


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--backend", choices=["hf", "bot"], default="bot")
    ap.add_argument("--model")
    ap.add_argument("--games", type=int, default=200)
    ap.add_argument("--size", type=int, help="all 2D mazes of this size (default: the lesson mix of shapes)")
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--max-new", type=int, default=32)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--shapes", choices=["2d", "mix"], default="2d", help="maze shapes when --size is not given")
    ap.add_argument("--walls", choices=sorted(WALLS), help="all games with these walls (default: the lesson mix)")
    ap.add_argument("--briefing", choices=["team", "size", "none"], default="team")
    ap.add_argument("--teacher", choices=["strict", "strictC", "strictR", "tagged"], default="tagged")
    a = ap.parse_args(argv)
    games = [Game(s, a.quiet, a.briefing, a.teacher) for s in heldout(a.games, a.size, a.walls, a.shapes)]
    t0 = time.time()
    if a.backend == "hf":
        run_hf(games, a.model, a.batch, a.max_new, a.temperature)
    else:
        run_bot(games)
    out, rs = summary(games)
    out["seconds"] = round(time.time() - t0)
    print(json.dumps(out))
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"summary": out, "games": rs}, f)


if __name__ == "__main__":
    main()
