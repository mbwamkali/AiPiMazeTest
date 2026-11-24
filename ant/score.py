"""Score an ant on held-out mazes (seeds never used for lessons), with the bot as yardstick.

Many games are played at once: every step, the current screen of each unfinished
game goes into one batch, the model answers them all together, and each answer's
GO line is sent to its own game. Batching is what makes it fast on a GPU.

Backends:
  --backend hf      PyTorch model folder (from ant.train), batched on the GPU
  --backend server  a running llama-server (OpenAI API), N games in parallel threads
  --backend bot     the strategy bot itself (the yardstick)

    python -m ant.score --backend hf --model runs/ant-test --mazes 200 --log runs/ant-test/games.log
"""
import argparse
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor

from hypermaze import Game, Maze
from hypermaze.harness import candidates

from . import compact as C
from . import bots as B
from . import personas as P
from .lessons import SYSTEM_QUIET, SYSTEM_TALK, decide, maze_mix, room_screen

HELDOUT_BASE = 900000   # lessons use 100000+, so these mazes are new to the ant


def heldout(n):
    rng = random.Random(HELDOUT_BASE)
    out = []
    for i in range(n):
        dims, size, loops, gen = maze_mix(rng)
        out.append((dims, size, loops, gen, HELDOUT_BASE + i))
    return out


def parse(reply):
    """(reason, move) from a reply: the text before GO: is the reason."""
    c = candidates(reply)
    move = c[-1] if c else None
    reason = reply.split("GO:")[0].strip() if "GO:" in reply else reply.strip()
    return reason, move


class Run:
    def __init__(self, spec, quiet, persona=None, v2=False, prompt_name=None):
        dims, size, loops, gen, seed = spec
        self.spec = spec
        self.m = Maze(dims, size, loops, seed=seed, generator=gen)
        self.g = Game(self.m, mode="notes")
        self.screen = room_screen(self.g.intro())
        self.limit = self.m.n * 4
        self.quiet = quiet
        self.persona = P.canonical(persona) if persona else None
        self.prompt_name = prompt_name
        self.v2 = v2
        self.doors = []
        self.out_chars = 0
        self.last = None
        self.maps = self.returns = 0
        self.agree = self.steps = self.noparse = 0
        self.lines = []

    @property
    def active(self):
        return not self.g.done and self.g.moves < self.limit and self.steps < self.limit * 2

    def messages(self):
        if self.v2:
            msgs, self.doors = C.messages(self.persona, self.screen, self.last, self.quiet, self.prompt_name)
            return msgs
        if self.persona:
            return P.messages(self.persona, self.screen, self.last, self.quiet, self.prompt_name)
        return [{"role": "system", "content": SYSTEM_QUIET if self.quiet else SYSTEM_TALK},
                {"role": "user", "content": self.screen}]

    def apply(self, reply):
        if self.persona:
            return self.apply_persona(reply)
        self.steps += 1
        bot_to, _ = decide(self.g)
        reason, move = parse(reply)
        here = self.m.names[self.g.cur]
        if move is None:
            self.noparse += 1
            move = "look"
        if bot_to is not None and move.lower() == self.m.names[bot_to].lower():
            self.agree += 1
        self.lines.append(f"[{here}] {reason}  -> GO: {move}" if reason else f"[{here}] -> GO: {move}")
        self.screen = room_screen(self.g.send(move))

    def apply_persona(self, reply):
        self.steps += 1
        self.out_chars += len(reply)
        if self.v2:
            if not self.doors:
                C.messages(self.persona, self.screen, self.last, self.quiet, self.prompt_name)
            move = C.parse(reply, self.doors)
            rest = [l for l in reply.splitlines() if "GO:" not in l and l.strip()]
            reason = rest[0].strip() if rest else ""
        else:
            reason, move = parse(reply)
        here = self.m.names[self.g.cur]
        if move is None:
            self.noparse += 1
            move = "look"
        if move.lower() == "map":
            self.maps += 1
        elif self.last and self.last[0] == "walk" and self.last[2] and move.lower() == self.last[1].lower():
            self.returns += 1
        self.lines.append(f"[{here}] {reason}  -> GO: {move}" if reason else f"[{here}] -> GO: {move}")
        self.screen, self.last = B.step(self.g, move, self.last)

    def result(self):
        g = self.g
        return {"seed": self.spec[4], "dims": self.spec[0], "size": self.spec[1], "loops": self.spec[2],
                "solved": g.result == "SOLVED", "moves": g.moves, "shortest": self.m.shortest,
                "invalid": g.bad, "noparse": self.noparse, "agree": self.agree, "steps": self.steps,
                "maps": self.maps, "returns": self.returns, "out_chars": self.out_chars}


