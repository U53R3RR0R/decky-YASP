# YASP · Live Streaming

YASP is a Decky Quick Access Menu plugin for capturing a Linux X11 desktop/game display, encoding it with FFmpeg, and publishing it locally and optionally to one external streaming destination.

## What it does

- Starts/stops a stream from the Decky QAM.
- Captures the full X11 display with `x11grab` and system output audio from the PulseAudio/PipeWire monitor.
- Encodes H.264/Opus for WebRTC; when an external destination is selected it also produces an AAC audio track for RTMP/RTSP services. Configurable output resolution, video/audio bitrate, quality preset, and stream-only rotation.
- Publishes the local stream to MediaMTX over RTSP. MediaMTX serves it to LAN WebRTC clients at `http://<LAN-IP>:8889/yasp`.
- Can publish simultaneously to Twitch, YouTube, a custom RTMP/RTMPS URL, or a custom RTSP/RTSPS URL. FFmpeg's tee muxer sends the same encoded stream to MediaMTX and the selected destination; a failed external output is best-effort and does not intentionally stop the local output.
- Downloads the matching Linux MediaMTX release into the plugin's own `bin/` directory. Configuration, settings, and logs use Decky's plugin-owned settings/runtime/log directories; the plugin does not write to OS-managed paths.

## Important platform limits

**Full display capture currently requires an X11 session. Wayland capture is not implemented.** Wayland screen capture requires an xdg-desktop-portal/PipeWire capture session; Decky's callable API does not provide one, and simply running FFmpeg against XWayland would not reliably capture the complete compositor display. On Wayland, startup fails with an explicit explanation rather than reporting a partial capture as successful.

Required runtime dependencies are `ffmpeg` (with `x11grab`, `libx264`, `libopus`, `pulse`, and RTSP muxing), `xdpyinfo`, `pactl`, and an active system-audio monitor source. External destinations also require FFmpeg's tee muxer and AAC encoder; RTMP destinations additionally require the FLV muxer. These must be available to the Decky plugin process in the current user's session. Video uses CPU-based `libx264`; GPU encoders are not selected, so high resolutions may exceed the device's encoding capacity. YASP does not install system packages, change display rotation globally, or assume SteamOS-specific paths. Immutable distributions can use the plugin-owned MediaMTX download, but still need the capture dependencies accessible in their session.

The Decky plugin APIs (`@decky/api` callable backend methods and `@decky/ui` panel components) are the standard plugin extension points used here; this checkout declares `@decky/api` 1.1.3 and `@decky/ui` 4.11.0. The backend runtime is Python and delegates capture/encoding to FFmpeg.

## MediaMTX and simultaneous outputs

MediaMTX can receive one local publisher and make that path available to LAN WebRTC readers at the same time. It is the local relay/WebRTC server here; it does **not itself push directly to Twitch or YouTube**. When an external destination is selected, FFmpeg's tee muxer sends one H.264 video encode to the local MediaMTX RTSP path and the external RTMP(S) or RTSP(S) destination. It creates separate audio tracks: Opus for WebRTC (the WebRTC reader does not support AAC) and AAC for the external output. External publishing is best-effort; local playback remains available if an external tee output fails, subject to the installed FFmpeg build and destination server.

MediaMTX is distributed as a portable Linux executable and can be downloaded into `bin/` rather than installed into the operating system. YASP fetches the latest matching x86_64 or ARM64 Linux release from the upstream MediaMTX GitHub releases at the user's request. The MediaMTX executable is not checked into this repository. FFmpeg is not bundled because capture support, codecs, hardware integration, and licensing vary across Linux distributions; YASP checks for the required capabilities and reports missing dependencies.

WebRTC is unauthenticated and is bound to the detected private LAN address. Anyone who can reach the device on the LAN may be able to watch the stream. Allow TCP port 8889 and UDP port 8189 through the host/network firewall if needed. The RTSP ingest listener is loopback-only. Stream keys and custom publish URLs are stored in the plugin's Decky settings directory with owner-only file permissions and are not returned to the frontend status response. The selected destination URL/key is passed to FFmpeg as a process argument while streaming, so other processes running as the same user (or root) may be able to inspect it.

## Build

```sh
pnpm install
pnpm run build
pnpm test
```

The Python backend tests use the standard library's `unittest` runner and need no extra test package.
