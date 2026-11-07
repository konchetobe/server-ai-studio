#!/usr/bin/env python3
"""
Server AI Studio - a small local front-end for MiniMax H3 video generation.

Works with either Runpod Serverless or a ComfyUI machine (for example Vast.ai).
Stdlib only: it serves a single-page UI and keeps your API keys in this process.

    python3 app.py            # http://127.0.0.1:8780

Keys are stored outside the project folder in ~/.server-ai-studio/config.json
(mode 600). Set SERVER_AI_STUDIO_CONFIG to use a different path.
"""

import base64
import json
import os
import re
import shutil
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import error as urlerror
from urllib import request as urlreq
from urllib.parse import unquote, urlencode, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
ASSETS = os.path.join(ROOT, "assets")
JOBS = os.path.join(ROOT, "jobs")

DEFAULT_CONFIG = os.path.join(os.path.expanduser("~"), ".server-ai-studio", "config.json")
CONFIG = os.environ.get("SERVER_AI_STUDIO_CONFIG") or DEFAULT_CONFIG
LEGACY_CONFIG = os.path.join(ROOT, "config.json")
INDEX = os.path.join(JOBS, "index.json")

API_BASE = "https://api.runpod.ai/v2"      # queue API: /run, /status, /cancel
MGMT_BASE = "https://api.runpod.io/v2"     # management API: /serverless/{id}

LOCK = threading.RLock()
JOBS_DB = []
CONFIG_DB = {
    "endpointId": "",
    "apiKey": "",
    "defaultVideoSize": "1344x768",
    "defaultSeconds": 10,
    "pollSeconds": 4,
    "autoPark": False,
    "backend": "runpod",     # runpod | comfyui
    "comfyUrl": "",          # e.g. http://1.2.3.4:8188
    "vastApiKey": "",
    "vastInstanceId": "",
    "vastRate": 1.2,          # $/hr, for the cost meter
    "autoStopMin": 10,        # stop the Vast instance after N idle minutes (0 = off)
    "setupDone": False,       # the first-run wizard has been completed or skipped
    "setupSkipped": False,    # the user chose "skip for now"
}

TERMINAL = ("COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT", "ERROR")
PARKED_BATCHES = set()

# LoRAs that ship on the model volume; "none" drops the LoRA node entirely.
# These are the filenames the Vast.ai bootstrap writes. The public Runpod
# worker uses different turbo-LoRA filenames; RUNPOD_NAME_MAP below translates
# them when the Runpod backend is active.
LORAS = [
    {"id": "", "name": "none — base checkpoint"},
    {"id": "turbo_ref2v_4step.safetensors", "name": "turbo 4-step (fast)"},
    {"id": "h3_character_swap_pro4500_1000.safetensors", "name": "character swap"},
    {"id": "h3-realism-people-t2v-i2v-r2v.safetensors", "name": "realism people"},
]
MODELS = [
    {"id": "", "name": "graph default (v1.3 pruned int8)"},
    {"id": "Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors",
     "name": "v1.3 pruned int8 — 21 GB"},
]
# The public worker's network volume is populated from models-manifest.txt.
# The bundled stock graphs use the shorter Vast.ai filenames, so translate
# them on the Runpod path. Keep this list aligned with the manifest.
RUNPOD_NAME_MAP = {
    "sing_v2_pruned_int8.safetensors":
        "Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors",
    "Minimax-h3_Singularity_ref2va_v2_pruned_int8.safetensors":
        "Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors",
    "Minimax-h3_Singularity_ref2va_v2_int8.safetensors":
        "Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors",
    "Minimax-h3_Singularity_ref2va_v1.3_Pruned_w4a8.safetensors":
        "Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors",
    "qwen3vl_32b_nvfp4_awq.safetensors":
        "qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors",
    "turbo_ref2v_4step.safetensors":
        "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
}
DEFAULT_OPTS = {
    "model": "",
    "lora": LORAS[1]["id"],
    "loraStrength": 1.0,
    "lora2": "",
    "lora2Strength": 1.0,
    "steps": 6,
    "enhance": False,
    "upscale": False,
    "rife": False,
    "refImageSize": "max",
}

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".mp4": "video/mp4",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


# ---------------------------------------------------------------- persistence
def _configured(cfg):
    """True when at least one backend has enough information to run a job."""
    runpod = bool(cfg.get("endpointId") and cfg.get("apiKey"))
    vast = bool(cfg.get("vastApiKey") and cfg.get("vastInstanceId")) or bool(cfg.get("comfyUrl"))
    return runpod or vast


def _migrate_legacy_config():
    """Move a config.json left beside app.py out of the project folder."""
    if CONFIG != DEFAULT_CONFIG or os.path.exists(CONFIG):
        return
    if not os.path.exists(LEGACY_CONFIG):
        return
    try:
        os.makedirs(os.path.dirname(CONFIG), mode=0o700, exist_ok=True)
        shutil.copy2(LEGACY_CONFIG, CONFIG)
        os.chmod(CONFIG, 0o600)
        os.remove(LEGACY_CONFIG)
    except OSError:
        pass


def load_state():
    global JOBS_DB, CONFIG_DB, BATCHES
    _migrate_legacy_config()
    loaded = {}
    if os.path.exists(CONFIG):
        try:
            with open(CONFIG) as f:
                loaded = json.load(f)
            CONFIG_DB.update(loaded)
        except Exception:
            loaded = {}
    # Legacy configs predate the setup flag. If they already have a working
    # provider, don't surprise the user with a first-run wizard after updating.
    if "setupDone" not in loaded:
        CONFIG_DB["setupDone"] = _configured(CONFIG_DB)
    if os.path.exists(INDEX):
        try:
            with open(INDEX) as f:
                JOBS_DB = json.load(f)
        except Exception:
            JOBS_DB = []
    if os.path.exists(BATCH_FILE):
        try:
            with open(BATCH_FILE) as f:
                BATCHES = json.load(f)
        except Exception:
            BATCHES = []


def save_config():
    folder = os.path.dirname(CONFIG)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    tmp = CONFIG + ".tmp"
    with open(tmp, "w") as f:
        json.dump(CONFIG_DB, f, indent=1)
    os.replace(tmp, CONFIG)
    try:
        os.chmod(CONFIG, 0o600)
    except OSError:
        pass


def setup_state():
    cfg = CONFIG_DB
    runpod_ready = bool(cfg.get("endpointId") and cfg.get("apiKey"))
    vast_ready = bool(cfg.get("vastApiKey") and cfg.get("vastInstanceId")) or bool(cfg.get("comfyUrl"))
    done = bool(cfg.get("setupDone"))
    backend = cfg.get("backend", "runpod")
    return {
        "needed": not done,
        "done": done,
        "skipped": bool(cfg.get("setupSkipped")),
        "runpodReady": runpod_ready,
        "vastReady": vast_ready,
        "ready": runpod_ready if backend == "runpod" else vast_ready,
        "backend": backend,
    }


def save_jobs():
    tmp = INDEX + ".tmp"
    with open(tmp, "w") as f:
        json.dump(JOBS_DB, f, indent=1)
    os.replace(tmp, INDEX)


