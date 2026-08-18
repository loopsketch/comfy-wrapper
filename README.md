# comfy-wrapper

English | [日本語](README.ja.md)

Run open-weight video and image models on a Google Colab GPU and use them from your
machine as a plain, key-authenticated HTTP API.

comfy-wrapper drives [ComfyUI](https://github.com/comfyanonymous/ComfyUI) on a Colab
runtime, puts a small FastAPI service in front of it, and tunnels that service to your
laptop over SSH. All the host needs is Docker and `cw`, a dependency-free CLI.

```bash
uv tool install --editable /path/to/comfy-wrapper

cw up --setup image --models z-image --gpu L4 --max 60
cw image "a pug on a sunlit windowsill" --out ./hero.png
cw stop
```

```
POST /v1/generate  ->  job_id   (202, generation runs in the background)
GET  /v1/jobs/{id} ->  status
GET  /v1/jobs/{id}/video -> mp4
```

Design decisions, measured numbers, model selection and troubleshooting live in
[docs/](docs/) (Japanese).

## Features

- **One API for several models.** MiniMax H3, Wan2.2 (14B MoE / TI2V-5B / S2V),
  LTX-2.3 (fp8 / GGUF / IC-LoRA) and LTX-2.5 for video; Z-Image and Qwen-Image / Qwen-Image-Edit
  for stills. Switching models is a field in the request body.
- **Audio-capable generation.** MiniMax H3 generates video and stereo audio in a single
  pass and accepts image / video / audio references; LTX-2.3 and LTX-2.5 also output audio;
  Wan2.2-S2V is driven by an input audio track.
- **ComfyUI is never exposed.** ComfyUI has no authentication, so it stays bound to
  `127.0.0.1` on the runtime. Only the FastAPI service is reachable, and only through
  an SSH tunnel with a bearer key.
- **Plaintext keys never leave your machine.** `cw key issue` issues keys locally and
  ships only SHA-256 hashes to the runtime.
- **Callers need to know three fewer things.** `cw` hides where the repository lives,
  what the compose services are called, and what the paths inside the container are.
  Generation runs on the host's own Python (stdlib only, zero dependencies), so
  `--ref ./ref.png` and `--out ./hero.png` stay relative to your current directory.
- **No CUDA and no ComfyUI on the host.** Three containers (`client`, `colab`, `tunnel`),
  all `python:3.12-slim`.
- **A watchdog that stops the meter.** Colab bills GPU wall-clock time, so
  `colab_watch.sh` keeps the runtime alive while work is running and shuts it down
  (after collecting outputs) once it goes idle or hits a time limit.
- **Finishing pass built in.** `POST /v1/postprocess` runs frame interpolation and
  upscaling to reach 4K / 24p.

## Architecture

```
[client container]   your machine: submits jobs, collects results
  |  http://tunnel:8000 + Bearer <key>
  v
[tunnel container]   ssh -L, using `colab ssh --proxy-mode` as ProxyCommand
  |
  |  ~~~ Colab runtime ~~~
  v
[FastAPI :8000]      auth, job queue, input handling
     |  127.0.0.1 only
     v
[ComfyUI :8188]      workflow execution
```

Inside the containers the endpoint is `http://tunnel:8000`; from the host `cw` talks to
the same tunnel on `http://127.0.0.1:8000`. Neither changes between sessions.

## Requirements

| | |
|---|---|
| Host | Linux / macOS / WSL2 with Docker and Compose **v2** (`docker compose`, not `docker-compose`). Ports 8000 and 8188 free |
| `cw` | Python **3.11+**, installed with `uv` or `pipx`. The package has no dependencies and does not need ffmpeg (`cw post` reads mp4 headers itself; ffprobe is only used for non-mp4 inputs) |
| Google account | Colab **compute units are required** — a free account cannot allocate a GPU runtime from the CLI |
| Browser | Once, for the initial OAuth code paste. It may be a browser on a different machine, so headless hosts are fine |
| SSH key | ed25519 or ecdsa. RSA is rejected |
| Network | `colab.pa.googleapis.com` and `oauth2.googleapis.com` from the host; `github.com` and `huggingface.co` from the runtime |
| GPU | L4 (24GB) is the default and runs every model except `ltx-2.3` fp8, which needs an A100 |

A Hugging Face token in `.colab/hf-token` is optional but strongly recommended:
unauthenticated downloads are throttled hard (measured 622MB/s vs 8MB/s), and download
time is billed GPU time.

## Quick start

### 0. Install `cw`

```bash
uv tool install --editable /path/to/comfy-wrapper   # `pipx install -e .` works too
cw --help
```

`--editable` matters: `cw` uses this source tree directly. **Generation (`image`,
`video`, `post`, `jobs`, `models`) runs on the host's Python**, and only the operational
commands (`up`, `stop`, `status`, ...) reach for `docker compose` internally. If you move
the repository, point `COMFY_WRAPPER_HOME` at it.

### 1. One-time setup

```bash
docker compose up -d colab                  # must stay running (see note below)
cw auth login                               # prints an authorization URL
cw auth login --code <code from browser>    # completes the login

cw init ssh                                 # create the SSH key (ed25519)
cw init hf < token.txt                      # store the Hugging Face token (--token works too)
cw init                                     # list whatever is still missing
```

Tokens and keys live in `.colab/` (git-ignored). The `colab` container refreshes the
OAuth token every 30 minutes on its own, so the browser step is needed only once.

`cw init` exists so nobody has to remember the storage layout or the in-container paths.
`cw init ssh` leaves an existing key alone (replacing it means re-establishing the
tunnel). The Hugging Face token is optional on paper and mandatory in practice, so
`cw init` warns when it is missing.

> Keep the `colab` service running. `colab new` spawns a detached keep-alive daemon; if
> you use `docker compose run --rm` the container — and the daemon with it — disappears
> when the command returns, and the runtime gets reaped for idleness.

### 2. Allocate a runtime and build it

```bash
cw up --setup image --models z-image --gpu L4 --max 60
```

Allocate → ship code and keys → build → open the tunnel, unattended, and **the session
stays up** (unlike `cw run`, which stops it). Installing ComfyUI and fetching weights
takes 15-25 minutes. `--setup` is `image` (stills), `video` (Wan2.2 / LTX) or `h3`
(MiniMax H3). `--max` is the watchdog's cap in minutes; past it the watchdog stops the
runtime for you.

```bash
cw status     # compose, sessions, watchdog and reachability on one screen
```

### 3. Generate

Paths are **relative to your current directory**, so it does not matter where you run it.

```bash
# stills (waits for the job and writes the png)
cw image "a cat on a neon-lit rooftop" --model z-image --aspect 9x16 --out ./cat.png
cw image "the person in image 1, sitting on a park bench" --ref ./ref.png

# video: pass an image for i2v, omit it for t2v
cw video ./cat.png --model ltx-2.5 --out ./clip.mp4
cw video --prompt "rain on neon streets" --duration 5

# finishing (frame interpolation + upscale)
cw post ./clip.mp4 --size 4k --multiplier 2

# submit a batch, collect it later
cw image "..." --no-wait
cw jobs

cw models     # what each model can do, and what a second of output costs
```

Outputs land in your current directory; the **job ledger lives in the repository under
`.colab/jobs/`, with absolute output paths**. Callers' projects stay clean, and `cw jobs`
collects to the right place from any directory.

### 4. Stop

```bash
cw stop
```

Watchdog → session → **ask the server** → tunnel, in that order. A session leaving the
local ledger is not the same as the remote runtime stopping, so the last thing printed is
what the server says. Billing is per GPU-hour: confirm the session is really gone.

If that last listing still shows entries starting with `[?]`, those are allocations that
fell out of the ledger. `cw stop -s <name>` cannot reach them; release them with
`cw stop --orphans` (that runs `src/scripts/colab_unassign.py`; `--all` folds named
sessions too).

**Do not leave `tunnel` running.** With no session present, its ProxyCommand
(`colab ssh -s comfy`) allocates a runtime under that name — observed as a CPU runtime,
so no compute units are spent, but it is an allocation you did not ask for. `cw stop`
folds the tunnel too.

### Unattended runs

`cw run` does allocate → build → run → stop in one command, and always stops the session
even on failure. Everything after `--` is passed to Python in the `client` container, so
**that part alone uses container paths**.

```bash
cw run --setup video --models ltx-2.5 --gpu L4 --max 60 -- \
  src/scripts/measure_video.py submit works/still.png --model ltx-2.5 --aspect 9x16
```

### Under the hood (when something breaks)

When the session is alive but nothing reaches it, restart only the tunnel. **Do not
re-allocate** — that throws away a live runtime and takes a GPU again.

```bash
cw tunnel restart     # up / stop / logs are there too
```

`cw` runs the existing scripts as they are, so you can call them directly.

```bash
docker compose ps
docker compose logs --tail 50 tunnel
src/scripts/colab.sh sessions
src/scripts/colab.sh exec -s comfy -f src/scripts/colab_setup_status.py
src/scripts/colab_watch.sh --status
```

If you cannot install Python on the host, generation still runs in the container —
that is what `docker/Dockerfile.client` is for. It ships ffmpeg, which also makes it the
way out when you need to finish something that is not an mp4.

```bash
docker compose run --rm client src/scripts/generate_image.py submit "..." --model z-image
docker compose run --rm client src/scripts/generate_video.py --first-frame works/still.png
```

## Using it from Claude Code

The skills are written against `cw`, so they work **from any project**. Install `cw`,
then install the skills you want.

```bash
uv tool install --editable /path/to/comfy-wrapper

cd /path/to/your-project
cw init skills            # install into this project (--global targets ~/.claude)
cw init skills --no-h3    # skip MiniMax's official h3-prompt-writing
```

`colab-comfy` and `ltx-prompt` install from the clone rather than GitHub, so they always
match the `cw` you have installed; `h3-prompt-writing` is fetched from MiniMax. It drives
the [skills CLI](https://github.com/vercel-labs/skills), so the manual route is the same:

```bash
npx skills add /path/to/comfy-wrapper --skill colab-comfy --skill ltx-prompt
npx skills add https://github.com/loopsketch/comfy-wrapper --skill colab-comfy
npx skills add https://github.com/MiniMax-AI/MiniMax-H3 --skill h3-prompt-writing
```

- **`colab-comfy`** — running the thing. Ask for an image or a clip and it supplies the
  whole procedure: check state, allocate, build, generate, stop, plus the rules that keep
  the meter from running. It reads the model list from `cw models` instead of copying it,
  so it does not go stale when a model is added.
- **`ltx-prompt`** — writing for LTX-2.5: prose, multi-shot cuts, the 8k+1 duration grid,
  and the phrasings that do nothing.
- **`h3-prompt-writing`** — writing for MiniMax H3 is left to the
  [official skill](https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills). What is
  specific to this wrapper (the duration grid, how the notation's tasks map onto the
  commands, the extra weights `r2v` needs) lives in `colab-comfy`.

This repository's `.claude/skills/` is the source the CLI installs from, so working here
needs no install step (`.claude/settings.json` is personal and stays out of git).

## Usage

From your own code, go through `lib/colab_link.py`; it resolves the endpoint and key,
retries transient failures, and reports why a request failed.

```python
import sys, base64, json
sys.path.insert(0, "/path/to/comfy-wrapper/src")   # /app/src inside the containers
from lib import colab_link

endpoint = colab_link.read_endpoint()      # tunnel:8000 in a container, 127.0.0.1:8000 on the host
key = colab_link.require_api_key()         # reads .colab/colab-api-key

still = base64.b64encode(open("works/still.png", "rb").read()).decode()
status, body = colab_link.request(
    endpoint, key, "POST", "/v1/generate",
    {
        "model": "ltx-2.3-gguf",
        "task": "i2v",
        "prompt": "slow dolly-in, neon reflections on wet asphalt",
        "first_frame": still,
        "duration": 5.0,
        "aspect": "9x16",
        "output_width": 1080, "output_height": 1920,
    },
)
job = json.loads(body)["job_id"]           # 202; generation runs in the background
```

Submissions return immediately, so the intended pattern is to queue every shot first and
collect them afterwards — model weights are loaded once per session.

```bash
curl -X POST "$COLAB_ENDPOINT/v1/generate" \
  -H "Authorization: Bearer $COLAB_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"task":"t2v","prompt":"rain on neon streets","duration":5,"aspect":"16x9"}'
```

## Using it from another project

**Run one instance of this repository per machine, and have other projects ask it over
HTTP instead of vendoring it.** The OAuth token, SSH key and session ledger in
`.colab/` can only exist once; a second copy fights the first for the runtime and you
get `Already-active SSH session (HTTP 429)` or an idle-pruned session. Do not give the
calling project its own `colab` / `tunnel` services.

There are two routes: **`cw` when a person asks, HTTP when code asks.**

```bash
# in the calling project
uv tool install --editable /path/to/comfy-wrapper
cd /path/to/your-project
cw image "..." --out ./assets/hero.png     # output here, ledger in comfy-wrapper
```

`cw` only ever points at the one repository on the machine, so nothing gets duplicated.
Generated files land where you ran it; the job ledger and the keys stay on the
comfy-wrapper side.

For code, one shared network keeps the endpoint resolving as `http://tunnel:8000`, so the
caller changes neither its code nor its configuration, and no port is exposed on the
host. The caller just repeats the **same declaration** this repository uses.

```yaml
# the caller's docker-compose.yml
services:
  app:
    networks: [default, comfy]
networks:
  comfy:
    name: comfy-net
```

**Do not mark it `external: true`.** An external network that does not exist yet makes
Compose fail to start at all — the caller could no longer run anything, generation-related
or not, and neither could this repository on its own. With a fixed name and no `external`,
whichever side comes up first creates it and the other joins. No `docker network create`
step is needed.

Issue **one key per project** — sharing `.colab/colab-api-key` means you cannot revoke
one caller without breaking the others.

```bash
cw key issue --name <project>   # the plaintext key is shown only here
cw key list
cw key push                     # ship the hashes to the runtime
cw key revoke --id <id>
```

Put the printed `COLAB_API_KEY=...` in the caller's `.env`. **Only SHA-256 hashes ever
reach the runtime**, which is why issuing and shipping (`cw key push`) are two steps.

**Read the model table from `GET /v1/models` instead of copying it.** It returns
resolutions, audio support, throughput and weight sizes. A copy goes stale the moment a
model is added here — that is exactly what happened with `ltx-2.5`, which this repository
supported while the caller rejected it as unknown.

Only `ready` (are the weights actually present) is runtime state: with ComfyUI down it is
null and `ready_known` is false. **The catalog itself answers without a runtime**, but the
API only exists on the runtime, so cache it on your side if you use it for cost estimates —
those need to work *before* a GPU is claimed.

The caller only needs to know three things:

- Submission returns 202 and generation runs in the background. **Do not resubmit
  something that got through** — a duplicate generation throws away GPU time.
- The job ledger lives only in the server's memory. Stop the runtime and it is gone.
- Claiming and stopping the runtime is this server's job. If `/health` does not
  answer, it simply is not running right now.

[music-video-creator2](https://github.com/loopsketch/music-video-creator2) is a worked
example — it used to carry this code inside it, and is the project this was extracted from.

## Models

`cw models` prints the same table (with no runtime too — only "is it loaded" goes
unknown).

One model per session: each is 30-45GB and they do not fit in `/content`, let alone VRAM,
at the same time.

| `model` | Weights | Audio | fps | Native resolution | Tasks |
|---|---|---|---|---|---|
| `minimax-h3` (default) | 42.5GB | yes | 24 | short edge 768, max 768x1344 | t2v / i2v / first+last frame / **r2v** |
| `wan2.2` (14B MoE) | ~38GB | no | 16 | 832x480, 1280x720 | t2v / i2v / first+last frame |
| `wan2.2-5b` (TI2V) | ~18GB | no | 24 | 1280x704 | t2v / i2v |
| `wan2.2-s2v` (S2V 14B) | ~25GB | input is carried over | 16 | 832x480 | **audio-driven** |
| `ltx-2.3` (22B fp8) | ~42GB | yes | 8-50 (default 25) | 1280x704, 1920x1088 | t2v / i2v |
| `ltx-2.3-gguf` (Q4_K_M) | ~28GB | yes | 8-50 (default 25) | same | t2v / i2v |
| `ltx-2.3-ic` (+IC-LoRA) | ~29GB | yes | same | same | i2v + **reference sheet** |
| `ltx-2.5` (22B int8) | ~40GB | yes | 8-50 (default 24) | same | t2v / i2v / **first+last frame** |

- **Reference-conditioned generation (`r2v`) is MiniMax H3 only.** References are
  addressed from the prompt as `<Picture 1>`, `<Video 1>`, `<Audio 1>`.
- **`ltx-2.3-gguf` is the fastest** on an L4: 5.6x MiniMax H3 and 2.3x Wan2.2, with audio
  at 25fps. It is the default choice for batches of i2v shots.
- **`ltx-2.3` fp8 does not fit on an L4** (29GB checkpoint) — use the GGUF build there
  and keep fp8 for an A100.
- **`ltx-2.5` does fit on an L4** in int8 (21.5GB transformer). Both 480p and 720p run
  without partial offload, with audio at 24fps. It is **the only LTX build that takes a
  last frame**, and that path is a single full-resolution pass (slower than the two-pass
  one).
- **There is no 2.5 build of the IC-LoRA reference sheet yet** — stay on `ltx-2.3-ic` if
  you need reference sheets.
- `duration` is rounded up to each model's latent frame grid; the response reports the
  actual length in `seconds`.

Measured on an L4, 480p, ~5s, 9:16, identical seed and prompt:

| model | first run | warm run | per second of video | per shot |
|---|---|---|---|---|
| `minimax-h3` (fp8, 20 steps) | 471s | 400s | 77.4s | JPY 2.02 |
| `wan2.2` (4-step distill) | 213s | 165s | 32.6s | JPY 0.83 |
| `ltx-2.3-gguf` (Q4_K_M) | **127s** | **72s** | **14.0s** | **JPY 0.36** |
| `ltx-2.5` (22B int8) | ~201s (sum) | 105s | 20.8s | JPY 0.53 |

Only the `ltx-2.5` first-run figure is a sum: the 96s load was measured on a 2s run, and
the 5s cold run was not measured. Other conditions from the same session:

| Condition | Time |
|---|---|
| 480p (512x832) / 2s / i2v | 148s cold, 52s warm |
| 720p (704x1280) / 2s / i2v | 96s |
| 480p (512x832) / 2s / first+last frame | 134s (single full-resolution pass, so heavier) |

VRAM during generation peaked at 21.4/23.0GB with no `loaded partially` lines at all,
so **720p runs natively on an L4**.

Numbers are from an L4 with weights already downloaded; your own timings are worth
measuring with `scripts/measure_video.py`, which separates the cold and warm runs.

## API

Every endpoint requires `Authorization: Bearer <key>` except `/health`.

| Method | Path | Description |
|---|---|---|
| GET | `/health` | No auth. Tunnel liveness and per-model weight readiness |
| GET | `/v1/info` | GPU name, VRAM, loaded model |
| GET | `/v1/models` | What each model can do, so callers need no model table of their own |
| POST | `/v1/generate` | Queue a video job. Returns 202 with `job_id` |
| POST | `/v1/images/generate` | Queue a still-image job |
| POST | `/v1/postprocess` | Queue interpolation / upscaling of an existing mp4 |
| GET | `/v1/jobs/{id}` | Status, queue position, error |
| GET | `/v1/jobs/{id}/video` | Finished mp4 |
| GET | `/v1/jobs/{id}/image` | Finished png |
| DELETE | `/v1/jobs/{id}` | Cancel a running job |
| GET | `/v1/jobs` | List jobs |

Main fields of `POST /v1/generate`:

| Field | Default | Description |
|---|---|---|
| `model` | `minimax-h3` | See the model table above |
| `task` | `i2v` | `t2v` / `i2v` / `r2v` (`r2v` is H3 only) |
| `prompt` | — | References are addressed as `<Picture i>` / `<Video k>` / `<Audio j>` |
| `negative` | per-model | Wan / LTX only; H3 ignores it |
| `first_frame` / `last_frame` | — | base64 or data URI. Required for `i2v`. `last_frame` is H3, `wan2.2` and `ltx-2.5` only, always alongside `first_frame` |
| `ref_images` / `ref_videos` / `ref_audios` | `[]` | For `r2v`. Max 9 / 3 / 3 |
| `audio` | — | Driving audio for `wan2.2-s2v` |
| `duration` | 5.0 | Seconds, rounded up to the model's frame grid |
| `aspect` | auto | `16x9` `9x16` `1x1` `4x3` `3x4` `21x9`. Inferred from `first_frame` if omitted |
| `megapixels` | 0.4 | Generation canvas (1MP = 1024x1024); 0.4 gives 864x480 at 16:9 |
| `fps` | per-model | LTX only (8-50) |
| `steps` | per-model | H3 20; Wan 4 with the distill LoRA, 20 without |
| `lightning` | `true` | Use the 4-step distill LoRA on `wan2.2` (14B) |
| `seed` | -1 | -1 for random |
| `output_width` / `output_height` | — | Set both to get exactly that size (center crop) |
| `upscale_model` | — | Filename under `upscale_models/`; lanczos if omitted |

`POST /v1/images/generate` takes `model` (`z-image` / `qwen-image` / `qwen-image-edit`),
`prompt`, `aspect`, `ref_images` (passing any switches to the edit path regardless of
model), `loras`, `seed` and `steps`.

## Post-processing

`POST /v1/postprocess` runs frame interpolation and upscaling using stock ComfyUI nodes,
following the official blueprints:

```
LoadVideo -> GetVideoComponents -> FrameInterpolate -> ImageUpscaleWithModel
          -> ImageScale -> CreateVideo -> SaveVideo
```

```bash
cw post ./clip.mp4 --size 4k-portrait --multiplier 2
cw jobs
```

The models total 226MB and are installed by every build (`--no-postprocess` to skip).
Interpolation preserves duration and raises fps instead, and multipliers are integers —
generate at an fps that divides your target cleanly. Peak cost is system RAM, driven by
the upscaler's intermediate size (input x factor), not the target size; `postprocess.py`
picks x2 or x4 and refuses to submit above a 30GB estimate.

Input size, fps and frame count come from `lib/mp4_probe.py`, which reads the mp4 header
directly, so **finishing does not require ffmpeg on the host.** Only non-mp4 containers
(webm / mkv) and fragmented mp4 fall back to ffprobe; without that it names what is
missing rather than guessing — a silent default would mean submitting an estimate that
does not match the input.

## Tests

```bash
python3 -m unittest discover -s tests -t tests                          # on the host
docker compose run --rm client -m unittest discover -s tests -t tests   # in the container
```

**Both must pass.** `cw` runs on the host's Python while `docker compose run --rm client`
runs inside the container, so getting the repository-root resolution wrong (`/app` versus
the clone) breaks exactly one of them.

Select a subset with `-p` (tests `import _bootstrap` from outside `tests/`, so
`python3 tests/test_cli.py` does not work).

```bash
python3 -m unittest discover -s tests -t tests -p 'test_cli.py'        # cw dispatch
python3 -m unittest discover -s tests -t tests -p 'test_mp4_probe.py'  # mp4 header parsing
python3 -m unittest discover -s tests -t tests -k Endpoint             # by name
```

No GPU, no Colab runtime, no network — the suite covers the logic you can get wrong
without noticing until a runtime is already billing:

- canvas sizes and latent frame grids (the tables in this README are the fixtures)
- **workflow graph integrity** for every model: each `[node, index]` reference resolves,
  no node is unreachable from the save node, and the upscale stage refuses to collide
  with existing node IDs — ComfyUI would otherwise reject these with a 400 only after
  you have paid for a GPU
- endpoint/key resolution, failure diagnosis, and the retry policy (a POST is never
  retried, so a generation is never submitted twice)
- the key store: hashes are persisted, plaintext never is, and revocation holds
- **pre-flight checks before fetching weights**: not fetching a single file when the disk
  is short, not counting a shared encoder twice, and landing files in the folders ComfyUI
  actually looks at — finding out after 40GB means all of it was billed for nothing
- **killing and restarting a stalled download**, verified by genuinely hanging a child
  process: a Xet stall raises no exception, so a try/except test would not reproduce it
- **OAuth renewal**: it runs from an unattended loop, so it must never raise, and it must
  not conflate "needs reauth" with "could not tell"
- **`cw` dispatch**: what gets handed to what, that `cw run ... -- <work>` keeps its `--`,
  that operational commands keep `docker compose` out of the caller's sight, and that
  `cw models` still prints a table with no runtime
- **mp4 header parsing**: not mistaking an audio track for the video one, not losing
  frames when `stts` is split into runs, and not answering 0 frames for a fragmented
  mp4 — get this wrong and both the multiplier and the RAM estimate are wrong, which
  you only discover after submitting
- **finishing pre-conditions**: mp4 is read with no external command, and unreadable
  formats name what is needed instead of quietly substituting defaults

## Repository layout

```
pyproject.toml         package definition for cw / comfy-wrapper (no dependencies)
docker-compose.yml     client / colab / tunnel
docker/                images for each service
.claude/skills/        Claude Code skills (colab-comfy: run it / ltx-prompt: write for LTX)
src/
  cli/                 the cw dispatcher (installed as comfy_wrapper)
  server/              FastAPI + ComfyUI bridge; runs on the Colab runtime
  setup/               weight downloads
  scripts/             operational scripts, local and runtime side
  lib/                 local shared layer (endpoint, key, retry, pricing)
tests/                 unit tests (stdlib unittest, no GPU or network)
works/                 outputs, measurements, rescued artifacts (git-ignored)
.colab/                tokens, SSH key, key store, job ledger (git-ignored)
```

Notable modules, relative to `src/`:

| Path | Role |
|---|---|
| `cli/main.py` | The `cw` dispatcher: generation calls `scripts/` `main()` in-process, operations run the `*.sh` |
| `server/app.py` | FastAPI service: auth, job queue, ComfyUI bridge |
| `server/{h3,wan,ltx,ltx25,image,post}_workflows.py` | Workflow builders in ComfyUI API format |
| `server/video_common.py` | Shared size / duration maths and the output-stage upscale |
| `server/comfy.py` | ComfyUI client (submit, poll, collect) |
| `server/auth.py` | Key issue / store / verify (hashes only) |
| `setup/download_*.py` | Weight acquisition |
| `scripts/colab.sh` | Colab CLI wrapper via the resident container |
| `scripts/colab_run.sh` | Allocate → build → run → stop, unattended |
| `scripts/colab_watch.sh` | Watchdog: keep-alive, progress, auto-stop, artifact rescue |
| `scripts/generate_image.py` | Submit a still-image job and collect the png |
| `scripts/generate_video.py` | Submit a video job and collect the mp4 (with no arguments, an end-to-end check) |
| `scripts/postprocess.py` | Submit and collect finishing jobs (interpolation + upscale) |
| `scripts/measure_video.py` | Timing measurements (cold vs warm) |
| `lib/colab_link.py` | Endpoint / key resolution, retries, failure diagnosis |
| `lib/mp4_probe.py` | Size, fps and frame count from the mp4 header (for the finishing estimate; no ffmpeg needed) |
| `lib/video_sizes.py` | `480p` / `720p` / `1080p` to per-model canvas and output sizes |

## Configuration

There is nothing to configure for the standard path — the endpoint is picked
automatically depending on whether you are inside a container, and keys are read from
`.colab/colab-api-key`. The variables below exist for other setups.

| Variable | Default | Where |
|---|---|---|
| `COMFY_WRAPPER_HOME` | the source tree `cw` was installed from | `cw`: point it at a moved repository |
| `COLAB_ENDPOINT` | `http://tunnel:8000` in a container, `http://127.0.0.1:8000` on the host | Client and `cw`. Override when the server runs elsewhere |
| `COLAB_SESSION` | `comfy` | `tunnel` service: which session to forward |
| `COLAB_AUTH_LOOP_MINUTES` | `30` | `colab` service: OAuth refresh interval |
| `TZ` | `Asia/Tokyo` | `colab` service: watchdog log timestamps |
| `COMFY_URL` | `http://127.0.0.1:8188` | Server |
| `WRAPPER_KEYS_PATH` | runtime path | Server: key store location |
| `H3_*` / `LTX_*` / `LTX25_*` / `CW_*` | per-model | Server: weight filename overrides |

## Running outside Colab

`src/server/` has no Colab dependencies. On any host with ComfyUI and the weights in
place:

```bash
COMFY_URL=http://127.0.0.1:8188 WRAPPER_KEYS_PATH=/data/keys.json \
  python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Put a tunnel or reverse proxy in front of it as usual, and keep ComfyUI on localhost.

## Security

ComfyUI ships without authentication by design, and the ability to submit a workflow is
equivalent to arbitrary code execution — exposed instances have been targeted by
cryptomining botnet campaigns. This project therefore keeps ComfyUI bound to `127.0.0.1`
and exposes only the FastAPI service, behind an SSH tunnel and a bearer key. Do not
publish port 8188.

Access keys are generated locally and stored in `.colab/comfy-keys.json`; only SHA-256
hashes are sent to the runtime, so no plaintext key ever exists remotely.

## Disclaimer

This software is provided as is, without warranty of any kind. The author accepts no
liability whatsoever.

- **Billing is your responsibility.** This project uses paid Colab GPU runtimes. The
  watchdog (`colab_watch.sh`) stops a runtime on its time limit and on idle, but it
  **does not guarantee that the runtime stops.** Expired credentials, a dropped network,
  or a script that dies unexpectedly can all leave a runtime running, and the author is
  not liable for any charges that result. Confirm the runtime is really gone yourself
  with `cw sessions`, which asks the server.
- **Model and output usage is your responsibility.** Reviewing and complying with each
  model's license and terms, and handling whatever you generate, are up to you; the
  author is not liable for any issue arising from either.
- **These examples are not exhaustive.** The author is not liable for any damage arising
  from use of, or inability to use, this software — including but not limited to
  unintended charges, data loss, leaked credentials, violations of or changes to or
  outages of third-party services, and disputes with third parties. Use it at your own
  risk.

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 LOOPSKETCH.

This covers the code in this repository only. The workflow builders under `src/server/`
are derived from the official ComfyUI workflow templates (MIT, Copyright (c) 2023-present
Comfy Org) — see [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md). Model weights carry
their own terms:
MiniMax H3 Community License (with a separate application form for USA/EU/UK/Korea),
Apache-2.0 for Wan2.2, and the LTX-2 Community License Agreement for LTX-2.3 and LTX-2.5.
Review them before any commercial use.

**The LTX repositories on Hugging Face are gated.** Accept the license ("Agree and Access"
on the model page) with the account behind your HF token before downloading, or the
fetches return 403 — and because the build does not abort on a failed fetch, you end up
with a running session that has no weights. 2.3 and 2.5 are separate repositories and need
separate acceptance.

## Acknowledgements

- [MiniMaxAI/MiniMax-H3](https://huggingface.co/MiniMaxAI/MiniMax-H3) and
  [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) (quantized weights)
- [Wan-AI](https://huggingface.co/Wan-AI) /
  [Lightricks/LTX-2.3](https://huggingface.co/Lightricks/LTX-2.3) /
  [Lightricks/LTX-2.5](https://huggingface.co/Lightricks/LTX-2.5)
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) and
  [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF)
- [google-colab-cli](https://github.com/googlecolab/google-colab-cli)
