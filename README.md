# Server AI Studio

A small, local web app for making **MiniMax H3** reference-to-video clips on a
rented GPU. It drives either **Runpod Serverless** or a **ComfyUI machine on
Vast.ai** from one plain-language interface.

No cloud account is needed to open the app. No dependencies either: it is
stdlib Python 3 and a single-page UI, with no build step.

```bash
python3 app.py            # http://127.0.0.1:8780
PORT=9000 python3 app.py
```

On the first start the app asks you to connect a provider. You can press
**Skip for now** and add keys later with the **Setup** button in the header.

---

## Start here if you are an AI agent

Do not guess at Runpod or Vast.ai steps. Read the matching guide first.

| The user wants to… | Follow this guide |
|---|---|
| Use **Runpod Serverless** (recommended) | [`docs/RUNPOD-MINIMAX-H3.md`](docs/RUNPOD-MINIMAX-H3.md) |
| Use **Vast.ai with ComfyUI** | [`docs/VAST-MINIMAX-H3.md`](docs/VAST-MINIMAX-H3.md) |
| Run the app on an **Android phone** with **Termux + OpenCode** | [`docs/TERMUX-OPENCODE.md`](docs/TERMUX-OPENCODE.md) |
| Understand the **public worker image**, models, or handler contract | [`docs/WORKER-REFERENCE.md`](docs/WORKER-REFERENCE.md) |

Rules for agents doing setup:

1. **Read the routed guide before creating anything.** Runpod and Vast.ai both
   bill from the moment a resource exists.
2. **Ask before creating a billable resource.** State the hourly price from the
   guide or the live console, and wait for the user's go-ahead.
3. **Runpod order matters:** network volume → cheap CPU pod populates the
   volume → terminate the CPU pod → 96 GB Blackwell endpoint. Do not let the
   GPU endpoint download the models.
4. **Never commit keys, endpoint IDs, volume IDs, references, prompts, or
   rendered clips.** `.gitignore` excludes them, but check before pushing.
5. **Do not create or delete resources the user did not ask for.** Diagnose
   first; change only what was authorized.

The app stores keys in `~/.server-ai-studio/config.json` (mode `600`), outside
this repository. Never print that file or send its contents anywhere.

---

## What it does

- **Two providers, one UI.** Runpod Serverless for pay-per-render, or a Vast.ai
  ComfyUI machine for hourly rental.
- **Reference images in order.** Click faces, outfits, or scene stills in the
  order your prompt calls them “Picture 1”, “Picture 2”, and so on. Uploads stay
  in the local library.
- **Prompt library.** Write a prompt in plain English, save it, reload it later.
- **Takes.** Queue several versions in one click. Seeds advance per take so the
  versions actually differ.
- **Queue and compare.** Watch status, render time, and output size. Play,
  download, cancel, or compare finished takes side by side.
- **Worker control.** See whether the Runpod endpoint is paused and set how many
  workers may run. `0` pauses and costs nothing; `1`+ accepts work.
- **Cost visibility.** The queue shows GPU time and an estimated cost using the
  rate you saved.
- **First-run setup that can be skipped.** Non-technical users can connect
  Runpod or Vast.ai, or skip and come back later.

The UI deliberately avoids provider jargon where it can. “Where to render”,
“What should happen in the clip?”, “Look & speed”, and “Delivery” are the main
sections; the technical names live under **Advanced settings**.

---

## Requirements

- Python **3.10 or newer**
- A modern browser
- A Runpod or Vast.ai account **only when you are ready to render**

There is nothing to `pip install`.

---

## Quick start

```bash
git clone https://github.com/YOUR-USER/server-ai-studio.git
cd server-ai-studio
python3 app.py
```

Open <http://127.0.0.1:8780>.

1. Choose **Runpod** or **Vast.ai** in the setup wizard.
2. Paste the keys and IDs the wizard asks for. Each field explains where to
   find it.
3. Save, or choose **Skip for now**.
4. Add a reference image, write a prompt, and render a short test clip.

For the full provider setup, use the guide in the table above.

---

## How the pieces fit

```
app.py                    local web server, Runpod/ComfyUI client, job poller
static/                   single-page UI (HTML, CSS, JS; no build step)
assets/graphs/            stock MiniMax H3 workflow JSON files
assets/refs/              your reference images        (gitignored)
assets/prompts/           your saved prompts           (gitignored)
jobs/                     rendered clips and job index  (gitignored)
tools/vast_rent.py        optional Vast.ai rental helper
tools/vast_bootstrap.sh   optional ComfyUI + H3 bootstrap for Vast.ai
docs/                     provider and phone guides
test_smoke.py             offline end-to-end test suite
```

### Stock workflows

Only the generic `graph-h3-stock-*` workflows ship in the repository:

