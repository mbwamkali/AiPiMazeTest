#!/usr/bin/env bash
# One live ant run through the keeper:
#   scripts/run-ant.sh <persona> <dims> <size> <seed> [loops] [max_moves]
# Starts the ant's llama-server (localhost only), creates the run, plays it, stops the server.
#
# Settings (environment variables; the defaults suit a single machine):
#   MODEL         path to the ant's .gguf model                      (required)
#   LLAMA_BIN     folder with llama-server                           (default: llama-server on $PATH)
#   REPO_DIR      this repository                                    (default: the folder above this script)
#   KEEPER_HOST   host of the keeper                                 (default: 127.0.0.1)
#   KEEPER_SCHEME http or https (https if the keeper runs with --tls-cert)   (default: http)
#   ARENA_ADMIN   keeper admin URL for tools/arena.py                (default: http://$KEEPER_HOST:7342)
#   HARNESS_KEY   the keeper's harness key, or set HARNESS_KEY_FILE / KEYS_FILE instead
#   KEYS_FILE     the keeper's keys.json (needs "harness"; also used for the admin key)
#   LLM_PORT      port for the ant's llama-server                    (default: 8095)
#   THREADS       llama-server threads                               (default: 4)
#   LOG_DIR       where the llama-server log goes                    (default: current folder)
set -euo pipefail
WHO=${1:?persona: NoMoe, Harry or Curlee}; DIMS=${2:?dims}; SIZE=${3:?size}; SEED=${4:?seed}
LOOPS=${5:-0}; MAXM=${6:-300}

: "${MODEL:?set MODEL to the ant model (.gguf)}"
REPO_DIR=${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
SERVER=${LLAMA_BIN:+$LLAMA_BIN/}llama-server
KEEPER_HOST=${KEEPER_HOST:-127.0.0.1}
KEEPER_SCHEME=${KEEPER_SCHEME:-http}
export ARENA_ADMIN=${ARENA_ADMIN:-http://$KEEPER_HOST:7342}
LLM_PORT=${LLM_PORT:-8095}
THREADS=${THREADS:-4}
LOG_DIR=${LOG_DIR:-.}

if [ -z "${HARNESS_KEY:-}" ]; then
  if [ -n "${HARNESS_KEY_FILE:-}" ]; then
    HARNESS_KEY=$(tr -d '[:space:]' < "$HARNESS_KEY_FILE")
  elif [ -n "${KEYS_FILE:-}" ]; then
    HARNESS_KEY=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["harness"])' "$KEYS_FILE")
  else
    echo "set HARNESS_KEY, HARNESS_KEY_FILE or KEYS_FILE" >&2; exit 2
  fi
fi
KEYS_ARG=()
[ -n "${KEYS_FILE:-}" ] && KEYS_ARG=(--keys "$KEYS_FILE")

export PYTHONPATH="$REPO_DIR:$REPO_DIR/runner${PYTHONPATH:+:$PYTHONPATH}"
cd "$REPO_DIR"

"$SERVER" -m "$MODEL" -t "$THREADS" -c 4096 -np 1 --jinja --host 127.0.0.1 --port "$LLM_PORT" \
  --alias ant > "$LOG_DIR/llama-server-$WHO.log" 2>&1 &
SP=$!
trap 'kill $SP 2>/dev/null' EXIT
for _ in $(seq 60); do curl -s -m 2 "127.0.0.1:$LLM_PORT/health" | grep -q ok && break; sleep 1; done

NEW=$(python3 tools/arena.py "${KEYS_ARG[@]}" new --dims "$DIMS" --size "$SIZE" --seed "$SEED" \
  --loops "$LOOPS" --rule max_moves="$MAXM")
RUN=$(echo "$NEW" | awk '/^run/{print $2}')
TOK=$(echo "$NEW" | awk '/^token/{print $2}')
echo "run $RUN  ($WHO, ${DIMS}D size $SIZE seed $SEED loops $LOOPS max_moves $MAXM)"

HOUSE_TOKEN=$TOK HARNESS_KEY=$HARNESS_KEY python3 -m ant.pi_runner --persona "$WHO" \
  --house "$KEEPER_SCHEME://$KEEPER_HOST:7341" --report "$KEEPER_SCHEME://$KEEPER_HOST:7343" \
  --run "$RUN" --llm "http://127.0.0.1:$LLM_PORT" --max-turns $((MAXM * 2))
