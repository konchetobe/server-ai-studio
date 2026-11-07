#!/usr/bin/env python3
"""Offline smoke test: runs the app with Runpod mocked out.

    python3 test_smoke.py

Exercises the HTTP API end to end without touching a GPU or an account.
"""

import base64
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as A

PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"
CALLS = []


def fake_rp(method, path, body=None, timeout=None):
    CALLS.append((method, path, body))
    if method == "POST" and path == "/run":
        n = len([c for c in CALLS if c[1] == "/run"])
        return {"id": f"fake-{n}-u1", "status": "IN_QUEUE"}
    if method == "POST" and path == "/batch":
        return {"id": "batch-1", "status": "DRAFT"}
    if method == "POST" and path.startswith("/batch/") and path.endswith("/requests"):
        # the real API rejects an object here; it wants a bare array
        assert isinstance(body, list), "batch append must send a top-level array"
        return {"ok": True}
    if method == "POST" and path.startswith("/batch/") and path.endswith("/finalize"):
        return {"id": "batch-1", "status": "FINALIZED"}
    if method == "GET" and path.startswith("/batch/") and "/requests" in path:
        return {"requests": [{
            "id": "req_1", "status": "COMPLETED",
            "output": {"outputs": [{"b64": base64.b64encode(b"BATCH-MP4").decode(), "filename": "x.mp4"}]},
            "delayTime": 1000, "executionTime": 2000,
        }], "total": 1, "offset": 0, "limit": 50, "hasMore": False}
    if method == "GET" and path.startswith("/batch/"):
        return {"id": "batch-1", "status": "COMPLETED", "requestTotal": 1,
                "requestCompleted": 1, "requestFailed": 0}
    if method == "GET" and path.startswith("/status/"):
        return {
            "status": "COMPLETED",
            "executionTime": 121000,
            "delayTime": 4000,
            "output": {
                "outputs": [{"b64": base64.b64encode(b"FAKE-MP4-BYTES").decode(),
                             "filename": "out.mp4"}],
                "texts": {},
            },
        }
    if method == "POST" and path.startswith("/cancel/"):
        return {"id": path.rsplit("/", 1)[-1], "status": "CANCELLED"}
    if method == "GET" and path == "/health":
        return {"jobs": {"inQueue": 0, "inProgress": 0, "completed": 0},
                "workers": {"idle": 1, "running": 0}}
    raise AssertionError(f"unexpected call {method} {path}")


def fake_rp_mgmt(method, path="", body=None, timeout=None):
    CALLS.append((method, "mgmt" + path, body))
    if method == "GET":
        return {"name": "test-endpoint", "workers": {"min": 0, "max": 1, "idleTimeout": 5}}
    if method == "PATCH":
        return {"name": "test-endpoint",
                "workers": {"min": 0, "max": body["workers"]["max"], "idleTimeout": 5}}
    raise AssertionError(f"unexpected mgmt call {method}")


FAKE = {}


class FakeComfy(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        if self.path == "/upload/image":
            FAKE["upload"] = body[:80]
            return self._json({"name": "uploaded.png", "subfolder": "", "type": "input"})
        if self.path == "/prompt":
            FAKE["prompt"] = json.loads(body.decode())
            return self._json({"prompt_id": "p1"})
        if self.path == "/interrupt":
            return self._json({"ok": True})
        self._json({})

    def do_GET(self):
        if self.path.startswith("/history/"):
            return self._json({"p1": {
                "status": {"status_str": "success", "completed": True},
                "outputs": {"126": {"gifs": [{"filename": "out.mp4", "subfolder": "", "type": "output"}]}},
            }})
        if self.path.startswith("/view"):
            blob = b"COMFY-MP4-BYTES"
            self.send_response(200)
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)
            return
        self._json({})


def wait(cond, timeout=8):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.05)
    return False


