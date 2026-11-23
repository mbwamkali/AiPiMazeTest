"""LoRA fine-tune a small chat model on ant lessons (lessons.jsonl from ant.lessons).

LoRA = instead of changing all of the model's weights, train small "adapter"
matrices beside them. Much less memory and time, and the base model stays intact.
Loss is only counted on the answer (reason + GO line), not on the screen text.

    python -m ant.train --model Qwen/Qwen3-0.6B --data lessons.jsonl --out runs/ant-test
"""
import argparse
import json
import math
import random
import time

import torch
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer


def encode(tok, ex, max_len):
    msgs = ex["messages"]
    prompt = tok.apply_chat_template(msgs[:-1], tokenize=False, add_generation_prompt=True,
                                     enable_thinking=False)
    answer = msgs[-1]["content"] + tok.eos_token + "\n"
    p = tok(prompt, add_special_tokens=False)["input_ids"]
    a = tok(answer, add_special_tokens=False)["input_ids"]
    ids = (p + a)[:max_len]
    labels = ([-100] * len(p) + a)[:max_len]
    return ids, labels


def batches(items, bs, pad_id, rng):
    rng.shuffle(items)
    for i in range(0, len(items) - bs + 1, bs):
        chunk = items[i:i + bs]
        L = max(len(x[0]) for x in chunk)
        ids = torch.full((bs, L), pad_id)
        lab = torch.full((bs, L), -100)
        att = torch.zeros((bs, L), dtype=torch.long)
        for j, (x, y) in enumerate(chunk):
            ids[j, :len(x)] = torch.tensor(x)
            lab[j, :len(y)] = torch.tensor(y)
            att[j, :len(x)] = 1
        yield ids, lab, att


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen3-0.6B")
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--epochs", type=float, default=1.0)
    ap.add_argument("--bs", type=int, default=32)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--rank", type=int, default=32)
    ap.add_argument("--max-len", type=int, default=384)
    ap.add_argument("--limit", type=int, default=0, help="use only the first N lessons (0 = all)")
    a = ap.parse_args(argv)

    dev = "cuda" if torch.cuda.is_available() else "cpu"   # ROCm shows up as "cuda" in PyTorch
    print("device:", dev, torch.cuda.get_device_name(0) if dev == "cuda" else "")
    tok = AutoTokenizer.from_pretrained(a.model)
    pad = tok.pad_token_id if tok.pad_token_id is not None else tok.eos_token_id
    model = AutoModelForCausalLM.from_pretrained(a.model, dtype=torch.bfloat16).to(dev)
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()
    model = get_peft_model(model, LoraConfig(
        r=a.rank, lora_alpha=a.rank * 2, lora_dropout=0.0, task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]))
    model.print_trainable_parameters()

    lines = open(a.data).read().splitlines()
    if a.limit:
        lines = lines[:a.limit]
    items = [encode(tok, json.loads(l), a.max_len) for l in lines]
    rng = random.Random(0)
    steps_per_epoch = len(items) // a.bs
    total = max(1, int(steps_per_epoch * a.epochs))
    print(f"{len(items)} lessons, batch {a.bs}, {total} steps")
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=a.lr, weight_decay=0.0)
    warm = max(1, total // 20)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, (s + 1) / warm) * 0.5 * (1 + math.cos(math.pi * min(1.0, s / total))))

    model.train()
    step, t0, run = 0, time.time(), None
    while step < total:
        for ids, lab, att in batches(items, a.bs, pad, rng):
            out = model(input_ids=ids.to(dev), attention_mask=att.to(dev), labels=lab.to(dev))
            out.loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step(); sched.step(); opt.zero_grad(set_to_none=True)
            step += 1
            run = out.loss.item() if run is None else 0.95 * run + 0.05 * out.loss.item()
            if step % 20 == 0 or step == total:
                el = time.time() - t0
                print(f"step {step}/{total}  loss {run:.4f}  {el:.0f}s elapsed, ~{el / step * (total - step):.0f}s left",
                      flush=True)
            if step >= total:
                break

    model = model.merge_and_unload()     # fold the adapter into the weights: one plain model file
    model.save_pretrained(a.out)
    tok.save_pretrained(a.out)
    print("saved", a.out)


if __name__ == "__main__":
    main()
