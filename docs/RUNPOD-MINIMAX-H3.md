# Runpod + MiniMax H3 — the reliable setup

This is the recommended path for Server AI Studio. It uses the public
[`huchukato/runpod-minimax-h3`](https://github.com/huchukato/runpod-minimax-h3)
worker and a Runpod network volume.

The important part is the order:

1. Create the network volume in the right data center.
2. Populate it with a **cheap CPU pod**.
3. Remove the CPU pod.
4. Create the **96 GB Blackwell GPU** endpoint with the populated volume.

Downloading ~100 GB of models at GPU prices is the mistake this guide avoids.

> Prices and stock below were checked on **2026-11-07**. Runpod changes both.
> Always confirm in the console before creating anything.

---

## What you need

- A Runpod account with credit.
- A Runpod API key: **console → Settings → API Keys → Create**.
  It starts with `rpa_`.
- About 30–60 minutes for the first setup. Most of that is downloading models.
- This app running locally: `python3 app.py`.

---

## Cost snapshot

| item | price on 2026-11-07 |
|---|---|
| RTX PRO 6000 Blackwell, 96 GB — Serverless | **$3.49/hour** while a worker is running |
| RTX PRO 6000 Blackwell, 96 GB — secure Pod | **$2.49/hour** |
| RTX PRO 6000 Blackwell, 96 GB — community Pod | **$1.69/hour** |
| CPU3 compute-optimized Pod, 8 vCPU / 16 GB | **~$0.24/hour** (8 × $0.03) |
| Network volume | billed separately by Runpod; check the console |

A 10-second clip usually takes about 5–6 minutes on the 96 GB card, so roughly
**$0.30–$0.35** of GPU time. A 5-second smoke test is roughly half that.

The CPU prefill usually costs **$0.10–$0.30** instead of paying GPU rates for the
same download.

---

## 1. Pick a data center and create the volume

A Runpod network volume:

- lives in **one** data center,
- can be mounted by pods and Serverless endpoints **in that same data center**,
- cannot be moved to another data center later.

So choose the data center first.

1. Open **Runpod → Storage → Network volumes → New network volume**.
2. Pick a data center that has **RTX PRO 6000 Blackwell 96 GB** (`BLACKWELL_96`)
   capacity. `US-WA-1` had stock when this was written, but check the GPU list
   in the console; do not assume.
3. Size it **200 GB**. The worker requires at least **160 GB**; 200 GB leaves
   room for TensorRT engines and future model files.
4. Name it something obvious, for example `minimax-h3-models`.
5. Create it.

> If you pick the wrong data center, delete the empty volume and create it again.
> Once models are on it, moving means downloading everything again.

---

## 2. Populate the volume with a CPU pod

The worker's entrypoint downloads `models-manifest.txt` to `/runpod-volume`
**before** it starts the GPU handler. That means a CPU pod can do the expensive
download, and the GPU only ever sees a full volume.

### Create the CPU pod

1. Open **Runpod → Pods → Deploy**.
2. Switch the compute type to **CPU**.
3. Choose **CPU3 — Compute Optimized**, **8 vCPU** (16 GB RAM).
   - 8 × $0.03 = **~$0.24/hour**.
4. Container image: use the current Hub release image. On the
   [Hub page](https://console.runpod.io/hub/huchukato/runpod-minimax-h3), the
   template shows the image. For release **v1.1.3** it was:

   ```
   registry.runpod.net/huchukato-runpod-minimax-h3-main-dockerfile:5af165a6f
   ```

   Prefer the tag shown by the current release; tags change when the worker
   updates. If the console refuses a GPU template on a CPU pod, choose
   **Custom deployment** and paste the image directly.
5. Container disk: **30 GB**.
6. Attach the network volume you created and mount it at `/runpod-volume`.
7. Add the environment variable:

   ```
   VOLUME_AUTOPOPULATE=true
   ```

   `HF_TOKEN` is not needed: every bundled model is public.
8. Deploy. No ports need to be exposed.

### Wait for the download

Open the pod logs. You will see the image pull, then lines like:

```
populate: free space on /runpod-volume: ...
populate: [1/16] downloading ...
...
populate: done — /runpod-volume/models
```

Wait for **`populate: done — /runpod-volume/models`**. That is the signal that
the volume is ready.

After that the container may try to start the serverless handler and fail
because there is no GPU. That is expected on a CPU pod. The volume is already
populated; the handler failing afterwards does not delete anything.

### Remove the CPU pod

Once `populate: done` is in the logs:

1. **Terminate** the CPU pod. Do not leave it running.
2. Confirm the network volume still exists and shows roughly **100 GB used**.

Do not create the GPU endpoint before this step.

> If the pod restarts during the download, it is safe: the worker skips files
> that already exist and resumes the rest. The volume has a `flock` so two
> workers cannot corrupt each other.

---

## 3. Create the Serverless endpoint

1. Open the Hub page:
   <https://console.runpod.io/hub/huchukato/runpod-minimax-h3>
2. Choose **Deploy** → **Serverless**.
3. Use these settings:

   | setting | value |
   |---|---|
   | GPU | **RTX PRO 6000 Blackwell, 96 GB** (`BLACKWELL_96`) |
   | GPU count | **1** |
   | Network volume | the volume from step 1 (same data center) |
   | Container disk | **30 GB** |
   | CUDA | any of **13.0, 13.1, 13.2** |
   | Minimum workers | **0** |
   | Maximum workers | **1** |
   | Idle timeout | **5 seconds** |
   | FlashBoot | on, if offered |
   | `VOLUME_AUTOPOPULATE` | **true** on the first deploy |
   | `JOB_TIMEOUT_S` | **1800** |

4. Deploy the endpoint.
5. Copy the **endpoint ID** (the short code in the endpoint page URL) and the
   API key you created earlier.

Once the volume is populated, you can set `VOLUME_AUTOPOPULATE=false` on the
endpoint to skip the file-check pass at every cold start. Leave it `true` if you
plan to add models later.

### Why `max workers = 1`

One worker is enough for interactive work, keeps costs predictable, and avoids
two cold workers racing to load the same models. Raise it only when you
deliberately want parallel renders.

---

## 4. Connect Server AI Studio

1. Start the app:

   ```bash
   python3 app.py
   ```

2. Open <http://127.0.0.1:8780>.
3. The first-run wizard appears. Choose **Runpod**.
4. Paste the **endpoint ID** and **API key**.
5. Save.

If you skipped the wizard earlier: **Setup** in the header, or
**Settings → Open setup guide**.

The app keeps the key in `~/.server-ai-studio/config.json` (mode 600). It is
never written into the project folder and never sent back to the browser.

---

## 5. Smoke test

Before rendering a long clip:

1. Add **one or two reference images** (a face, an outfit, or a scene style).
2. Write a short prompt, or load a saved prompt.
3. Set:
   - **Length:** 5 seconds
   - **Shape:** Wide 16:9 or Portrait 9:16
   - **How many takes:** 1
   - **Quality:** Fast draft
4. Click **Render on Runpod**.

The first job is slow: the worker pulls the image, loads ~100 GB of models, and
compiles TensorRT engines. Expect **5–15 minutes** of cold start. Later jobs on
a warm worker are much faster; a 5-second clip is commonly around 2–3 minutes.

If the clip appears and plays, the setup is done.

---

## 6. Keep the bill under control

- **Minimum workers = 0.** You do not pay when nothing is running.
- **Maximum workers = 1** unless you need parallel jobs.
- **Idle timeout = 5 seconds.** The worker scales to zero quickly.
- The network volume is billed even when no worker is running. It is the price
  of not re-downloading 100 GB every time.
- Delete the endpoint and the volume when you are finished with the project.
- The app's **GPU stock** button shows current Runpod availability for the
  endpoint's GPU pools.

---

## Troubleshooting

| symptom | likely cause | fix |
|---|---|---|
| `409 ENDPOINT_PAUSED` | endpoint workers are set to 0 | In the app, Settings → worker control → set to 1 and Apply. Or raise max workers in Runpod. |
| Worker starts, then errors on missing models | volume empty, wrong volume, or wrong data center | Check that the endpoint is in the same data center as the volume. Re-run the CPU prefill and confirm `populate: done`. |
| Endpoint cannot see the volume | volume and endpoint are in different data centers | Network volumes cannot cross data centers. Recreate the volume in the endpoint's data center. |
| `BLACKWELL_96` has no capacity | stock ran out in that data center | Check GPU stock in the console. A different data center needs a new volume. |
| CUDA / driver error at startup | host CUDA is outside 13.0–13.2 | Pick a host with CUDA 13.0–13.2, or redeploy. |
| First job takes 15+ minutes | cold start: image pull + model load + TensorRT compile | Expected. Warm jobs are much faster. |
| Every job is slow | worker is not staying warm, or `min` is 0 and jobs are spaced out | Use a batch, or accept the cold start. Do not raise `min` unless you accept idle billing. |
| Results over ~20 MB fail | Runpod drops very large inline results | Use shorter clips, or configure S3 output on the worker. |
| App says Runpod is not connected | key/endpoint missing after a skip | Open Setup and add them. |

### Do not

- Do not create the GPU endpoint first and let it download the models.
- Do not change the GPU pool after TensorRT engines have been compiled on the
  volume; the engines are architecture-specific.
- Do not add `--fast`, `--use-sage-attention`, or `--async-offload` to
  `COMFYUI_EXTRA_ARGS`; they corrupt the int8 stacks.
- Do not commit `config.json`, API keys, or the volume contents to GitHub.

---

## Quick checklist

- [ ] Network volume created in a `BLACKWELL_96` data center, 200 GB
- [ ] CPU pod created with the worker image, volume mounted at `/runpod-volume`
- [ ] `VOLUME_AUTOPOPULATE=true`
- [ ] Logs show `populate: done — /runpod-volume/models`
- [ ] CPU pod terminated
- [ ] Serverless endpoint created with the same volume and `BLACKWELL_96`
- [ ] min workers 0, max workers 1, idle timeout 5 s
- [ ] Endpoint ID + API key saved in Server AI Studio
- [ ] One 5-second smoke clip completed