| graph | references | use |
|---|---|---|
| `graph-h3-stock-2ref` | 2 | fast two-reference shots |
| `graph-h3-stock-5ref` | 5 | more references, no composition anchor |
| `graph-h3-stock-5ref-guide` | 5 + guide | anchors the opening frame to the last reference |
| `graph-h3-stock-5ref-guide3` | 5 + guide | variant of the anchored graph |
| `graph-h3-stock-5ref-sparse` | 5 | lets the model choose the camera more freely |

The app picks a suitable graph from the number of references and the
**Camera framing** choice. You can override it under **Advanced settings**.

The stock graphs use the Vast.ai model filenames. When the Runpod backend is
active, the server translates them to the public worker's manifest names
automatically, so the same graphs work on both providers.

Personal `graph-singularity-*` workflows, reference images, prompts, and
rendered clips are **not** part of the public release and are gitignored.

---

## Configuration

Runtime settings and keys live in:

```
~/.server-ai-studio/config.json
```

The folder is created with mode `700` and the file with mode `600`. Override the
location with:

```bash
SERVER_AI_STUDIO_CONFIG=/path/to/config.json python3 app.py
```

Other environment variables:

| variable | default | meaning |
|---|---|---|
| `PORT` | `8780` | HTTP port |
| `HOST` | `127.0.0.1` | bind address; keep it local unless you trust the network |
| `SERVER_AI_STUDIO_CONFIG` | `~/.server-ai-studio/config.json` | config file path |

There is **no login screen**. The app is designed for `127.0.0.1`. Do not expose
it to the public internet. If you bind it to `0.0.0.0`, anyone on that network
can use your saved provider keys.

If an old `config.json` is found next to `app.py`, the app moves it to the new
location on startup and removes the in-repo copy.

---

## Costs, plainly

| provider | what you pay |
|---|---|
| **Runpod Serverless** | GPU time only while a worker runs; scale-to-zero; volume storage separately |
| **Vast.ai** | hourly GPU rate + disk + bandwidth; stop the machine when idle |

At the time of writing, a 96 GB RTX PRO 6000 Blackwell on Runpod Serverless was
**$3.49/hour**, and a 10-second clip usually took about 5–6 minutes. That is
roughly **$0.30–$0.35** of GPU time per clip. Verify current prices in the
console before creating anything.

The Runpod guide uses a cheap CPU pod to populate the network volume first. That
turns a ~100 GB download from GPU-priced hours into a few cents of CPU time.

---

## Tests

The suite mocks Runpod, uses temporary folders, and never reads your real
config or personal media:

```bash
python3 test_smoke.py
```

It covers first-run setup and skip, secret redaction, submission, seeds,
resolution changes, reference upload/delete, prompts, worker pause, batches,
LoRA stacking, and the ComfyUI path.

---

## Troubleshooting

| symptom | fix |
|---|---|
| The app opens but says “not connected” | Open **Setup** and add a provider; **Skip for now** does not connect one. |
| `409 ENDPOINT_PAUSED` | Runpod workers are at `0`. Settings → worker control → set to `1` and Apply. |
| Runpod job fails on a missing model | The endpoint is using the wrong volume, the volume is empty, or it is in another data center. See the Runpod guide's troubleshooting table. |
| Every clip looks identical | Change the seed or increase takes; each take should advance the seed. Update to the current version if seeds are not written per job. |
| Vast port does not answer | The machine may still be downloading, or the bootstrap exited. Read `/setup.log` through port `8188`; see the Vast guide. |
| Vast machine bills while idle | Set **Stop the Vast machine after N idle minutes**, or stop it manually in the console. |
| A key was committed by mistake | Remove it from git history, rotate the key in Runpod/Vast.ai, and check `git status` before pushing. |

---

## Security notes

- Keys are stored outside the repository and are never returned to the browser.
  The UI only reports whether a key exists.
- `.gitignore` excludes the config, personal references and prompts, generated
  jobs, old personal graphs, caches, and editor files.
- The app proxies provider APIs from the local Python process; the browser never
  receives the Runpod or Vast.ai key.
- Reference images and rendered clips are sent only to the provider you chose.

---

## Documentation

- [Runpod + MiniMax H3](docs/RUNPOD-MINIMAX-H3.md) — CPU prefill, then a 96 GB
  Blackwell Serverless endpoint.
- [Vast.ai + ComfyUI](docs/VAST-MINIMAX-H3.md) — rent a 96 GB machine, bootstrap
  ComfyUI, connect it to the app.
- [Termux + OpenCode](docs/TERMUX-OPENCODE.md) — run the app on an Android phone,
  optionally with an AI coding agent in the terminal.
- [Worker reference](docs/WORKER-REFERENCE.md) — image layers, model inventory,
  handler input, graph internals, and the ComfyUI equivalent.
