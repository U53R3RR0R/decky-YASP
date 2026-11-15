# Y.A.S.P. — Yet Another Streaming Plugin

Y.A.S.P. captures the Steam Game Mode X11 display, publishes a low-latency local feed to MediaMTX, serves that feed to LAN viewers over WebRTC, and can ask MediaMTX to forward compatible audio/video tracks to Twitch, YouTube, Restream.io, or a custom RTMP(S)/RTSP(S) destination.

## Features

- Start/stop streaming from Quick Access Menu (QAM).
- Choose 16:9 resolutions (1280×720, 1600×900, 1920×1080) or 16:10 resolutions (1280×800, 1440×900, 1920×1200), video bitrate, audio bitrate, quality preset, and display rotation.
- Watch on the LAN using the WebRTC URL shown by the plugin.
- Select Twitch, YouTube, Restream.io, or a custom RTMP(S)/RTSP(S) destination.
- Keep runtime binaries, configuration, logs, and settings under plugin-owned storage; no SteamOS-specific or system-wide writable paths are assumed.
- Optionally install a verified, plugin-managed FFmpeg build from QAM, or use a compatible host FFmpeg.

## Streaming architecture

FFmpeg captures the full X11 display and sends H.264 video plus audio to MediaMTX over RTSP/UDP on loopback. MediaMTX then serves WebRTC to LAN viewers and, when configured, forwards compatible tracks to the selected external destination. This avoids encoding the video twice and keeps the local feed available independently of an external publishing failure.

For external RTMP publishing, FFmpeg supplies both AAC (for RTMP services) and Opus (for WebRTC). MediaMTX forwards tracks; it does not transcode them. External destinations must accept the H.264/AAC stream. Restream.io can fan out a single incoming stream to multiple configured platforms; Y.A.S.P. itself configures one MediaMTX forwarding destination at a time.

The local WebRTC endpoint is shown in QAM, typically `http://<LAN-IP>:8889/yasp`. Connect the viewer device to the same LAN and allow inbound TCP port 8889 and UDP port 8189 through the host firewall if required. RTSP ingest and RTP/RTCP use loopback only.

## Destination setup

- **Twitch:** enter the stream key. Y.A.S.P. uses Twitch's default RTMP ingest endpoint.
- **YouTube:** enter the stream key. Y.A.S.P. uses YouTube's RTMP ingest endpoint.
- **Restream.io:** enter the stream key from Restream. Y.A.S.P. uses `rtmp://live.restream.io/live`.
- **Custom RTMP(S):** provide a publish URL. Both `rtmp(s)://host/app/stream-key` and MediaMTX-style `rtmp(s)://host/app#stream-key` forms are accepted.
- **Custom RTSP(S):** provide the complete publish URL.

Stream keys and custom URLs are stored in the plugin's settings file with restrictive permissions and are omitted from status responses and redacted from logs. Do not share diagnostic logs without reviewing them.

## Host requirements and limitations

- Linux Steam Game Mode with an X11 display. Wayland/portal/PipeWire screen capture is not implemented; startup reports this limitation rather than publishing a blank capture.
- `ffmpeg` with `x11grab`, H.264 encoding (`libx264`), Opus encoding, and, for external publishing, AAC encoding.
- `xdpyinfo` and `pactl`, plus an available PulseAudio/PipeWire audio monitor.
- A compatible MediaMTX release for the host's supported architecture, downloaded and stored in plugin-owned storage.

FFmpeg can be installed from QAM as a GPLv3 static build published by the FFmpeg-Builds project. The downloader selects x86_64 or ARM64, verifies the GitHub release's SHA-256 digest, extracts only the executable, and retains the archive's license documentation in plugin-owned storage. The binary is downloaded on demand rather than included in the install ZIP because release archives are roughly 120–150 MiB. The upstream Linux builds target glibc 2.28 or newer. A compatible host FFmpeg remains supported as a fallback.

`xdpyinfo`, `pactl`, and an available PulseAudio/PipeWire audio monitor remain host dependencies. MediaMTX and the optional FFmpeg binary are stored in plugin-owned directories, suitable for immutable distributions when those directories are writable. Streaming cannot start if required capture/audio tools are missing, the FFmpeg build lacks required features, or a runtime download fails.

## Build and package

The frontend build and tests need Node.js and pnpm. The Decky CLI creates the installable ZIP and uses a Docker-compatible container engine to build the frontend. The generated ZIP is written to `out/`. Building the ZIP is separate from `pnpm run build`, which only creates the frontend `dist/` files.

