#!/usr/bin/env python3
"""Rent a Vast.ai 96 GB machine and run tools/vast_bootstrap.sh on it.

    export VAST_API_KEY=...          # or save it in the app's Settings first
    python3 tools/vast_rent.py [--label h3-vast]

The script searches for a 96 GB RTX PRO 6000-class offer, creates the instance,
waits for the mapped ComfyUI port, and writes the machine ID and hourly rate
into ~/.server-ai-studio/config.json so Server AI Studio can use it.

GPU rates and bandwidth are billed by Vast.ai. The offer price is printed before
anything is created; nothing is rented until the script has chosen an offer.
"""

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.environ.get("SERVER_AI_STUDIO_CONFIG") or os.path.join(
    os.path.expanduser("~"), ".server-ai-studio", "config.json")
BASE = "https://console.vast.ai"
IMAGE = "pytorch/pytorch:2.7.1-cuda12.8-cudnn9-devel"


def load_key():
    key = (os.environ.get("VAST_API_KEY") or "").strip()
    if key:
        return key
    try:
        with open(CONFIG) as f:
            return (json.load(f).get("vastApiKey") or "").strip()
    except Exception:
        return ""


def save_instance(instance_id, rate):
    cfg = {}
    try:
        with open(CONFIG) as f:
            cfg = json.load(f)
    except Exception:
        pass
    cfg["vastInstanceId"] = str(instance_id)
    cfg["vastRate"] = float(rate)
    cfg["backend"] = "comfyui"
    cfg["setupDone"] = True
    os.makedirs(os.path.dirname(CONFIG), mode=0o700, exist_ok=True)
    tmp = CONFIG + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=1)
    os.replace(tmp, CONFIG)
    try:
        os.chmod(CONFIG, 0o600)
    except OSError:
        pass


def api(key, method, path, body=None, timeout=90):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return False, e.read().decode()[:200]


def main():
    key = load_key()
    if not key:
        print("No Vast.ai API key. Set VAST_API_KEY or add the key in Server AI Studio → Settings.")
        return 1

    label = "h3-vast"
    if "--label" in sys.argv:
        label = sys.argv[sys.argv.index("--label") + 1]

    script = open(os.path.join(ROOT, "tools", "vast_bootstrap.sh")).read()
    args = f"bash -c 'echo {base64.b64encode(script.encode()).decode()} | base64 -d > /tmp/s.sh; bash /tmp/s.sh'"

    query = {"gpu_name": {"in": ["RTX PRO 6000 S", "RTX PRO 6000 WS", "RTX PRO 6000 Max-Q"]},
             "num_gpus": {"eq": 1}, "rentable": {"eq": True}, "type": "ondemand",
             "reliability": {"gte": 0.94}, "disk_space": {"gte": 220}, "inet_down": {"gte": 1000}}
    ok, data = api(key, "POST", "/api/v0/bundles/", query)
    offers = [o for o in data.get("offers", []) if o.get("dph_total")]
    offers.sort(key=lambda o: o["dph_total"])
    print(f"{len(offers)} offers, cheapest ${offers[0]['dph_total']:.2f}/hr" if offers else "no offers")
    if not offers:
        return 1

    for offer in offers[:6]:
        body = {"image": IMAGE, "label": label, "disk": 200, "runtype": "args",
                "args_str": args, "env": {"-p 8188:8188": "1", "-p 8189:8189": "1"},
                "cancel_unavail": True}
        ok, res = api(key, "PUT", f"/api/v0/asks/{offer['id']}/", body)
        if ok and res.get("new_contract"):
            iid = res["new_contract"]
            rate = float(offer["dph_total"])
            print(f"CREATED {iid}  ${rate:.2f}/hr  {offer.get('geolocation')}")
            save_instance(iid, rate)
            print(f"Saved machine ID and rate to {CONFIG}")
            for _ in range(20):
                time.sleep(12)
                ok, detail = api(key, "GET", f"/api/v0/instances/{iid}/")
                inst = detail.get("instances", {}) if ok else {}
                ports = {k.split("/")[0]: v[0]["HostPort"]
                         for k, v in (inst.get("ports") or {}).items() if v}
                print(" ", inst.get("actual_status"), ports)
                if ports.get("8189"):
                    print(f"COMFY http://{inst['public_ipaddr']}:{ports['8189']}")
                    return 0
            print("created but ports are not mapped yet — check the Vast.ai console")
            return 0
        print("skip offer", offer["id"], str(res)[:70])
    return 1


if __name__ == "__main__":
    sys.exit(main())