# ---------------------------------------------------------------------- runpod
def _http(method, url, body=None, timeout=180):
    data = None
    headers = {"Authorization": f"Bearer {CONFIG_DB['apiKey']}"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urlreq.Request(url, data=data, headers=headers, method=method)
    try:
        with urlreq.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urlerror.HTTPError as e:
        detail = e.read().decode(errors="replace")[:400]
        if e.code == 409 and "ENDPOINT_PAUSED" in detail:
            raise RuntimeError("endpoint is paused (workers.max = 0) — set workers to 1 or more in Settings")
        raise RuntimeError(f"HTTP {e.code}: {detail}")
    except urlerror.URLError as e:
        raise RuntimeError(f"network: {e.reason}")


def rp(method, path, body=None, timeout=180):
    cfg = CONFIG_DB
    if not cfg.get("endpointId") or not cfg.get("apiKey"):
        raise RuntimeError("Runpod is not connected yet — open Setup and add your endpoint ID and API key")
    return _http(method, f"{API_BASE}/{cfg['endpointId']}{path}", body, timeout)


def rp_mgmt(method, path="", body=None, timeout=60):
    cfg = CONFIG_DB
    if not cfg.get("endpointId") or not cfg.get("apiKey"):
        raise RuntimeError("Runpod is not connected yet — open Setup and add your endpoint ID and API key")
    return _http(method, f"{MGMT_BASE}/serverless/{cfg['endpointId']}{path}", body, timeout)


def health():
    try:
        return rp("GET", "/health", timeout=20)
    except Exception as e:
        return {"error": str(e)}


# ------------------------------------------------------------------- job logic
def find(job_id):
    for j in JOBS_DB:
        if j["id"] == job_id:
            return j
    return None


def take_suffix(i):
    return chr(ord("a") + i) if i < 26 else str(i + 1)


def build_input(job, graph, prompt, images):
    inp = {
        "prompt_graph": graph,
        "prompt": prompt,
        "images": images,
        "seconds": job["seconds"],
        "video_size": job["videoSize"],
        "enhance": bool(job.get("enhance", False)),
        "upscale": bool(job.get("upscale", False)),
        "rife": bool(job.get("rife", False)),
        "seed": job["seed"],
        "job_name": job["name"],
    }
    if job.get("refImageSize"):
        inp["ref_image_size"] = job["refImageSize"]
    return inp


def apply_options(graph, job):
    """Bake the per-job knobs into a copy of the workflow."""
    lora = job.get("lora", DEFAULT_OPTS["lora"])
    strength = float(job.get("loraStrength") or 1.0)
    lora2 = job.get("lora2") or ""
    strength2 = float(job.get("lora2Strength") or 1.0)
    steps = int(job.get("steps") or 4)
    ref_size = job.get("refImageSize") or "match"

    lora_node_id = next((k for k, v in graph.items()
                         if v.get("class_type") == "LoraLoaderModelOnly"), None)

    if lora_node_id:
        node = graph[lora_node_id]
        if not lora:
            upstream = list(node["inputs"]["model"])
            for other in graph.values():
                for key, val in other.get("inputs", {}).items():
                    if isinstance(val, list) and len(val) == 2 and str(val[0]) == lora_node_id:
                        other["inputs"][key] = list(upstream)
            graph.pop(lora_node_id, None)
            lora_node_id = None
        else:
            node["inputs"]["lora_name"] = lora
            node["inputs"]["strength_model"] = strength

    # optional second LoRA, chained after the first
    if lora2 and lora_node_id:
        clone_id = lora_node_id + "b"
        graph[clone_id] = {
            "inputs": {
                "lora_name": lora2,
                "strength_model": strength2,
                "model": [lora_node_id, 0],
            },
            "class_type": "LoraLoaderModelOnly",
            "_meta": {"title": "LoRA 2"},
        }
        for other in graph.values():
            for key, val in other.get("inputs", {}).items():
                if isinstance(val, list) and len(val) == 2 and str(val[0]) == lora_node_id \
                        and other is not graph[clone_id]:
                    other["inputs"][key] = [clone_id, 0]

    for node in graph.values():
        if node.get("class_type") == "BasicScheduler":
            node["inputs"]["steps"] = steps
        if node.get("class_type") == "MiniMaxH3ReferenceToVideo":
            node["inputs"]["ref_image_size"] = ref_size
            # video_size must drive the latent, or every render comes out at the
            # graph's hardcoded canvas regardless of what the job asked for.
            m = re.match(r"\s*(\d+)\s*[x×]\s*(\d+)", str(job.get("videoSize") or ""))
            if m:
                w, h = int(m.group(1)), int(m.group(2))
                w -= w % 32
                h -= h % 32
                if w >= 256 and h >= 256:
                    node["inputs"]["width"] = w
                    node["inputs"]["height"] = h
        if job.get("model") and node.get("class_type") == "UNETLoader":
            node["inputs"]["unet_name"] = job["model"]
        if node.get("class_type") == "RandomNoise" and job.get("seed") is not None:
            node["inputs"]["noise_seed"] = int(job["seed"])
    if CONFIG_DB.get("backend") != "comfyui":
        for node in graph.values():
            inputs = node.get("inputs") or {}
            for field in ("unet_name", "clip_name", "lora_name"):
                if inputs.get(field) in RUNPOD_NAME_MAP:
                    inputs[field] = RUNPOD_NAME_MAP[inputs[field]]
    return graph


def prepare_payload(job):
    graph_path = os.path.join(ASSETS, "graphs", job["graph"] + ".json")
    with open(graph_path) as f:
        graph = json.load(f)
    graph = apply_options(graph, job)
    refs = job.get("refs") or []
    images = []
    for name in refs:
        with open(os.path.join(ASSETS, "refs", name), "rb") as f:
            images.append(base64.b64encode(f.read()).decode())
    prompt = job.get("_prompt", "")
    return build_input(job, graph, prompt, images), graph, refs, prompt


def submit_worker(job_ids):
    """Runs in a thread: uploads then submits each queued job, in order."""
    for job_id in job_ids:
        with LOCK:
            job = find(job_id)
            if not job:
                continue
            job["status"] = "UPLOADING"
            save_jobs()
            prompt = job.get("_prompt", "")
            refs = job.get("refs") or []
        try:
            payload, graph, refs, prompt = prepare_payload(job)
            mb = round(len(json.dumps(payload)) / 1048576, 2)
            with LOCK:
                job["payloadMB"] = mb
                job["_input"] = {
                    "prompt_graph": {"nodes": len(graph), "class": "MiniMaxH3 workflow"},
                    "prompt": prompt,
                    "images": refs,
                    "seconds": payload["seconds"],
                    "video_size": payload["video_size"],
                    "enhance": payload["enhance"],
                    "upscale": payload["upscale"],
                    "rife": payload["rife"],
                    "seed": payload["seed"],
                    "job_name": payload["job_name"],
                    "lora": job.get("lora"),
                    "steps": job.get("steps"),
                }
                save_jobs()
            res = rp("POST", "/run", {"input": payload})
            with LOCK:
                job["runpodId"] = res.get("id")
                job["status"] = res.get("status", "IN_QUEUE")
                job["submittedAt"] = time.time()
                save_jobs()
        except Exception as e:
            with LOCK:
                job["status"] = "ERROR"
                job["error"] = str(e)[:500]
                save_jobs()
    if job_ids:
        with LOCK:
            first = find(job_ids[0])
            b = first.get("batch") if first else None
        if b:
            maybe_park(b)


BATCHES = []
BATCH_FILE = os.path.join(JOBS, "batches.json")


def save_batches():
    tmp = BATCH_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(BATCHES, f, indent=1)
    os.replace(tmp, BATCH_FILE)


def find_batch(bid):
    for b in BATCHES:
        if b["id"] == bid or b.get("localId") == bid:
            return b
    return None


def batch_worker(local_id):
    """Build the payloads, create the Runpod batch, append requests, finalize."""
    with LOCK:
        b = find_batch(local_id)
        if not b:
            return
        tpl = b["_template"]
        takes = b["takes"]
        reqs = []
        for i in range(takes):
            job = dict(tpl)
            job["seed"] = b["baseSeed"] + i
            job["name"] = f"{b['name']}-{take_suffix(i)}"
            payload, _graph, _refs, _prompt = prepare_payload(job)
            reqs.append({"name": job["name"], "seed": job["seed"],
                         "input": payload})
        b["requests"] = [{"name": r["name"], "seed": r["seed"]} for r in reqs]
        save_batches()
    try:
        created = rp("POST", "/batch", [])
        bid = created.get("id")
        with LOCK:
            b["id"] = bid
            b["status"] = "DRAFT"
            save_batches()
        # append in <=9 MiB chunks (the API takes a bare array here, not {"requests": [...]})
        chunk, size = [], 0
        for r in reqs:
            n = len(json.dumps({"input": r["input"]}))
            if chunk and size + n > 9 * 1048576:
                rp("POST", f"/batch/{bid}/requests", chunk)
                chunk, size = [], 0
            chunk.append({"input": r["input"]})
            size += n
        if chunk:
            rp("POST", f"/batch/{bid}/requests", chunk)
        rp("POST", f"/batch/{bid}/finalize")
        with LOCK:
            b["status"] = "FINALIZED"
            save_batches()
    except Exception as e:
        with LOCK:
            b["status"] = "ERROR"
            b["error"] = str(e)[:400]
            save_batches()


def poll_batches():
    with LOCK:
        pending = [b for b in BATCHES if b.get("id") and b["status"] in ("DRAFT", "FINALIZED", "QUEUED")]
    for b in pending:
        try:
            s = rp("GET", f"/batch/{b['id']}", timeout=60)
        except Exception as e:
            with LOCK:
                cur = find_batch(b["id"])
                if cur:
                    cur["lastError"] = str(e)[:200]
            continue
        with LOCK:
            cur = find_batch(b["id"])
            if not cur:
                continue
            cur["status"] = s.get("status", cur["status"])
            for k in ("requestTotal", "requestCompleted", "requestFailed", "requestInProgress"):
                if k in s:
                    cur[k] = s[k]
            save_batches()
        if cur["status"] == "COMPLETED" and not cur.get("imported"):
            import_batch_results(cur["id"])


def import_batch_results(bid):
    """Pull the finished requests and write each video into jobs/."""
    offset, limit, saved, idx, total = 0, 50, 0, 0, None
    with LOCK:
        b = find_batch(bid)
        names = [r["name"] for r in (b.get("requests") or [])] if b else []
    while True:
        try:
            page = rp("GET", f"/batch/{bid}/requests?offset={offset}&limit={limit}", timeout=120)
        except Exception:
            return
        if total is None:
            total = page.get("total")
        reqs = page.get("requests") or []
        for r in reqs:
            name = names[idx] if idx < len(names) else f"{bid[:8]}-{idx+1}"
            idx += 1
            out = r.get("output") or {}
            outs = out.get("outputs") if isinstance(out, dict) else None
            if r.get("status") == "COMPLETED" and outs and outs[0].get("b64"):
                try:
                    raw = base64.b64decode(outs[0]["b64"])
                    with open(os.path.join(JOBS, f"{name}.mp4"), "wb") as f:
                        f.write(raw)
                    saved += 1
                except Exception:
                    pass
        if not page.get("hasMore"):
            break
        offset += limit
    with LOCK:
        b = find_batch(bid)
        if b:
            b["imported"] = True
            b["saved"] = saved
            if saved == 0 and total:
                b["error"] = (f"Runpod reports {total} requests but returned none of their outputs — "
                              f"nothing left to import")
            save_batches()


def poll_loop():
    while True:
        time.sleep(max(2, int(CONFIG_DB.get("pollSeconds", 4))))
        try:
            poll_once()
            poll_batches()
            poll_comfy()
            maybe_autostop()
        except Exception:
            pass


# --------------------------------------------------------------------- vast
VAST_BASE = "https://console.vast.ai"


def vast_api(method, path, body=None, timeout=60):
    key = (CONFIG_DB.get("vastApiKey") or "").strip()
    if not key:
        raise RuntimeError("Vast API key is not set")
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {key}"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urlreq.Request(VAST_BASE + path, data=data, headers=headers, method=method)
    with urlreq.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode() or "{}"
        return json.loads(raw)


def vast_status():
    iid = (CONFIG_DB.get("vastInstanceId") or "").strip()
    if not iid:
        return {"configured": False, "rate": CONFIG_DB.get("vastRate")}
    inst = {}
    try:
        r = vast_api("GET", f"/api/v0/instances/{iid}/")
        inst = r.get("instances") or r
        if isinstance(inst, list):
            inst = inst[0] if inst else {}
    except Exception as e:
        try:
            r = vast_api("GET", "/api/v1/instances/")
            for x in (r.get("instances") or []):
                if str(x.get("id")) == str(iid):
                    inst = x
                    break
        except Exception:
            return {"configured": True, "id": iid, "error": str(e)[:200],
                    "rate": CONFIG_DB.get("vastRate")}
    if not isinstance(inst, dict):
        inst = {}
    host = inst.get("public_ipaddr")
    ports = inst.get("ports") or {}
    mapping = {}
    for k, v in ports.items():
        if not (isinstance(v, list) and v):
            continue
        key = str(k).split("/")[0]
        port = v[0].get("HostPort") if isinstance(v[0], dict) else v[0]
        if port:
            mapping[key] = str(port)
    return {
        "configured": True,
        "id": iid,
        "status": inst.get("actual_status") or inst.get("status") or "unknown",
        "gpu": inst.get("gpu_name"),
        "rate": CONFIG_DB.get("vastRate"),
        "host": host,
        "ports": mapping,
        "port": mapping.get("8189"),
        "url": CONFIG_DB.get("comfyUrl"),
    }


def vast_set_state(state):
    """state: 'running' | 'stopped'. v0 is the documented control path; v1 is tried first."""
    iid = (CONFIG_DB.get("vastInstanceId") or "").strip()
    if not iid:
        raise RuntimeError("Vast instance id is not set")
    last = None
    for path in (f"/api/v1/instances/{iid}/", f"/api/v0/instances/{iid}/"):
        try:
            return vast_api("PUT", path, {"state": state})
        except Exception as e:
            last = e
    raise last


def maybe_autostop():
    if CONFIG_DB.get("backend") != "comfyui":
        return
    minutes = int(CONFIG_DB.get("autoStopMin") or 0)
    if minutes <= 0 or not CONFIG_DB.get("vastInstanceId"):
        return
    with LOCK:
        busy = any(j["status"] in ("QUEUED", "UPLOADING", "SUBMITTED", "IN_PROGRESS")
                   for j in JOBS_DB)
        last = max([j.get("completedAt") or j.get("createdAt") or 0 for j in JOBS_DB] or [0])
        pend = any(b.get("status") in ("QUEUED", "DRAFT", "FINALIZED") for b in BATCHES)
        started = float(CONFIG_DB.get("vastStartedAt") or 0)
    if busy or pend:
        return
    # Idleness must be measured from the LATER of the last job and the current
    # start. Measuring from the last job alone stopped freshly started boxes on
    # the very next poll.
    idle_since = max(last, started)
    if time.time() - idle_since < minutes * 60:
        return
    st = vast_status()
    if st.get("status") not in ("running", "loading"):
        return
    try:
        vast_set_state("stopped")
        print(f"[vast] auto-stopped after {minutes} min idle", flush=True)
    except Exception as e:
        print(f"[vast] auto-stop failed: {e}", flush=True)
def comfy_base():
    """Resolve the ComfyUI base URL.

    A Vast instance gets a *new* host port every time it restarts, so when an
    instance id is configured the URL is derived from its live port map instead
    of trusting a stored address.
    """
    iid = (CONFIG_DB.get("vastInstanceId") or "").strip()
    if iid:
        try:
            st = vast_status()
            host = st.get("host")
            port = (st.get("ports") or {}).get("8189")
            if host and port:
                return f"http://{host}:{port}"
        except Exception:
            pass
    url = (CONFIG_DB.get("comfyUrl") or "").rstrip("/")
    if not url:
        raise RuntimeError("ComfyUI URL is not set")
    return url


def comfy_request(path, data=None, headers=None, timeout=120, raw=False):
    url = comfy_base() + path
    req = urlreq.Request(url, data=data, headers=headers or {}, method="POST" if data is not None else "GET")
    try:
        with urlreq.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return body if raw else json.loads(body.decode() or "{}")
    except urlerror.HTTPError as e:
        detail = e.read().decode(errors="replace")[:600]
        raise RuntimeError(f"ComfyUI {e.code}: {detail}")


def comfy_upload(name, blob):
    boundary = "----studio" + uuid.uuid4().hex
    parts = []
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
                 f"Content-Type: application/octet-stream\r\n\r\n".encode())
    parts.append(blob)
    parts.append(b"\r\n")
    for field, value in (("overwrite", "true"), ("subfolder", "")):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"\r\n\r\n{value}\r\n".encode())
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    res = comfy_request("/upload/image", data=body,
                        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    return res.get("name") or name


def _ref_bytes(name):
    """Reference bytes, capped the way the Pixaroma loader caps them.

    The public worker's loader carries LoadImageMiniState with max_mp 1 and
    longest_side 1024, so every reference is downscaled to <=1024 px before
    conditioning. Matching that keeps token weight in the same range.
    """
    path = os.path.join(ASSETS, "refs", name)
    raw = open(path, "rb").read()
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        if max(im.size) > 1024:
            im.thumbnail((1024, 1024), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=95)
            return buf.getvalue()
    except Exception:
        pass
    return raw


def comfy_ready_graph(job):
    """Load the workflow, upload its references, and point stock LoadImage nodes at them.

    Slots beyond the number of references are dropped, so one template serves
    2-, 3-, 4- and 5-reference scenes.
    """
    graph_path = os.path.join(ASSETS, "graphs", job["graph"] + ".json")
    with open(graph_path) as f:
        graph = json.load(f)
    graph = apply_options(graph, job)
    loaders = sorted((k for k, v in graph.items()
                      if v.get("class_type") in ("LoadImage", "PixaromaLoadImageMini")),
                     key=lambda k: (len(k), k))
    refs = job.get("refs") or []
    r2v = next((v for v in graph.values()
                if v.get("class_type") == "MiniMaxH3ReferenceToVideo"), None)

    # A loader titled GUIDE is the composition anchor: it takes the LAST reference
    # (usually the scene still) and is never counted as a reference slot.
    guide_ids = [k for k in loaders
                 if str((graph[k].get("_meta") or {}).get("title", "")).upper() == "GUIDE"]
    slot_ids = [k for k in loaders if k not in guide_ids]

    for idx, node_id in enumerate(slot_ids):
        if idx >= len(refs):
            graph.pop(node_id, None)
            if r2v is not None:
                r2v["inputs"].pop(f"ref_images.ref_image_{idx}", None)
            continue
        blob = _ref_bytes(refs[idx])
        uploaded = comfy_upload(refs[idx], blob)
        node = graph[node_id]
        node["class_type"] = "LoadImage"
        node["inputs"] = {"image": uploaded}

    if guide_ids and refs:
        blob = _ref_bytes(refs[-1])
        uploaded = comfy_upload(refs[-1], blob)
        for node_id in guide_ids:
            graph[node_id]["class_type"] = "LoadImage"
            graph[node_id]["inputs"] = {"image": uploaded}
    else:
        for node_id in guide_ids:
            graph.pop(node_id, None)
    return graph


def comfy_submit_worker(job_ids):
    for job_id in job_ids:
        with LOCK:
            job = find(job_id)
            if not job:
                continue
            job["status"] = "UPLOADING"
            save_jobs()
        try:
            graph = comfy_ready_graph(job)
            res = comfy_request("/prompt", data=json.dumps({
                "prompt": graph,
                "client_id": uuid.uuid4().hex,
            }).encode(), headers={"Content-Type": "application/json"}, timeout=180)
            with LOCK:
                job["promptId"] = res.get("prompt_id")
                job["status"] = "SUBMITTED"
                job["submittedAt"] = time.time()
                job["_input"] = {
                    "backend": "comfyui",
                    "graph": job["graph"],
                    "images": job.get("refs") or [],
                    "seconds": job["seconds"],
                    "video_size": job["videoSize"],
                    "seed": job["seed"],
                    "steps": job.get("steps"),
                    "lora": job.get("lora"),
                    "lora2": job.get("lora2"),
                    "model": job.get("model"),
                }
                save_jobs()
        except Exception as e:
            with LOCK:
                job["status"] = "ERROR"
                job["error"] = str(e)[:400]
                save_jobs()


def poll_comfy():
    with LOCK:
        pending = [dict(j) for j in JOBS_DB
                   if j.get("backend") == "comfyui" and j.get("promptId")
                   and j["status"] in ("SUBMITTED", "IN_PROGRESS")]
    for job in pending:
        try:
            hist = comfy_request(f"/history/{job['promptId']}", timeout=60)
        except Exception as e:
            with LOCK:
                j = find(job["id"])
                if j:
                    j["lastError"] = str(e)[:200]
            continue
        entry = hist.get(job["promptId"]) if isinstance(hist, dict) else None
        if not entry:
            with LOCK:
                j = find(job["id"])
                if j:
                    j["status"] = "IN_PROGRESS"
            continue
        status = entry.get("status") or {}
        msgs = status.get("messages") or []
        ts = {}
        for m in msgs:
            if isinstance(m, list) and len(m) > 1 and isinstance(m[1], dict):
                ts[m[0]] = m[1].get("timestamp")
        exec_ms = None
        if "execution_start" in ts and "execution_success" in ts:
            exec_ms = int(ts["execution_success"] - ts["execution_start"])
        files = []
        for out in (entry.get("outputs") or {}).values():
            for key in ("gifs", "videos", "images"):
                files.extend(out.get(key) or [])
        media = next((f for f in files
                      if str(f.get("filename", "")).lower().endswith((".mp4", ".webm", ".gif"))), None)
        if media:
            try:
                q = urlencode({"filename": media["filename"],
                               "subfolder": media.get("subfolder", ""),
                               "type": media.get("type", "output")})
                blob = comfy_request(f"/view?{q}", timeout=300, raw=True)
                fname = f"{job['name']}.mp4"
                with open(os.path.join(JOBS, fname), "wb") as f:
                    f.write(blob)
                with LOCK:
                    j = find(job["id"])
                    if j:
                        j["file"] = fname
                        j["bytes"] = len(blob)
                        j["status"] = "COMPLETED"
                        j["completedAt"] = time.time()
                        if exec_ms:
                            j["executionTime"] = exec_ms
                            j["queueTime"] = max(0, int((j["completedAt"] - j.get("submittedAt", j["completedAt"])) * 1000) - exec_ms)
                        save_jobs()
            except Exception as e:
                with LOCK:
                    j = find(job["id"])
                    if j:
                        j["status"] = "ERROR"
                        j["error"] = f"download failed: {e}"[:300]
                        save_jobs()
        elif status.get("status_str") == "error":
            with LOCK:
                j = find(job["id"])
                if j:
                    j["status"] = "ERROR"
                    j["error"] = "ComfyUI reported an execution error"
                    save_jobs()


def poll_once():
    with LOCK:
        pending = [
            dict(j) for j in JOBS_DB
            if j.get("runpodId")
            and j["status"] in ("IN_QUEUE", "IN_PROGRESS", "RUNNING", "SENDING")
        ]
    for job in pending:
        try:
            s = rp("GET", f"/status/{job['runpodId']}", timeout=90)
        except Exception as e:
            with LOCK:
                j = find(job["id"])
                if j:
                    j["lastError"] = str(e)[:300]
            continue
        status = s.get("status", "")
        out = s.get("output") or {}
        done_video = None
        if isinstance(out, dict):
            outs = out.get("outputs") or []
            if outs and isinstance(outs[0], dict) and outs[0].get("b64"):
                done_video = outs[0]
        raw = None
        if status == "COMPLETED" and done_video:
            try:
                raw = base64.b64decode(done_video["b64"])
            except Exception as e:
                raw = None
                done_video = {"decode_error": str(e)}
        with LOCK:
            j = find(job["id"])
            if not j:
                continue
            j["status"] = status
            if s.get("executionTime"):
                j["executionTime"] = s["executionTime"]
            if s.get("delayTime"):
                j["delayTime"] = s["delayTime"]
            if status == "COMPLETED" and done_video:
                fname = f"{j['name']}.mp4"
                try:
                    if raw is None:
                        raise RuntimeError(done_video.get("decode_error", "no video"))
                    with open(os.path.join(JOBS, fname), "wb") as f:
                        f.write(raw)
                    j["file"] = fname
                    j["bytes"] = len(raw)
                    j["sourceName"] = done_video.get("filename")
                except Exception as e:
                    j["error"] = f"save failed: {e}"
                j["completedAt"] = time.time()
            if status in ("FAILED", "CANCELLED", "TIMED_OUT"):
                j["error"] = str(s.get("error") or status)[:500]
            save_jobs()

        if status in TERMINAL:
            maybe_park(job.get("batch"))


def maybe_park(batch):
    """Park the endpoint (workers.max=0) once every job of a batch is terminal."""
    if not batch or not CONFIG_DB.get("autoPark"):
        return
    with LOCK:
        if batch in PARKED_BATCHES:
            return
        stale = [j for j in JOBS_DB if j.get("batch") == batch]
        if not stale or any(j["status"] not in TERMINAL for j in stale):
            return
        PARKED_BATCHES.add(batch)
    try:
        rp_mgmt("PATCH", body={"workers": {"max": 0}}, timeout=60)
    except Exception:
        with LOCK:
            PARKED_BATCHES.discard(batch)
        return
    with LOCK:
        for j in JOBS_DB:
            if j.get("batch") == batch:
                j["batchParked"] = True
        save_jobs()


# ----------------------------------------------------------------- http layer
class Handler(BaseHTTPRequestHandler):
    server_version = "ServerAIStudio/1.0"

    def log_message(self, fmt, *args):
        pass

    # -- helpers
    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, download_name=None):
        if not os.path.isfile(path):
            self.send_json({"error": "not found"}, 404)
            return
        ext = os.path.splitext(path)[1].lower()
        size = os.path.getsize(path)
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        partial = False
        if rng and rng.startswith("bytes="):
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
                partial = True
        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        if ext in (".html", ".js", ".css"):
            self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(262144, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def read_json(self, limit=64 * 1024 * 1024):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        if n > limit:
            raise ValueError("body too large")
        return json.loads(self.rfile.read(n).decode())

    # -- routes
    def do_GET(self):
        u = urlparse(self.path)
        p = unquote(u.path)

        if p in ("/", "/index.html"):
            return self.send_file(os.path.join(STATIC, "index.html"))

        if p == "/api/state":
            with LOCK:
                cfg = {
                    "endpointId": CONFIG_DB.get("endpointId", ""),
                    "hasKey": bool(CONFIG_DB.get("apiKey")),
                    "defaultVideoSize": CONFIG_DB.get("defaultVideoSize", "1344x768"),
                    "defaultSeconds": CONFIG_DB.get("defaultSeconds", 10),
                    "pollSeconds": CONFIG_DB.get("pollSeconds", 4),
                    "autoPark": bool(CONFIG_DB.get("autoPark")),
                    "backend": CONFIG_DB.get("backend", "runpod"),
                    "comfyUrl": CONFIG_DB.get("comfyUrl", ""),
                    "hasVastKey": bool(CONFIG_DB.get("vastApiKey")),
                    "vastInstanceId": CONFIG_DB.get("vastInstanceId", ""),
                    "vastRate": CONFIG_DB.get("vastRate", 1.2),
                    "autoStopMin": CONFIG_DB.get("autoStopMin", 10),
                }
                setup = setup_state()
                jobs = [strip(j) for j in JOBS_DB]
                batches = [public_batch(b) for b in BATCHES]
                done = [j for j in JOBS_DB if j.get("executionTime") and j.get("file")]
                ex = sorted(j["executionTime"] for j in done)
                rate = (CONFIG_DB.get("vastRate") or 0) if CONFIG_DB.get("backend") == "comfyui" else 3.49
                stats = {
                    "clips": len(done),
                    "medianExecMs": ex[len(ex) // 2] if ex else 0,
                    "meanExecMs": int(sum(ex) / len(ex)) if ex else 0,
                    "totalExecS": round(sum(ex) / 1000, 1),
                    "estCost": round(sum(ex) / 1000 / 3600 * rate, 2),
                    "rate": rate,
                }
            return self.send_json({
                "config": cfg,
                "setup": setup,
                "jobs": jobs,
                "batches": batches,
                "stats": stats,
                "library": library(),
                "options": {"loras": dynamic_options()["loras"], "models": dynamic_options()["models"], "defaults": DEFAULT_OPTS},
                "health": None,
            })

        if p == "/api/options":
            dyn = dynamic_options()
            return self.send_json({"loras": dyn["loras"], "models": dyn["models"], "defaults": DEFAULT_OPTS})

        if p == "/api/availability":
            try:
                e = rp_mgmt("GET")
            except Exception as ex:
                return self.send_json({"error": str(ex)}, 502)
            pools = (e.get("gpu") or {}).get("pools") or []
            try:
                cat = _http("GET", MGMT_BASE + "/catalog/gpus?include=AVAILABILITY&product=SERVERLESS&count=1", timeout=45)
                arr = cat if isinstance(cat, list) else (cat.get("items") or [])
            except Exception as ex:
                arr = []
            gpus = []
            for g in arr:
                if g.get("pool") not in pools:
                    continue
                gpus.append({
                    "name": g.get("name"),
                    "pool": g.get("pool"),
                    "hourly": (g.get("price") or {}).get("serverless"),
                    "availability": g.get("availability"),
                    "dataCenters": [{"id": d.get("id"), "availability": d.get("availability")}
                                    for d in (g.get("dataCenters") or [])],
                })
            return self.send_json({
                "pools": pools,
                "dataCenters": e.get("dataCenterIds"),
                "gpus": gpus,
                "catalogEmpty": len(arr) == 0,
                "health": health(),
            })

        if p == "/api/vast":
            return self.send_json(vast_status())

        if p == "/api/batches":
            with LOCK:
                return self.send_json([public_batch(b) for b in BATCHES])

        if p == "/api/health":
            return self.send_json(health())

        if p.startswith("/api/jobs/") and p.count("/") == 3:
            job_id = p.split("/")[3]
            with LOCK:
                j = find(job_id)
                if not j:
                    return self.send_json({"error": "not found"}, 404)
                detail = strip(j)
                detail["prompt"] = j.get("_prompt", "")
                detail["input"] = j.get("_input")
            return self.send_json(detail)

        if p == "/api/endpoint":
            try:
                e = rp_mgmt("GET")
            except Exception as ex:
                return self.send_json({"error": str(ex)}, 502)
            w = e.get("workers") or {}
            return self.send_json({
                "name": e.get("name"),
                "workers": w,
                "gpu": (e.get("gpu") or {}).get("gpuTypeIds"),
                "dataCenters": e.get("dataCenterIds"),
                "flashboot": e.get("flashboot"),
            })

        if p.startswith("/media/"):
            name = os.path.basename(p[len("/media/"):])
            return self.send_file(os.path.join(JOBS, name))

        if p.startswith("/download/"):
            name = os.path.basename(p[len("/download/"):])
            return self.send_file(os.path.join(JOBS, name), download_name=name)

        if p.startswith("/assets/"):
            rel = p[len("/assets/"):]
            target = os.path.normpath(os.path.join(ASSETS, rel))
            if not target.startswith(ASSETS):
                return self.send_json({"error": "forbidden"}, 403)
            return self.send_file(target)

        if p.startswith("/static/"):
            rel = p[len("/static/"):]
            target = os.path.normpath(os.path.join(STATIC, rel))
            if not target.startswith(STATIC):
                return self.send_json({"error": "forbidden"}, 403)
            return self.send_file(target)

        self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        p = unquote(u.path)
        try:
            body = self.read_json()
        except Exception as e:
            return self.send_json({"error": f"bad body: {e}"}, 400)

        if p == "/api/config":
            with LOCK:
                for k in ("endpointId", "defaultVideoSize", "defaultSeconds", "pollSeconds"):
                    if k in body:
                        CONFIG_DB[k] = body[k]
                for k in ("backend", "comfyUrl"):
                    if k in body:
                        CONFIG_DB[k] = (body[k] or "").strip() if k == "comfyUrl" else body[k]
                for k in ("vastApiKey", "vastInstanceId"):
                    if body.get(k):
                        if k == "vastInstanceId" and body[k].strip() != CONFIG_DB.get(k):
                            CONFIG_DB["vastStartedAt"] = 0
                        CONFIG_DB[k] = body[k].strip()
                for k in ("vastRate", "autoStopMin"):
                    if k in body:
                        try:
                            CONFIG_DB[k] = float(body[k])
                        except (TypeError, ValueError):
                            pass
                if "autoPark" in body:
                    CONFIG_DB["autoPark"] = bool(body["autoPark"])
                for k in ("setupDone", "setupSkipped"):
                    if k in body:
                        CONFIG_DB[k] = bool(body[k])
                if body.get("apiKey"):
                    CONFIG_DB["apiKey"] = body["apiKey"].strip()
                if body.get("clearKey"):
                    CONFIG_DB["apiKey"] = ""
                if body.get("clearVastKey"):
                    CONFIG_DB["vastApiKey"] = ""
                save_config()
            return self.send_json({"ok": True, "setup": setup_state()})

        if p == "/api/submit":
            return self.submit(body)

        if p == "/api/batch":
            return self.create_batch(body)

        if p == "/api/vast/action":
            action = (body.get("action") or "").lower()
            if action not in ("start", "stop"):
                return self.send_json({"error": "action must be start or stop"}, 400)
            try:
                vast_set_state("running" if action == "start" else "stopped")
                if action == "start":
                    with LOCK:
                        CONFIG_DB["vastStartedAt"] = time.time()
                        save_config()
            except Exception as e:
                return self.send_json({"error": str(e)[:300]}, 502)
            return self.send_json({"ok": True, "status": vast_status()})

        if p.startswith("/api/batches/") and p.endswith("/cancel"):
            bid = p.split("/")[3]
            with LOCK:
                b = find_batch(bid)
                rid = b.get("id") if b else None
            if not rid:
                return self.send_json({"error": "batch not submitted yet"}, 400)
            try:
                rp("POST", f"/batch/{rid}/cancel")
            except Exception as e:
                return self.send_json({"error": str(e)}, 502)
            with LOCK:
                b = find_batch(bid)
                if b:
                    b["status"] = "CANCELLED"
                    save_batches()
            return self.send_json({"ok": True})

        if p.startswith("/api/batches/") and p.endswith("/retry"):
            bid = p.split("/")[3]
            with LOCK:
                b = find_batch(bid)
                if not b or not b.get("_template"):
                    return self.send_json({"error": "nothing to retry"}, 400)
                rec = {
                    "localId": uuid.uuid4().hex[:10],
                    "id": None,
                    "name": b["name"],
                    "status": "QUEUED",
                    "takes": b["takes"],
                    "baseSeed": b["baseSeed"],
                    "requestTotal": b["takes"],
                    "requestCompleted": 0,
                    "requestFailed": 0,
                    "createdAt": time.time(),
                    "requests": [],
                    "_template": dict(b["_template"]),
                }
                BATCHES.append(rec)
                save_batches()
            threading.Thread(target=batch_worker, args=(rec["localId"],), daemon=True).start()
            return self.send_json({"ok": True, "id": rec["localId"]})

        if p == "/api/endpoint/workers":
            try:
                n = int(body.get("max"))
            except (TypeError, ValueError):
                return self.send_json({"error": "max must be an integer"}, 400)
            if not 0 <= n <= 25:
                return self.send_json({"error": "max must be between 0 and 25"}, 400)
            try:
                e = rp_mgmt("PATCH", body={"workers": {"max": n}}, timeout=60)
            except Exception as ex:
                return self.send_json({"error": str(ex)}, 502)
            return self.send_json({"ok": True, "workers": e.get("workers")})

        if p.startswith("/api/jobs/") and p.endswith("/cancel"):
            job_id = p.split("/")[3]
            with LOCK:
                j = find(job_id)
                rid = j.get("runpodId") if j else None
                is_comfy = bool(j and j.get("backend") == "comfyui")
            if is_comfy:
                try:
                    comfy_request("/interrupt", data=b"{}",
                                  headers={"Content-Type": "application/json"}, timeout=30)
                except Exception as e:
                    return self.send_json({"error": str(e)}, 502)
                with LOCK:
                    j = find(job_id)
                    if j:
                        j["status"] = "CANCELLED"
                        save_jobs()
                return self.send_json({"ok": True, "status": "CANCELLED"})
            if not rid:
                return self.send_json({"error": "not submitted yet"}, 400)
            try:
                res = rp("POST", f"/cancel/{rid}")
            except Exception as e:
                return self.send_json({"error": str(e)}, 502)
            with LOCK:
                j = find(job_id)
                if j:
                    j["status"] = res.get("status", "CANCELLED")
                    save_jobs()
                    batch = j.get("batch")
                else:
                    batch = None
            if batch:
                maybe_park(batch)
            return self.send_json({"ok": True, "status": res.get("status")})

        if p == "/api/upload-ref":
            name = os.path.basename(body.get("name") or "upload.jpg")
            b64 = (body.get("b64") or "").split(",", 1)[-1]
            if not b64:
                return self.send_json({"error": "empty file"}, 400)
            safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name)
            stem, ext = os.path.splitext(safe)
            if ext.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                return self.send_json({"error": "jpg, png or webp only"}, 400)
            refdir = os.path.join(ASSETS, "refs")
            os.makedirs(refdir, exist_ok=True)
            n = 2
            while os.path.exists(os.path.join(refdir, safe)):
                safe = f"{stem}-{n}{ext}"
                n += 1
            try:
                raw = base64.b64decode(b64)
            except Exception:
                return self.send_json({"error": "not valid base64"}, 400)
            with open(os.path.join(refdir, safe), "wb") as f:
                f.write(raw)
            return self.send_json({"ok": True, "name": safe, "bytes": len(raw)})

        if p == "/api/prompts":
            pid = re.sub(r"\s+", "-", (body.get("id") or "").strip())
            pid = re.sub(r"[^A-Za-z0-9._-]+", "-", pid).strip("-")[:60]
            text = body.get("text") or ""
            if not pid or not text.strip():
                return self.send_json({"error": "name and text are required"}, 400)
            with open(os.path.join(ASSETS, "prompts", pid + ".txt"), "w") as f:
                f.write(text)
            return self.send_json({"ok": True, "id": pid})

        self.send_json({"error": "not found"}, 404)

    def do_DELETE(self):
        p = unquote(urlparse(self.path).path)
        if p.startswith("/api/jobs/"):
            job_id = p.split("/")[3]
            with LOCK:
                j = find(job_id)
                if not j:
                    return self.send_json({"error": "not found"}, 404)
                if j.get("file"):
                    try:
                        os.remove(os.path.join(JOBS, j["file"]))
                    except OSError:
                        pass
                JOBS_DB.remove(j)
                save_jobs()
            return self.send_json({"ok": True})

        if p.startswith("/api/batches/"):
            bid = p.split("/")[3]
            with LOCK:
                b = find_batch(bid)
                if not b:
                    return self.send_json({"error": "not found"}, 404)
                rid = b.get("id")
                active = b.get("status") in ("DRAFT", "FINALIZED", "QUEUED")
                BATCHES.remove(b)
                save_batches()
            if rid and active:
                try:
                    rp("POST", f"/batch/{rid}/cancel")
                except Exception:
                    pass
            return self.send_json({"ok": True})

        if p.startswith("/api/refs/"):
            name = os.path.basename(p[len("/api/refs/"):])
            target = os.path.join(ASSETS, "refs", name)
            if not name or not os.path.isfile(target):
                return self.send_json({"error": "not found"}, 404)
            try:
                os.remove(target)
            except OSError as e:
                return self.send_json({"error": str(e)}, 500)
            return self.send_json({"ok": True})

        if p.startswith("/api/prompts/"):
            pid = os.path.basename(p[len("/api/prompts/"):])
            if pid.endswith(".txt"):
                pid = pid[:-4]
            target = os.path.join(ASSETS, "prompts", pid + ".txt")
            if not pid or not os.path.isfile(target):
                return self.send_json({"error": "not found"}, 404)
            try:
                os.remove(target)
            except OSError as e:
                return self.send_json({"error": str(e)}, 500)
            return self.send_json({"ok": True})

        self.send_json({"error": "not found"}, 404)

    # -- submit
    def submit(self, body):
        with LOCK:
            backend = CONFIG_DB.get("backend", "runpod")
            if backend == "comfyui":
                if not (CONFIG_DB.get("comfyUrl") or CONFIG_DB.get("vastInstanceId")):
                    return self.send_json(
                        {"error": "Connect a ComfyUI machine first — open Setup and add its address or Vast machine ID"}, 400)
            elif not CONFIG_DB.get("endpointId") or not CONFIG_DB.get("apiKey"):
                return self.send_json({"error": "Connect Runpod first — open Setup and add your endpoint ID and API key"}, 400)

        prompt = (body.get("prompt") or "").strip()
        if not prompt:
            return self.send_json({"error": "prompt is empty"}, 400)
        refs = body.get("refs") or []
        if not refs:
            return self.send_json({"error": "pick at least one reference image"}, 400)
        graph = body.get("graph") or "graph-h3-stock-2ref"
        graph_path = os.path.join(ASSETS, "graphs", graph + ".json")
        if not os.path.isfile(graph_path):
            return self.send_json({"error": f"unknown graph {graph}"}, 400)
        with open(graph_path) as f:
            g = json.load(f)
        needed = sum(1 for n in g.values()
                     if isinstance(n, dict)
                     and n.get("class_type") in ("PixaromaLoadImageMini", "LoadImage")
                     and ((n.get("_meta") or {}).get("title") != "GUIDE"))
        if len(refs) > needed:
            return self.send_json(
                {"error": f"{graph} takes {needed} reference(s), {len(refs)} given"}, 400)

        base = (body.get("name") or "clip").strip() or "clip"
        base = re.sub(r"[^A-Za-z0-9._-]+", "-", base)[:48]
        try:
            start_seed = int(body.get("seed") or 10000)
        except ValueError:
            return self.send_json({"error": "seed must be an integer"}, 400)
        try:
            takes = max(1, min(20, int(body.get("takes") or 1)))
        except ValueError:
            takes = 1
        seconds = body.get("seconds") or CONFIG_DB.get("defaultSeconds", 10)
        video_size = body.get("videoSize") or CONFIG_DB.get("defaultVideoSize", "1344x768")

        opts = {k: body.get(k, DEFAULT_OPTS[k]) for k in DEFAULT_OPTS}
        created = []
        batch = uuid.uuid4().hex[:8]
        backend = CONFIG_DB.get("backend", "runpod")
        with LOCK:
            for i in range(takes):
                jid = uuid.uuid4().hex[:12]
                name = f"{base}-{take_suffix(i)}"
                job = {
                    "id": jid,
                    "name": name,
                    "group": base,
                    "batch": batch,
                    "take": take_suffix(i),
                    "seed": start_seed + i,
                    "seconds": seconds,
                    "videoSize": video_size,
                    "graph": graph,
                    "refs": refs,
                    "promptChars": len(prompt),
                    "status": "QUEUED",
                    "createdAt": time.time(),
                    "backend": backend,
                    "_prompt": prompt,
                    **opts,
                }
                JOBS_DB.append(job)
                created.append(jid)
            save_jobs()
        worker = comfy_submit_worker if backend == "comfyui" else submit_worker
        threading.Thread(target=worker, args=(created,), daemon=True).start()
        return self.send_json({"ok": True, "created": created, "backend": backend})

    # -- batch (best-effort, discounted)
    def create_batch(self, body):
        with LOCK:
            if CONFIG_DB.get("backend") == "comfyui":
                return self.send_json({"error": "batches are a Runpod feature — switch the backend to runpod"}, 400)
            if not CONFIG_DB.get("endpointId") or not CONFIG_DB.get("apiKey"):
                return self.send_json({"error": "Connect Runpod first — open Setup and add your endpoint ID and API key"}, 400)
        prompt = (body.get("prompt") or "").strip()
        if not prompt:
            return self.send_json({"error": "prompt is empty"}, 400)
        refs = body.get("refs") or []
        if not refs:
            return self.send_json({"error": "pick at least one reference image"}, 400)
        graph = body.get("graph") or "graph-h3-stock-2ref"
        if not os.path.isfile(os.path.join(ASSETS, "graphs", graph + ".json")):
            return self.send_json({"error": f"unknown graph {graph}"}, 400)
        base = re.sub(r"[^A-Za-z0-9._-]+", "-", (body.get("name") or "batch").strip())[:40] or "batch"
        try:
            start_seed = int(body.get("seed") or 10000)
            takes = max(1, min(5000, int(body.get("takes") or 1)))
        except ValueError:
            return self.send_json({"error": "seed and takes must be integers"}, 400)
        opts = {k: body.get(k, DEFAULT_OPTS[k]) for k in DEFAULT_OPTS}
        rec = {
            "localId": uuid.uuid4().hex[:10],
            "id": None,
            "name": base,
            "status": "QUEUED",
            "takes": takes,
            "baseSeed": start_seed,
            "requestTotal": takes,
            "requestCompleted": 0,
            "requestFailed": 0,
            "createdAt": time.time(),
            "requests": [],
            "_template": {
                "seconds": body.get("seconds") or CONFIG_DB.get("defaultSeconds", 10),
                "videoSize": body.get("videoSize") or CONFIG_DB.get("defaultVideoSize", "1344x768"),
                "graph": graph,
                "refs": refs,
                "_prompt": prompt,
                **opts,
            },
        }
        with LOCK:
            BATCHES.append(rec)
            save_batches()
        threading.Thread(target=batch_worker, args=(rec["localId"],), daemon=True).start()
        return self.send_json({"ok": True, "id": rec["localId"], "requests": takes})


def strip(job):
    """Public view of a job: no prompt body, no ref bytes."""
    return {k: v for k, v in job.items() if not k.startswith("_")}


def public_batch(b):
    return {k: v for k, v in b.items() if not k.startswith("_")}


def comfy_enum(node, field):
    """Allowed values for an enum input, straight from the live ComfyUI."""
    info = comfy_request(f"/object_info/{node}", timeout=30)
    spec = (((info.get(node) or {}).get("input") or {}).get("required") or {}).get(field)
    if isinstance(spec, list) and spec and isinstance(spec[0], list):
        return list(spec[0])
    if isinstance(spec, list) and spec and isinstance(spec[0], str):
        return [spec[0]]
    opts = (spec[1] or {}).get("options") if isinstance(spec, list) and len(spec) > 1 else None
    return [o for o in (opts or []) if isinstance(o, str)]


_ENUM_CACHE = {"at": 0, "models": None, "loras": None}


def dynamic_options():
    """Model/LoRA lists for the active backend."""
    if CONFIG_DB.get("backend") != "comfyui":
        return {"models": MODELS, "loras": LORAS}
    if time.time() - _ENUM_CACHE["at"] < 60 and _ENUM_CACHE["models"] is not None:
        return {"models": _ENUM_CACHE["models"], "loras": _ENUM_CACHE["loras"]}
    try:
        unets = comfy_enum("UNETLoader", "unet_name")
        loras = comfy_enum("LoraLoaderModelOnly", "lora_name")
        models = [{"id": "", "name": "graph default"}] + [
            {"id": u, "name": u.replace(".safetensors", "")} for u in unets]
        lora_opts = [{"id": "", "name": "none"}] + [
            {"id": l, "name": l.replace(".safetensors", "")} for l in loras]
        _ENUM_CACHE.update({"at": time.time(), "models": models, "loras": lora_opts})
        return {"models": models, "loras": lora_opts}
    except Exception:
        return {"models": MODELS, "loras": LORAS}


def library():
    refs = []
    refdir = os.path.join(ASSETS, "refs")
    if os.path.isdir(refdir):
        refs = sorted(f for f in os.listdir(refdir)
                      if f.lower().endswith((".jpg", ".jpeg", ".png")))
    prompts = []
    pdir = os.path.join(ASSETS, "prompts")
    if os.path.isdir(pdir):
        for f in sorted(os.listdir(pdir)):
            if f.endswith(".txt"):
                prompts.append({"id": f[:-4], "title": f[:-4]})
    graphs = []
    gdir = os.path.join(ASSETS, "graphs")
    if os.path.isdir(gdir):
        for f in sorted(os.listdir(gdir)):
            if f.endswith(".json"):
                try:
                    with open(os.path.join(gdir, f)) as fh:
                        g = json.load(fh)
                    slots = sum(1 for n in g.values()
                                if isinstance(n, dict)
                                and n.get("class_type") in ("PixaromaLoadImageMini", "LoadImage")
                     and ((n.get("_meta") or {}).get("title") != "GUIDE"))
                except Exception:
                    slots = None
                graphs.append({"id": f[:-5], "refs": slots})
    return {"refs": refs, "prompts": prompts, "graphs": graphs}


def main():
    os.makedirs(JOBS, exist_ok=True)
    os.makedirs(os.path.join(ASSETS, "refs"), exist_ok=True)
    os.makedirs(os.path.join(ASSETS, "prompts"), exist_ok=True)
    os.makedirs(os.path.join(ASSETS, "graphs"), exist_ok=True)
    load_state()
    # Batches that finished while the app was off: pull their results now.
    for b in list(BATCHES):
        if b.get("id") and b.get("status") == "COMPLETED" and not b.get("imported"):
            threading.Thread(target=import_batch_results, args=(b["id"],), daemon=True).start()
    threading.Thread(target=poll_loop, daemon=True).start()
    port = int(os.environ.get("PORT", "8780"))
    host = os.environ.get("HOST", "127.0.0.1")
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"Server AI Studio  ->  http://{host}:{port}")
    print(f"Runpod endpoint: {CONFIG_DB.get('endpointId') or '(not set)'}")
    print(f"Config: {CONFIG}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