def run_hf(runs, model_dir, batch, max_new, temperature=0.0):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(model_dir)
    tok.padding_side = "left"   # pad on the left so every answer starts right after its prompt
    model = AutoModelForCausalLM.from_pretrained(model_dir, dtype=torch.bfloat16).to("cuda").eval()
    while True:
        act = [r for r in runs if r.active][:batch]
        if not act:
            break
        prompts = [tok.apply_chat_template(r.messages(), tokenize=False, add_generation_prompt=True,
                                           enable_thinking=False) for r in act]
        enc = tok(prompts, return_tensors="pt", padding=True, add_special_tokens=False).to("cuda")
        with torch.no_grad():
            samp = {"do_sample": True, "temperature": temperature, "top_p": 0.95} if temperature > 0 else {"do_sample": False}
            out = model.generate(**enc, max_new_tokens=max_new, **samp,
                                 pad_token_id=tok.pad_token_id or tok.eos_token_id)
        for r, seq in zip(act, out[:, enc["input_ids"].shape[1]:]):
            r.apply(tok.decode(seq, skip_special_tokens=True))


def run_server(runs, url, parallel, max_new, temperature=0.0):
    import urllib.request

    def ask(r):
        body = json.dumps({"messages": r.messages(), "max_tokens": max_new, "temperature": temperature,
                           "chat_template_kwargs": {"enable_thinking": False}}).encode()
        req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", body,
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.load(resp)["choices"][0]["message"]["content"]

    def play(r):
        while r.active:
            r.apply(ask(r))

    with ThreadPoolExecutor(parallel) as ex:
        list(ex.map(play, runs))


def run_bot(runs):
    for r in runs:
        while r.active:
            to, kind = decide(r.g)
            r.apply(f"GO: {r.m.names[to]}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["hf", "server", "bot"], default="hf")
    ap.add_argument("--model", help="model folder (hf)")
    ap.add_argument("--url", default="http://127.0.0.1:8090", help="llama-server (server)")
    ap.add_argument("--mazes", type=int, default=200)
    ap.add_argument("--batch", type=int, default=64, help="games per GPU batch (hf) / parallel games (server)")
    ap.add_argument("--quiet", action="store_true", help="quiet mode: GO line only")
    ap.add_argument("--max-new", type=int, default=48)
    ap.add_argument("--persona", choices=P.NAMES + tuple(P.ALIASES), help="play as one of the stooge ants")
    ap.add_argument("--prompt-name", help="name used in the persona's system prompt (default: the trained one)")
    ap.add_argument("--v2", action="store_true", help="fast format: numbered doors, GO first, comments only at interesting moments")
    ap.add_argument("--temperature", type=float, default=0.0, help="0 = always the likeliest answer; >0 = some dice")
    ap.add_argument("--log", help="write every game's moves and reasons here")
    ap.add_argument("--json", help="write per-game results here")
    a = ap.parse_args(argv)

    runs = [Run(s, a.quiet, a.persona, a.v2, a.prompt_name) for s in heldout(a.mazes)]
    t0 = time.time()
    if a.backend == "hf":
        run_hf(runs, a.model, a.batch, a.max_new, a.temperature)
    elif a.backend == "server":
        run_server(runs, a.url, a.batch, a.max_new, a.temperature)
    else:
        run_bot(runs)
    el = time.time() - t0

    res = [r.result() for r in runs]
    solved = [x for x in res if x["solved"]]
    steps = sum(x["steps"] for x in res)
    print(f"backend={a.backend} model={a.model} persona={a.persona} prompt_name={a.prompt_name or 'trained'} quiet={a.quiet} temperature={a.temperature}")
    print(f"solved {len(solved)}/{len(res)}  "
          f"avg moves/shortest (solved) {sum(x['moves'] / max(1, x['shortest']) for x in solved) / max(1, len(solved)):.2f}")
    print(f"agrees with bot {sum(x['agree'] for x in res) / max(1, steps):.1%} of moves, "
          f"invalid moves {sum(x['invalid'] for x in res)}, unreadable replies {sum(x['noparse'] for x in res)}")
    if a.persona:
        print(f"map checks {sum(x['maps'] for x in res)}, trips back to a half-done room {sum(x['returns'] for x in res)}, "
              f"avg reply {sum(x['out_chars'] for x in res) / max(1, steps):.0f} chars")
    print(f"{steps} turns in {el:.1f}s = {steps / max(el, 1e-9):.1f} moves/s overall")
    if a.log:
        with open(a.log, "w") as f:
            for r in runs:
                x = r.result()
                f.write(f"=== seed {x['seed']} {x['dims']}D size {x['size']} loops {x['loops']}: "
                        f"{'SOLVED' if x['solved'] else 'not solved'} in {x['moves']} (shortest {x['shortest']})\n")
                f.write("\n".join(r.lines) + "\n\n")
    if a.json:
        with open(a.json, "w") as f:
            json.dump(res, f)


if __name__ == "__main__":
    main()
