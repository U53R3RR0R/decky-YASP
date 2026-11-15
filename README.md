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

## Development

Build the frontend with `pnpm run build`. Run backend tests with `pnpm test` (or `python3 -m unittest discover -s tests -v`). The optional FFmpeg binary is GPLv3; its license files are retained under the plugin's `licenses/` directory, and the corresponding upstream source/build project is [FFmpeg-Builds](https://github.com/FFmpeg-Builds/FFmpeg-Builds). The Decky CLI can package the plugin as an installable ZIP; it may require Docker-compatible container access and elevated permissions for the build container to read dependencies.
