#!/usr/bin/env python3
"""Ask a ComfyUI instance what its nodes actually expect.

    python3 tools/comfy_probe.py http://host:port

Prints required/optional inputs per node so a workflow can be generated from
the live schema instead of guessing. Also lists any H3-related classes it finds
that are not in our reference set.
"""

import json
import sys
import urllib.request

WANTED = [
    "MiniMaxH3ReferenceToVideo",
    "UNETLoader",
    "CLIPLoader",
    "VAELoader",
    "LoraLoaderModelOnly",
    "BasicScheduler",
    "KSamplerSelect",
    "BasicGuider",
    "RandomNoise",
    "SamplerCustomAdvanced",
    "VAEDecode",
    "VAEDecodeAudio",
    "CreateVideo",
    "SaveVideo",
    "LoadImage",
    "PrimitiveInt",
    "PrimitiveString",
    "PrimitiveFloat",
]


def fetch(base):
    with urllib.request.urlopen(base.rstrip("/") + "/object_info", timeout=60) as r:
        return json.loads(r.read().decode())


def describe(node):
    inp = node.get("input", {})
    order = node.get("input_order", {})
    out = []
    for section in ("required", "optional"):
        for name, spec in (inp.get(section) or {}).items():
            kind = spec[0] if isinstance(spec, list) and spec else spec
            if isinstance(kind, list):
                kind = f"enum[{len(kind)}]"
            opts = spec[1] if isinstance(spec, list) and len(spec) > 1 and isinstance(spec[1], dict) else {}
            flags = ",".join(k for k in ("default", "min", "max", "step") if k in opts)
            out.append(f"    {section[0].upper()} {name}: {kind}" + (f" ({flags})" if flags else ""))
    returns = node.get("output_name") or node.get("output") or []
    return "\n".join(out), returns


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8188"
    info = fetch(base)
    print(f"# {len(info)} node classes on {base}\n")
    for name in WANTED:
        node = info.get(name)
        if not node:
            print(f"## {name}: MISSING\n")
            continue
        body, returns = describe(node)
        print(f"## {name} -> {returns}")
        print(body or "    (no inputs)")
        print()
    h3 = sorted(n for n in info if "h3" in n.lower() or "minimax" in n.lower())
    print("## H3-related classes present")
    for n in h3:
        print("   ", n)


if __name__ == "__main__":
    main()