### Standard Arch Linux

Install the tools and start Docker:

```bash
sudo pacman -S --needed base-devel git nodejs npm pnpm docker curl
sudo systemctl enable --now docker
docker info
```

Clone this repository, then from its root install the locked dependencies, run the tests, build the frontend, and install the Decky CLI helper:

```bash
git clone https://github.com/U53R3RR0R/decky-YASP.git
cd decky-YASP
pnpm install --frozen-lockfile
pnpm test
pnpm run build
bash .vscode/setup.sh
```

The setup script offers to download the Decky CLI into the ignored `cli/` directory. When it completes, build the package as described in [Create the Decky ZIP](#create-the-decky-zip).

### Distrobox

Distrobox is useful when your host is immutable or does not have the build tools installed. The repository should be in a directory shared with the container, such as your home directory. For example, create an Arch build container and enter it:

```bash
distrobox create --name yasp-build --image archlinux:latest
distrobox enter yasp-build
```

Inside the container, install the same Arch packages listed above. The Decky CLI needs a Docker-compatible engine. If the host is Bazzite or another Podman-based system, enable its rootless Podman API socket from inside the box and point the Docker CLI at it:

```bash
distrobox-host-exec systemctl --user enable --now podman.socket
export DOCKER_HOST="unix://${XDG_RUNTIME_DIR}/podman/podman.sock"
docker info
```

Continue with the pnpm setup, tests, frontend build, CLI setup, and ZIP build from the repository root. If `docker info` cannot reach the server, fix the container-engine connection before running the Decky CLI.

### Immutable/atomic Linux

Avoid installing the build toolchain into the read-only host image. Use an Arch Distrobox as above, with the project in a shared home directory and the host's Podman socket exposed to the box. Build and package inside the box; install and run the resulting plugin on the host where Decky Loader and Steam Game Mode are running. Distrobox is only the build environment; it does not provide the plugin's display or audio capture session.

### Create the Decky ZIP

Y.A.S.P.'s Python backend is the root-level `main.py`; the Decky CLI includes it directly and does not need a separate backend build. Build the ZIP from the repository root:

```bash
sudo -E ./cli/decky plugin build "$PWD"
```

The repository's `.vscode/build.sh` invokes the CLI with `sudo -E` as well. If your Docker-compatible engine is already accessible without elevated privileges and the build does not hit file-permission errors, you can omit `sudo -E`. After a successful build, look in `out/` for the plugin ZIP. Check that the archive contains the plugin directory with `dist/index.js`, `main.py`, `plugin.json`, `package.json`, `README.md`, and `LICENSE`. Give the downloaded release asset a versioned name (for example `YASP-v0.2.0.zip`) when attaching it to a GitHub Release; keep the ZIP's internal plugin layout unchanged.

If the frontend build in the CLI container fails while removing `node_modules/.pnpm` with a permission error, rerun the ZIP build with `sudo -E` as above. If the Podman Docker API reports buildx/buildkit errors, verify `docker info` and the `DOCKER_HOST` socket first; the CLI requires a working Docker-compatible build engine.

## Install a release ZIP through Decky

1. Download the versioned `.zip` asset from the project's GitHub Release to the Steam Deck's local storage (for example, `Downloads`). Do not extract it.
2. Open Decky Loader in Game Mode and open its **Settings** (gear) page.
3. Open **Developer**, then under **Third-Party Plugins** choose **Install Plugin from ZIP File** and select **Browse**.
4. Choose the downloaded ZIP in the file picker and accept Decky's third-party plugin warning to install it.
5. Return to the Quick Access Menu and open Y.A.S.P. On first use, use its QAM controls to install the portable MediaMTX host and, if needed, portable FFmpeg.

The ZIP installation control is intended for local testing and third-party release distribution; it does not publish the plugin to Decky's store. The plugin requires an X11 Game Mode session for capture, plus host `xdpyinfo`, `pactl`, and a PulseAudio/PipeWire monitor as described above.

## Development checks and licenses

Run `pnpm test` (or `python3 -m unittest discover -s tests -v`) for backend tests and `pnpm run build` for the frontend. The optional FFmpeg binary is GPLv3; its license files are retained under the plugin's `licenses/` directory, and the corresponding upstream source/build project is [FFmpeg-Builds](https://github.com/FFmpeg-Builds/FFmpeg-Builds).
