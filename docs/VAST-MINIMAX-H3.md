# Vast.ai + ComfyUI — the reliable setup

Use this path if you want a full ComfyUI machine you rent by the hour. It is more
control and more setup than Runpod Serverless. Server AI Studio talks to ComfyUI
over HTTP and reads the machine's live port from the Vast.ai API, so restarts do
not break the connection.

> The prices and timings below are examples, not quotes. Vast.ai prices change
> per host, and bandwidth is billed separately. Confirm the offer in the console
> before renting.

---

## What you need

- A Vast.ai account with credit.
- A Vast.ai API key: **console.vast.ai → Account → API Keys**.
- A 96 GB GPU offer. The tested class is **RTX PRO 6000 S / WS / Max-Q**.
- This app running locally.

---

## What you get

A rented 96 GB machine running stock ComfyUI with MiniMax H3 (Singularity v2),
reachable on a mapped HTTP port, driven by Server AI Studio. The bootstrap script
installs ComfyUI and downloads the models once. If you keep the machine stopped
instead of destroying it, the disk keeps the models.

---

## Option A — rent with the included script (easiest)

The repo includes `tools/vast_rent.py`, which searches for a 96 GB offer,
creates the instance with the bootstrap script, waits for the mapped ComfyUI
port, and writes the machine ID and rate into your app config.

```bash
export VAST_API_KEY=...          # or save the key in the app first
python3 tools/vast_rent.py
```

It prints the offer price before creating anything, then:

```
CREATED 12345678  $1.07/hr  ...
Saved machine ID and rate to ~/.server-ai-studio/config.json
  running {'8188': 40003, '8189': 40159}
COMFY http://203.0.113.10:40159
```

Then start the app and choose **Vast.ai / ComfyUI** as the provider. The app
already has the machine ID from the config file.

> The script rents a machine and starts billing. Only run it when you intend to
> use it, and stop the machine when you are done.

---

## Option B — create it in the Vast.ai console

1. Open **Vast.ai → Search**.
2. Filter:
   - GPU: **RTX PRO 6000 S**, **RTX PRO 6000 WS**, or **RTX PRO 6000 Max-Q**
   - GPU count: **1**
   - Rentable: **yes**
   - On-demand
   - Reliability: **≥ 0.94**
   - Disk: **≥ 220 GB**
   - Download speed: **≥ 1000 Mbps**
3. Prefer offers with **disk bandwidth ≥ 2000 MB/s**. Model loading is
   disk-bound; the cheapest slow disk is a false economy.
4. Use the image:

   ```
   pytorch/pytorch:2.7.1-cuda12.8-cudnn9-devel
   ```

   The `-devel` image is required because ComfyUI custom nodes compile CUDA
   helpers at import time. A `-runtime` image fails with
   `Failed to find C compiler`.
5. Set the launch mode / run type so you can supply your own command, and expose
   **two ports**:
   - `8188` — status and setup logs
   - `8189` — ComfyUI
6. Use the bootstrap script from `tools/vast_bootstrap.sh`. The script is
   self-contained; the easiest way is to let `tools/vast_rent.py` pass it.
   If you paste it manually, base64-encode it first so quotes and `$` survive.

The rest of this guide assumes the bootstrap script is running.

---

## What the bootstrap does

`tools/vast_bootstrap.sh`:

1. Starts a tiny status server on **8188** so you can always read progress.
2. Installs `git`, `ffmpeg`, OpenCV libraries, `curl`, and `build-essential`.
3. Clones stock ComfyUI and installs its requirements.
4. Downloads the H3 video/audio VAEs, turbo and style LoRAs, the Qwen3-VL text
   encoder, and the Singularity v2 pruned int8 checkpoint.
5. Prints `DOWNLOADS_DONE`.
6. Runs ComfyUI on **8189** in a restart loop, because the script is PID 1 and
   must never exit.

Downloads are resumable (`curl -C -`) and retried. If a download drops, run it
again; it continues instead of starting from zero.

### Verify it is ready

Read the instance detail from the Vast.ai API:

```text
GET https://console.vast.ai/api/v0/instances/{id}/
```

Note the `v0`; the `v1` detail endpoint returns 404. Wait for
`actual_status == "running"`, then read `ports`.

- `8188/tcp → hostPort` serves `/setup.log`. Look for `DOWNLOADS_DONE`.
- `8189/tcp → hostPort` is ComfyUI. Check
  `http://<host>:<comfy-port>/system_stats`.