def req(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(r, timeout=20)
    except urllib.error.HTTPError as e:
        resp = e
    with resp:
        raw = resp.read()
        try:
            return resp.status, json.loads(raw)
        except Exception:
            return resp.status, raw


def main():
    A.rp = fake_rp
    A.rp_mgmt = fake_rp_mgmt

    # The test gets its own config, jobs, references, prompts, and graphs.
    # It must never read the user's ~/.server-ai-studio/config.json.
    tmp = tempfile.mkdtemp(prefix="studio-test-")
    A.CONFIG = os.path.join(tmp, "config.json")
    A.JOBS = os.path.join(tmp, "jobs")
    A.ASSETS = os.path.join(tmp, "assets")
    A.INDEX = os.path.join(A.JOBS, "index.json")
    A.BATCH_FILE = os.path.join(A.JOBS, "batches.json")
    for folder in (A.JOBS, os.path.join(A.ASSETS, "refs"), os.path.join(A.ASSETS, "prompts")):
        os.makedirs(folder, exist_ok=True)
    shutil.copytree(os.path.join(A.ROOT, "assets", "graphs"),
                    os.path.join(A.ASSETS, "graphs"))
    one_px = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Z4xkAAAAASUVORK5CYII=")
    for name in ("ref-a.png", "ref-b.png", "ref-c.png", "ref-d.png", "ref-e.png"):
        with open(os.path.join(A.ASSETS, "refs", name), "wb") as f:
            f.write(one_px)
    with open(os.path.join(A.ASSETS, "prompts", "demo.txt"), "w") as f:
        f.write("A short demo prompt for the smoke test.\n")

    A.load_state()
    A.CONFIG_DB.update({"endpointId": "", "apiKey": "", "pollSeconds": 2,
                        "backend": "runpod", "comfyUrl": "", "vastInstanceId": "",
                        "vastApiKey": "", "setupDone": False, "setupSkipped": False})
    A.JOBS_DB.clear()
    A.BATCHES.clear()

    srv = ThreadingHTTPServer(("127.0.0.1", PORT), A.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.3)

    ok = []

    # 1. static page + first-run setup state
    st, page = req("/")
    ok.append(("index.html", st == 200 and b"SERVER AI" in page))
    st, s = req("/api/state")
    ok.append(("state", st == 200 and "config" in s and "library" in s))
    ok.append(("fresh install asks for setup", s["setup"]["needed"] is True))
    ok.append(("secrets never serialized", "apiKey" not in s["config"]
               and "vastApiKey" not in s["config"]))
    ok.append(("library refs", {"ref-a.png", "ref-b.png"} <= set(s["library"]["refs"])))
    ok.append(("library prompts", "demo" in {p["id"] for p in s["library"]["prompts"]}))
    ok.append(("library graphs", "graph-h3-stock-2ref" in {g["id"] for g in s["library"]["graphs"]}))

    # skip is persistent
    st, r = req("/api/config", "POST", {"setupDone": True, "setupSkipped": True})
    st, s = req("/api/state")
    ok.append(("skip setup", st == 200 and s["setup"]["needed"] is False
               and s["setup"]["skipped"] is True))

    # connect Runpod from the same API the wizard uses
    st, r = req("/api/config", "POST", {
        "setupDone": True, "setupSkipped": False, "backend": "runpod",
        "endpointId": "testendpoint", "apiKey": "rpa_test",
        "defaultVideoSize": "1344x768", "defaultSeconds": 10,
    })
    st, s = req("/api/state")
    ok.append(("wizard saves Runpod", st == 200 and s["config"]["hasKey"]
               and s["setup"]["runpodReady"] and not s["setup"]["needed"]))

    # 2. two takes with 2 refs
    refs = s["library"]["refs"][:2]
    st, r = req("/api/submit", "POST", {
        "prompt": "test prompt", "refs": refs, "graph": "graph-h3-stock-2ref",
        "seconds": 15, "videoSize": "768x1344", "seed": 700, "takes": 2, "name": "smoke",
    })
    ok.append(("submit accepted", st == 200 and len(r["created"]) == 2))
    ok.append(("uploader ran", wait(lambda: all(
        j.get("runpodId") for j in A.JOBS_DB if j["id"] in r["created"]))))

    runs = [c for c in CALLS if c[1] == "/run"]
    ok.append(("two /run calls", len(runs) == 2))

    # job detail keeps the prompt and the redacted input
    st, det = req(f"/api/jobs/{r['created'][0]}")
    ok.append(("detail has prompt", st == 200 and det.get("prompt") == "test prompt"))
    ok.append(("detail has input", (det.get("input") or {}).get("seed") == 700
               and det.get("input", {}).get("video_size") == "768x1344"))
    ok.append(("detail has refs", len(det.get("refs") or []) == 2))
    if runs:
        inp = runs[0][2]["input"]
        ok.append(("graph nested object", isinstance(inp["prompt_graph"], dict)))
        ok.append(("2 images inline", len(inp["images"]) == 2))
        ok.append(("seeds increment", [c[2]["input"]["seed"] for c in runs] == [700, 701]))
        ok.append(("vertical size", inp["video_size"] == "768x1344"))
        ok.append(("enhance off", inp["enhance"] is False))

    # 3. poll marks them complete and writes the file
    A.poll_once()
    done = [j for j in A.JOBS_DB if j.get("group") == "smoke"]
    ok.append(("poll completed", all(j["status"] == "COMPLETED" for j in done)))
    ok.append(("files written", all(j.get("file") and os.path.exists(
        os.path.join(A.JOBS, j["file"])) for j in done)))

    st, blob = req(f"/media/{done[0]['file']}")
    ok.append(("media served", st == 200 and blob == b"FAKE-MP4-BYTES"))

    # 4. validation: too many refs for the 2-ref graph
    st, e = req("/api/submit", "POST", {
        "prompt": "x", "refs": s["library"]["refs"][:5],
        "graph": "graph-h3-stock-2ref", "name": "bad",
    })
    ok.append(("slot guard", st == 400 and "takes 2 reference" in e.get("error", "")))

    # 5. empty prompt rejected
    st, e = req("/api/submit", "POST", {"prompt": "", "refs": refs, "name": "bad"})
    ok.append(("empty prompt guard", st == 400))

    # 6. delete removes the record and the file
    st, _ = req(f"/api/jobs/{done[0]['id']}", "DELETE")
    ok.append(("delete", st == 200 and not any(j["id"] == done[0]["id"] for j in A.JOBS_DB)))

    # 7. own references: upload, name collision, delete from library
    png = base64.b64encode(
        b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()
    up = {"name": "new scene.png", "b64": "data:image/png;base64," + png}
    st, r1 = req("/api/upload-ref", "POST", up)
    st2, r2 = req("/api/upload-ref", "POST", up)
    ok.append(("ref upload", st == 200 and r1["name"] == "new-scene.png"))
    ok.append(("collision suffix", st2 == 200 and r2["name"] == "new-scene-2.png"))
    st, lib = req("/api/state")
    ok.append(("upload in library", {"new-scene.png", "new-scene-2.png"}
               <= set(lib["library"]["refs"])))
    st, _ = req("/api/refs/new-scene.png", "DELETE")
    st, lib = req("/api/state")
    ok.append(("ref delete", st == 200 and "new-scene.png" not in lib["library"]["refs"]))
    req("/api/refs/new-scene-2.png", "DELETE")

    # 8. own prompts: save and delete
    st, pr = req("/api/prompts", "POST", {"id": "my scene", "text": "hello world"})
    ok.append(("prompt save", st == 200 and pr["id"] == "my-scene"
               and os.path.exists(os.path.join(A.ASSETS, "prompts", "my-scene.txt"))))
    st, _ = req("/api/prompts/my-scene", "DELETE")
    ok.append(("prompt delete", st == 200
               and not os.path.exists(os.path.join(A.ASSETS, "prompts", "my-scene.txt"))))

    # 9. endpoint workers control
    st, ep = req("/api/endpoint")
    ok.append(("endpoint read", st == 200 and ep["workers"]["max"] == 1))
    st, r = req("/api/endpoint/workers", "POST", {"max": 0})
    ok.append(("pause accepted", st == 200 and r["workers"]["max"] == 0))
    st, r = req("/api/endpoint/workers", "POST", {"max": 3})
    ok.append(("unpause accepted", st == 200 and r["workers"]["max"] == 3))
    st, r = req("/api/endpoint/workers", "POST", {"max": 99})
    ok.append(("worker bounds", st == 400))

    # 10. park the endpoint when the whole batch is finished
    A.CONFIG_DB["autoPark"] = True
    CALLS.clear()
    st, r = req("/api/submit", "POST", {
        "prompt": "p", "refs": refs[:1], "graph": "graph-h3-stock-2ref",
        "name": "parkme", "takes": 2, "seed": 900,
    })
    wait(lambda: all(j.get("runpodId") for j in A.JOBS_DB if j["id"] in r["created"]))
    A.poll_once()
    A.poll_once()
    parks = [c for c in CALLS if c[0] == "PATCH" and ((c[2] or {}).get("workers") or {}).get("max") == 0]
    ok.append(("auto park fires once", len(parks) == 1))
    ok.append(("batch marked parked", all(j.get("batchParked") for j in A.JOBS_DB
                                          if j["id"] in r["created"])))
    A.CONFIG_DB["autoPark"] = False

    # 11. options: LoRA removal and step override reach the graph
    st, o = req("/api/options")
    ok.append(("options listed", st == 200 and len(o["loras"]) >= 2 and o["defaults"]["steps"] == 6))
    CALLS.clear()
    st, r = req("/api/submit", "POST", {
        "prompt": "opt test", "refs": refs[:1], "graph": "graph-h3-stock-2ref",
        "name": "optcheck", "takes": 1, "seed": 5, "lora": "", "steps": 8,
    })
    wait(lambda: any(c[1] == "/run" for c in CALLS))
    payload = [c for c in CALLS if c[1] == "/run"][0][2]["input"]
    g = payload["prompt_graph"]
    ok.append(("lora removed", not any(n.get("class_type") == "LoraLoaderModelOnly" for n in g.values())))
    ok.append(("steps applied", all(n["inputs"]["steps"] == 8 for n in g.values()
                                    if n.get("class_type") == "BasicScheduler")))

    # 12. batch create -> finalize -> poll -> import
    st, r = req("/api/batch", "POST", {
        "prompt": "batch test", "refs": refs[:1], "graph": "graph-h3-stock-2ref",
        "name": "bc", "takes": 2, "seed": 300,
    })
    ok.append(("batch accepted", st == 200 and r["requests"] == 2))
    ok.append(("batch submitted", wait(lambda: (A.find_batch(r["id"]) or {}).get("id") == "batch-1")))
    A.poll_batches()
    b = A.find_batch(r["id"])
    ok.append(("batch completed", b["status"] == "COMPLETED"))
    ok.append(("batch imported", bool(b.get("imported")) and b.get("saved") == 1))

    # 11b. stacked LoRA + checkpoint override
    CALLS.clear()
    st, r = req("/api/submit", "POST", {
        "prompt": "stack test", "refs": refs[:1], "graph": "graph-h3-stock-2ref",
        "name": "stack", "takes": 1, "seed": 6,
        "lora": "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
        "lora2": "h3-realism-people-t2v-i2v-r2v.safetensors",
        "model": "Minimax-h3_Singularity_ref2va_v2_pruned_int8.safetensors",
    })
    wait(lambda: any(c[1] == "/run" for c in CALLS))
    g2 = [c for c in CALLS if c[1] == "/run"][0][2]["input"]["prompt_graph"]
    loras = [n for n in g2.values() if n.get("class_type") == "LoraLoaderModelOnly"]
    unet = [n for n in g2.values() if n.get("class_type") == "UNETLoader"][0]
    ok.append(("two loras chained", len(loras) == 2
               and any(n["inputs"]["lora_name"].startswith("h3-realism") for n in loras)))
    ok.append(("checkpoint override", unet["inputs"]["unet_name"].endswith("Pruned_v1.3_int8.safetensors")))
    clip = [n for n in g2.values() if n.get("class_type") == "CLIPLoader"][0]
    ok.append(("runpod text-encoder name", clip["inputs"]["clip_name"].endswith("heretic_minimax_h3_nvfp4.safetensors")))

    # 13. ComfyUI backend end to end (fake server)
    comfy = ThreadingHTTPServer(("127.0.0.1", 8801), FakeComfy)
    threading.Thread(target=comfy.serve_forever, daemon=True).start()
    A.CONFIG_DB["backend"] = "comfyui"
    A.CONFIG_DB["comfyUrl"] = "http://127.0.0.1:8801"
    st, r = req("/api/submit", "POST", {
        "prompt": "comfy test", "refs": refs[:1], "graph": "graph-h3-stock-2ref",
        "name": "comfy", "takes": 1, "seed": 11, "videoSize": "768x1344",
    })
    ok.append(("comfy submit", st == 200 and r.get("backend") == "comfyui"))
    ok.append(("comfy accepted prompt", wait(lambda: (A.find(r["created"][0]) or {}).get("promptId") == "p1")))
    sent = FAKE.get("prompt") or {}
    loaders = [n for n in (sent.get("prompt") or {}).values() if n.get("class_type") == "LoadImage"]
    ok.append(("comfy rewrote loaders", len(loaders) == 1
               and loaders[0]["inputs"]["image"] == "uploaded.png"))
    noise = [n for n in (sent.get("prompt") or {}).values() if n.get("class_type") == "RandomNoise"]
    ok.append(("comfy applies seed", bool(noise) and noise[0]["inputs"]["noise_seed"] == 11))
    r2v = [n for n in (sent.get("prompt") or {}).values()
           if n.get("class_type") == "MiniMaxH3ReferenceToVideo"]
    ok.append(("comfy applies video_size", bool(r2v) and r2v[0]["inputs"]["width"] == 768
               and r2v[0]["inputs"]["height"] == 1344))
    A.poll_comfy()
    j = A.find(r["created"][0])
    ok.append(("comfy completed", j["status"] == "COMPLETED" and j.get("file")))
    st, e = req("/api/batch", "POST", {
        "prompt": "x", "refs": refs[:1], "graph": "graph-h3-stock-2ref", "name": "nope", "takes": 1,
    })
    ok.append(("comfy rejects batches", st == 400 and "Runpod" in e.get("error", "")))
    A.CONFIG_DB["backend"] = "runpod"
    comfy.shutdown()

    srv.shutdown()

    width = max(len(n) for n, _ in ok)
    bad = 0
    for name, good in ok:
        print(f"  {'PASS' if good else 'FAIL'}  {name.ljust(width)}")
        bad += 0 if good else 1
    print(f"\n{len(ok) - bad}/{len(ok)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
