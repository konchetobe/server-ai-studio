# Phone setup — Termux + OpenCode

Server AI Studio is a small stdlib-Python web app, so it runs natively on an
Android phone through [Termux](https://termux.dev/). OpenCode is optional: use it
if you want an AI agent to help you set up Runpod/Vast.ai and edit the project
from the phone.

There are two separate things to install:

1. **Termux + Python + this app** — enough to use Server AI Studio.
2. **OpenCode** — an AI coding agent that runs in the terminal, if you want one.

---

## 1. Install Termux

Install Termux from **F-Droid** or the **GitHub releases** page:

- <https://f-droid.org/packages/com.termux/>
- <https://github.com/termux/termux-app/releases>

Avoid the Play Store build; it is old and its packages are not compatible with
current Termux.

Open Termux and update:

```bash
pkg update && pkg upgrade -y
pkg install -y python git ripgrep unzip
```

Check Python:

```bash
python --version
```

Python **3.10 or newer** is required. The app has no third-party dependencies.

---

## 2. Get the app

```bash
git clone https://github.com/YOUR-USER/server-ai-studio.git
cd server-ai-studio
```

If you are testing a local folder instead of GitHub, copy the project into
Termux's home directory with `cp -r`, or use `termux-setup-storage` and work from
shared storage.

Start it:

```bash
python app.py
```

You will see:

```
Server AI Studio  ->  http://127.0.0.1:8780
Config: /data/data/com.termux/files/home/.server-ai-studio/config.json
```

Open **http://127.0.0.1:8780** in the phone's browser. The first-run wizard asks
for a Runpod or Vast.ai connection. You can press **Skip for now** and add the
keys later with the **Setup** button.

---

## 3. Keep it running

Android may suspend or kill background processes. For a longer session:

```bash
termux-wake-lock
```

Also open Android **Settings → Apps → Termux → Battery** and set it to
**Unrestricted**. On Android 12+, the "phantom process killer" can still end
long-running background processes; keeping the Termux notification visible and
the screen on while rendering helps.

Use `tmux` if you want the app to survive closing the Termux view:

```bash
pkg install -y tmux
tmux new -s studio
python app.py
```

Detach with **Ctrl+B**, then **D**. Reattach with:

```bash
tmux attach -t studio
```

---

## 4. Access from another device on the same Wi-Fi (optional)

By default the app listens only on the phone. To open it from a tablet or
laptop on the same trusted network:

```bash
HOST=0.0.0.0 python app.py
```

Then browse to `http://<phone-ip>:8780`. Find the phone IP in Android Wi-Fi
settings or with `ip addr`.

> There is no login screen. Do not expose the app to the public internet, and do
> not run `HOST=0.0.0.0` on a network you do not trust.

---

## 5. Install OpenCode on the phone

OpenCode's official installer downloads a glibc Linux binary, which Android
cannot run directly. On Termux you have two practical paths.

### Option A — community native build (easiest)

[`guysoft/opencode-termux`](https://github.com/guysoft/opencode-termux) publishes
an Android aarch64 build that runs directly in Termux.

1. Open the releases page on the phone:

   <https://github.com/guysoft/opencode-termux/releases/latest>

2. Download the **`opencode-*-android-aarch64.zip`** asset.
3. In Termux, go to the download folder (usually `~/storage/downloads` after
   `termux-setup-storage`), then install:

   ```bash
   pkg install -y ripgrep unzip
   mkdir -p "$PREFIX/libexec/opencode" "$PREFIX/lib"
   unzip opencode-*-android-aarch64.zip
   mv opencode "$PREFIX/bin/opencode"
   chmod +x "$PREFIX/bin/opencode"
   mv opencode.bin "$PREFIX/libexec/opencode/opencode.bin"
   chmod +x "$PREFIX/libexec/opencode/opencode.bin"
   mv libtagfix.so libc++_shared.so libopentui.so "$PREFIX/lib/"
   ```

4. Run:

   ```bash
   opencode
   ```

The native build is community-maintained and may lag the newest OpenCode
release. It is still the least-friction on-device option.

### Option B — proot-distro Ubuntu (current upstream)

If you need the latest OpenCode, run it inside a small Ubuntu userland:

```bash
pkg install -y proot-distro
proot-distro install ubuntu
proot-distro login ubuntu
```

Inside Ubuntu:

```bash
apt update
apt install -y curl git ripgrep
curl -fsSL https://opencode.ai/v2/install | bash
```

Follow the installer's PATH hint, then:

```bash
opencode
```

To work on the project inside the chroot, clone it there:

```bash
git clone https://github.com/YOUR-USER/server-ai-studio.git
cd server-ai-studio
opencode
```

The app itself does not need the chroot; you can keep running it in plain Termux
and use OpenCode only for editing/help.

---

## 6. Connect OpenCode to an AI provider

Inside OpenCode:

```text
/connect
```

Choose a provider and paste its API key. Then:

```text
/models
```

to pick a model. OpenCode stores its own credentials separately from Server AI
Studio's Runpod/Vast.ai keys.

Useful first prompt inside the project:

```text
Read README.md first. I want to set up MiniMax H3 on Runpod.
Follow docs/RUNPOD-MINIMAX-H3.md step by step.
Do not create any billable Runpod resource without asking me first.
```

Or, for Vast.ai:

```text
Read README.md and docs/VAST-MINIMAX-H3.md.
Walk me through renting a 96 GB machine and connecting it to this app.
Ask before renting anything.
```

OpenCode can also run the tests:

```bash
python test_smoke.py
```

---

## 7. Where the files go on the phone

| item | location |
|---|---|
| App config and keys | `~/.server-ai-studio/config.json` (mode 600) |
| Reference images you upload | `assets/refs/` (gitignored) |
| Saved prompts | `assets/prompts/` (gitignored) |
| Rendered clips | `jobs/` (gitignored) |
| App code | the cloned `server-ai-studio` folder |

Use the app's **Save** button on a finished clip to put it in the phone's
Downloads folder, or open it in the browser's video player.

---

## Troubleshooting

| symptom | fix |
|---|---|
| `python: command not found` | `pkg install python` |
| Browser cannot open `127.0.0.1:8780` | Make sure the app is still running and the Termux session is alive. Try `curl http://127.0.0.1:8780` in Termux. |
| App dies when Termux is in the background | `termux-wake-lock`; set Termux battery usage to Unrestricted; keep the notification visible. |
| `opencode: cannot execute` after npm install | Use the native build or proot-distro path above; the official Linux binary is not Android-compatible. |
| OpenCode starts but has no model | Run `/connect`, add a provider key, then `/models`. |
| OpenCode cannot find `rg` | `pkg install ripgrep` (native) or `apt install ripgrep` (proot). |
| Phone storage fills up | Rendered clips are large. Delete old files from `jobs/` or move them to Downloads. |
| Keys were committed by mistake | Remove them from git history immediately and rotate the keys in Runpod/Vast.ai. `.gitignore` already excludes the local config. |
