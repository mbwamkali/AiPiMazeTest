#!/usr/bin/env python3
"""A stand-in for llama-server for testing play_llm.py offline.
It answers like a sloppy model: mostly a good GO line for a random path, sometimes the
prompt's placeholder, "...So final: GO: X", the move only in the thinking, or no GO at all.
  tests/fake_llm.py 8099
"""
import http.server
import json
import random
import re
import sys

rng = random.Random(1)


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        screen = body["messages"][-1]["content"]
        paths = re.findall(r"Paths: (.+)", screen)
        exits = paths[-1].split(", ") if paths else ["look"]
        x = rng.choice(exits)
        r = rng.random()
        content, reasoning = f"I will try {x}.\nGO: {x}", ""
        if r < 0.15:
            content = "Thinking about it.\nGO: <command or room name>"
        elif r < 0.3:
            content = f"<command or room name>. So final: GO: **{x}**"
        elif r < 0.4:
            content, reasoning = "", f"long thoughts... GO: {x}"
        elif r < 0.5:
            content = "I am not sure what to do here, the house is large and I have been going round in circles for a while now."
        elif r < 0.55:
            content = "GO: map" if "map" in screen else "GO: look"
        out = {"choices": [{"message": {"content": content, "reasoning_content": reasoning}}],
               "timings": {"prompt_n": 10, "prompt_per_second": 100.0, "predicted_n": 5,
                           "predicted_per_second": 50.0, "predicted_ms": 100.0}}
        b = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