Expect roughly 8–15 minutes for the downloads on a good host, depending on
bandwidth, then about 30 seconds for ComfyUI to come up. The first model load
after that is slower.

---

## Connect the app

Start the app:

```bash
python3 app.py
```

Open <http://127.0.0.1:8780>.

### First run

1. The setup wizard appears.
2. Choose **Vast.ai**.
3. Paste the **Vast.ai API key**.
4. Paste the **machine ID** (the number shown in the Vast.ai console).
5. Optionally add a **ComfyUI address** if ComfyUI runs somewhere else.
6. Save.

### Later

**Setup** in the header, or **Settings**:

- Provider: **Vast.ai / ComfyUI — rent by the hour**
- Vast.ai API key: leave blank to keep the saved key
- Vast.ai machine ID
- Machine price: used only for the cost estimate
- Stop the Vast machine after N idle minutes: recommended **10–15**

The app reads the current mapped `8189` port from Vast every time, so the
Settings URL is only an override for a ComfyUI that is not on the Vast machine.

> Keys are stored in `~/.server-ai-studio/config.json` (mode 600). They are
> never written into the project folder and never returned to the browser.

---

## Smoke test

1. Add one or two reference images.
2. Write a short prompt.
3. Use:
   - **Length:** 5 seconds
   - **Shape:** Wide 16:9
   - **Takes:** 1
   - **Quality:** Fast draft
4. Click **Render on Vast.ai**.

A healthy 5-second clip should come back at 1344×768, 24 fps, H.264 + AAC.
If it does, the machine and the app are connected correctly.

---

## Costs

Vast.ai bills three things:

| item | how it works |
|---|---|
| GPU | the hourly rate shown on the offer, billed while the machine runs |
| Disk | a daily/monthly charge for the allocated disk |
| Bandwidth | billed per GB transferred; a full model pull is tens of GB |

**Stop the machine instead of destroying it** once the models are on disk.
Stopping keeps the disk and avoids paying the download again. Destroy only when
you are done with the build.

The app's **auto-stop** setting stops the machine after N idle minutes. Use it.
A forgotten running machine is the most common way to waste credit.

---

## Troubleshooting

| symptom | cause | fix |
|---|---|---|
| Instance is `offline` | the host went down, not your instance | Wait for the host, or rent another machine. A volume or a kept disk avoids re-downloading. |
| Instance is `exited` | you stopped it, or it crashed | Start it. It resumes only if that host's GPU is free; otherwise rent another. |
| Port 8188/8189 unreachable while `running` | the bootstrap exited and PID 1 died | Check `/workspace/setup.log` through the status port. The script must end in a restart loop. |
| `DOWNLOADS_DONE` never appears | a download is stuck or the disk is full | Read `setup.log`; check disk space; rerun the bootstrap (it resumes). |
| `Failed to find C compiler` | `-runtime` image | Use the `-devel` image and install `build-essential`. |
| `value_not_in_list` when queueing | the model/LoRA name does not exist on that machine | The app reads the live lists from ComfyUI and self-heals after the machine is up. Re-select the model. |
| ComfyUI works, app cannot connect | wrong machine ID, or the port changed | The app reads the live port when a machine ID is set. Check the ID. |
| Every take is identical | old build without seed patching | Update the app; the current version writes the seed per job. |
| Machine stops by itself | auto-stop reached its idle limit | Raise the limit or set it to 0 in Settings. |

### Ports change on every restart

Vast reassigns the host port each time the machine starts. Never hardcode it:

- the app derives `host:ports["8189"]` from the Vast API on every request;
- the Settings URL is an override and is ignored while a machine ID is set;
- for manual checks, read `ports` from `GET /api/v0/instances/{id}/`.

### Composition lock

`graph-h3-stock-5ref-guide` anchors frame 0 to the **last reference** through
`MiniMaxH3AddGuide`. Select references in order and put the scene still last.
Keep the clip 16:9 for the closest match.

---

## Checklist before a real run

- [ ] `/system_stats` returns 200 on the mapped ComfyUI port
- [ ] `sing_v2_pruned_int8.safetensors` is in `diffusion_models`
- [ ] `qwen3vl_32b_nvfp4_awq.safetensors` is in `text_encoders`
- [ ] both VAEs and the LoRAs are present
- [ ] one 5-second smoke clip returns 1344×768 / 24 fps
- [ ] app provider is **Vast.ai / ComfyUI**
- [ ] machine ID and rate are saved
- [ ] auto-stop is configured
- [ ] credit covers the planned run with some margin
