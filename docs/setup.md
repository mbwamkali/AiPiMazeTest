# Setup

Three roles, which can all share one machine or each have their own:

- **keeper** – any Linux box: `python3 -m arena serve`
- **viewer** – usually on the keeper: `python3 -m arena.viewer`
- **ant** – a Raspberry Pi (4 or 5, 64-bit OS) running `llama-server` and `ant.pi_runner`

Everything except training is standard-library Python 3.9+. Clone the repository on every machine.

> **Never commit keys, tokens, certificates or private keys.** `.gitignore` already covers
> `keys.json`, `tls/`, `*.key`, `*.crt`, `*.pem` and `hypermaze-keeper-data/`; keep it that way.

## 1. Keeper

```bash
python3 -m arena keys --data ~/hypermaze-keeper-data          # makes keys.json (mode 600)
python3 -m arena keys --data ~/hypermaze-keeper-data --rotate # new keys, old ones stop working
```

`keys.json` holds `admin` (start/edit/stop runs) and `harness` (post turns). For the viewer add a
`viewer` entry (any random string):

```bash
python3 -c "import json,secrets,os;p=os.path.expanduser('~/hypermaze-keeper-data/keys.json');k=json.load(open(p));k['viewer']=secrets.token_hex(16);json.dump(k,open(p,'w'),indent=1)"
```

Start it. Every port binds to `127.0.0.1` unless told otherwise; open only what other machines need:

```bash
python3 -m arena serve --data ~/hypermaze-keeper-data \
  --contestant 0.0.0.0:7341 --harness 0.0.0.0:7343      # admin stays on 127.0.0.1:7342
```

Copy the **harness** key to each ant machine (for example into a file readable only by you).
The **admin** key stays on machines you control. The run token printed by `tools/arena.py new`
goes to the ant for that one run.

## 2. Your own TLS certificates (optional, recommended on a shared network)

A small private CA, one certificate for the keeper and one for the viewer. Replace
`<keeper-host>` with the keeper's hostname and `<keeper-ip>` with its address.

```bash
mkdir -p ~/hypermaze-keeper-data/tls && cd ~/hypermaze-keeper-data/tls
chmod 700 .

# the CA (keep ca.key offline if you can)
openssl req -x509 -newkey rsa:3072 -nodes -days 3650 -keyout ca.key -out ca.crt \
  -subj "/CN=hypermaze local CA"

# keeper certificate
openssl req -newkey rsa:2048 -nodes -keyout keeper.key -out keeper.csr -subj "/CN=<keeper-host>"
printf "subjectAltName=DNS:<keeper-host>,IP:<keeper-ip>,IP:127.0.0.1\n" > keeper.ext
openssl x509 -req -in keeper.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 825 \
  -extfile keeper.ext -out keeper.crt
cat keeper.crt ca.crt > keeper-chain.crt

# viewer certificate: same steps with viewer.key / viewer.csr / viewer.ext / viewer.crt
chmod 600 *.key
```

Then:

```bash
python3 -m arena serve --data ~/hypermaze-keeper-data --contestant 0.0.0.0:7341 --harness 0.0.0.0:7343 \
  --tls-cert ~/hypermaze-keeper-data/tls/keeper-chain.crt --tls-key ~/hypermaze-keeper-data/tls/keeper.key
```

TLS covers the contestant and harness ports; the admin port stays plain HTTP on `127.0.0.1`.
Copy **only `ca.crt`** to the ants and point Python at it with `SSL_CERT_FILE=/path/to/ca.crt`,
then use `https://<keeper-host>:7341` and `https://<keeper-host>:7343`.

## 3. Viewer

```bash
python3 -m arena.viewer --keeper http://127.0.0.1:7342 --listen 0.0.0.0:7344 \
  --keys ~/hypermaze-keeper-data/keys.json \
  --tls-cert ~/hypermaze-keeper-data/tls/viewer-chain.crt --tls-key ~/hypermaze-keeper-data/tls/viewer.key
```

Open `https://<keeper-host>:7344/?key=<viewer key>` (or `http://` without the TLS options).
The page keeps the key for the tab; it is never written to the viewer's log.

## 4. Ant on a Raspberry Pi

### Build llama.cpp

```bash
sudo apt install -y git cmake build-essential libcurl4-openssl-dev
git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j4 --target llama-server
```

The binary is `llama.cpp/build/bin/llama-server`.

### Get the model

Download the ant's Q4_0 `.gguf` from this repository's GitHub Releases page. To make your own:
train with `ant/train.py` (below), merge the LoRA adapter, convert with llama.cpp's
`convert_hf_to_gguf.py`, then quantise:

```bash
llama.cpp/build/bin/llama-quantize ant-f16.gguf ant-Q4_0.gguf Q4_0
```

Q4_0 is the fastest format on the Pi's ARM cores.

### Run one ant

```bash
export MODEL=/path/to/ant-Q4_0.gguf
export LLAMA_BIN=/path/to/llama.cpp/build/bin
export KEEPER_HOST=<keeper-host>
export HARNESS_KEY_FILE=/path/to/harness.key      # the harness key, one line
export ARENA_ADMIN=http://<keeper-host>:7342      # only if this machine may start runs
scripts/run-ant.sh Curlee 2 8 42
```

