#!/bin/bash
# Vast bootstrap — reaches a working ComfyUI with H3 on ports 8188 (logs) / 8189 (ComfyUI).
# Assumes the devel image (has gcc) and that this script is PID 1, so it must never exit.
set -x
mkdir -p /workspace && cd /workspace
nohup python3 -m http.server 8188 --directory /workspace > /workspace/httpd.log 2>&1 &
exec > /workspace/setup.log 2>&1
echo "START $(date)"
export DEBIAN_FRONTEND=noninteractive CC=gcc
apt-get update -y
apt-get install -y git ffmpeg libgl1 libglib2.0-0 curl build-essential

dl () {
  url="$1"; out="$2"; mkdir -p "$(dirname "$out")"
  for i in $(seq 1 15); do
    curl -fL -C - --retry 3 --retry-delay 2 --retry-all-errors -o "$out" "$url"; rc=$?
    if [ $rc -eq 0 ] || [ $rc -eq 33 ]; then echo "ok $out"; return 0; fi
    sleep 2
  done
  echo "FAILED $out"; return 1
}

python - <<'EOF' > /tmp/c.txt || true
try:
    import torch, torchvision
    print(f"torch=={torch.__version__}")
    print(f"torchvision=={torchvision.__version__}")
except Exception:
    pass
EOF

[ -d ComfyUI ] || git clone --depth 1 https://github.com/comfyanonymous/ComfyUI
cd /workspace/ComfyUI
pip install --no-cache-dir -r requirements.txt -c /tmp/c.txt

M=/workspace/ComfyUI/models
dl https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_video_vae_fp16.safetensors $M/vae/minimax_h3_video_vae_fp16.safetensors
dl https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_audio_vae_fp32.safetensors $M/vae/minimax_h3_audio_vae_fp32.safetensors
dl https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors $M/loras/turbo_ref2v_4step.safetensors
dl https://huggingface.co/fal/MiniMax-H3-Realism-People-LoRA/resolve/main/h3-realism-people-t2v-i2v-r2v.safetensors $M/loras/realism_people.safetensors
dl https://huggingface.co/akatz-ai/MiniMax-H3-Character-Swap-LoRA/resolve/main/h3_character_swap_pro4500_1000.safetensors $M/loras/character_swap.safetensors
dl https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors $M/text_encoders/qwen3vl_32b_nvfp4_awq.safetensors
dl https://huggingface.co/WarmBloodAban/Minimax-h3_Singularity/resolve/main/Minimax-h3_Singularity_ref2va_v2_pruned_int8.safetensors $M/diffusion_models/sing_v2_pruned_int8.safetensors

ls -la $M/diffusion_models $M/text_encoders $M/vae $M/loras
echo DOWNLOADS_DONE

# Keep the container alive: PID 1 must never return.
while true; do
  echo "=== comfy start $(date)" >> /workspace/comfy.log
  python /workspace/ComfyUI/main.py --listen 0.0.0.0 --port 8189 >> /workspace/comfy.log 2>&1
  echo "=== comfy exited $? $(date)" >> /workspace/comfy.log
  sleep 10
done