`run-ant.sh` starts `llama-server` on `127.0.0.1:8095`, asks the keeper for a new run, plays it
with `ant.pi_runner` and stops the server. Starting a run needs the admin key (`KEYS_FILE` or
`$ARENA_ADMIN_KEY`); if the Pi should not hold it, start the run elsewhere and run
`ant.pi_runner` directly with the token:

```bash
llama.cpp/build/bin/llama-server -m "$MODEL" -t 4 -c 4096 -np 1 --jinja --host 127.0.0.1 --port 8095 &
HOUSE_TOKEN=<token> HARNESS_KEY=$(cat /path/to/harness.key) python3 -m ant.pi_runner --persona NoMoe \
  --house http://<keeper-host>:7341 --report http://<keeper-host>:7343 --run <run id>
```

## 5. Training (GPU machine)

```bash
pip install torch transformers peft
python3 -m ant.compact --out lessons.jsonl --mazes 2000          # v2 lessons for all three ants
python3 -m ant.train --model Qwen/Qwen3-0.6B --data lessons.jsonl --out runs/ant
python3 -m ant.score --backend hf --model runs/ant --v2 --persona Harry --mazes 200
```

Training output (`runs/`, `lessons*.jsonl`) and model files (`*.gguf`) are ignored by git.

## 6. Settings files and services (`hm`)

Instead of long command lines, each machine can keep one TOML file per component in
`~/hypermaze/config/` (needs Python 3.11+):

| Component | File | Used by |
|---|---|---|
| keeper | `keeper.toml` | `python3 -m arena serve` / `python3 -m arena keys` |
| viewer | `viewer.toml` | `python3 -m arena.viewer` |
| runner | `runner.toml` | `runner/house.py` |
| ant | `ant.toml` | `python3 -m ant.llm_server` (llama-server) and `python3 -m ant.pi_runner` |

Start from the commented examples in `config/*.example.toml`. Each entry point reads its default
file if it exists, or the one given with `--config PATH`. Precedence for every setting:
command-line flag > environment variable > settings file > built-in default. Unknown settings and
wrong types stop the program with a message, so typos show up.

**Secrets never go in these files**, only paths: `keys_file` (keeper, viewer), `token_file`
(runner, ant), `harness_key_file` and `run_file` (ant), `ca_cert` (runner, ant; used only when
`$SSL_CERT_FILE` is not set). Keep those files mode 600. Real settings files are ignored by git;
only `*.example.toml` is committed.

### `scripts/hm`

```bash
scripts/hm install <keeper|viewer|runner|ant>
```

clones (or pulls) the repository into `~/hypermaze/app`, copies the example settings to
`~/hypermaze/config/<component>.toml` **only if that file is missing**, writes the systemd user
unit(s) to `~/.config/systemd/user/` with `--config` pointing at that file, and enables them.

| Component | Units |
|---|---|
| keeper | `hypermaze-keeper.service` |
| viewer | `hypermaze-viewer.service` |
| ant | `hypermaze-ant.service` (one run, not restarted) and `hypermaze-llm.service` (llama-server, on demand) |
| runner | none: `house.py` is a terminal game started by `play_llm.py` or the ant |

```bash
scripts/hm config edit ant        # $EDITOR, then checks the TOML parses and required settings are set
scripts/hm config check ant       # the check alone
scripts/hm config                 # every settings file, key/token/secret-like values masked
scripts/hm start ant              # start | stop | restart | status | logs
scripts/hm update                 # git pull, then try-restart the keeper and viewer services
scripts/hm --dry-run install ant  # print what would happen, change nothing
```

All units run with `UMask=0077`, so files they create (run logs, keys) are private to you.

**The Pi's model runs on demand.** `hypermaze-llm.service` is not started at boot. Each ant run
(`hm start ant`) starts it through `Wants=`, and the ant waits until the model has loaded.
`ant.llm_server` checks llama-server's `/slots` every 30 s and stops it after `idle_minutes`
(in `ant.toml`, default 5; 0 = never) with no request. It exits cleanly, so systemd leaves it down
until the next run. A run started within those minutes reuses the warm model. While the model is
loading, or if `/slots` can't be read, it counts as busy as long as `hypermaze-ant.service` is active.
`hm update` restarts neither the ant nor the model.

`--home DIR` (or `$HM_HOME`) uses another folder than `~/hypermaze`; `--repo URL` (or `$HM_REPO`)
another repository. User services only run while you are logged in unless lingering is on:

```bash
sudo loginctl enable-linger "$USER"
```

### One ant run with services

On the machine that holds the admin key, start a run and copy its token and id to the Pi:

```bash
tools/arena.py new --dims 2 --size 8 --rule max_moves=300
```

On the Pi (with `ant.toml` pointing `token_file`, `run_file` and `harness_key_file` at these):

```bash
umask 077
echo '<token>' > ~/hypermaze/run.token
echo '<run id>' > ~/hypermaze/run.id
scripts/hm start ant
scripts/hm logs ant
```
