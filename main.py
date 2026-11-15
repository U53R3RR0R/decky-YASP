import asyncio
import ipaddress
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import tarfile
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import decky


class Plugin:
    MEDIA_REPO = "https://api.github.com/repos/bluenviron/mediamtx/releases/latest"
    RESOLUTIONS = {"1280x720", "1920x1080", "2560x1440", "3840x2160"}
    QUALITY_PRESETS = {
        "low": "ultrafast",
        "balanced": "veryfast",
        "high": "faster",
    }

    def __init__(self) -> None:
        self.plugin_dir = Path(decky.DECKY_PLUGIN_DIR)
        self.bin_dir = self.plugin_dir / "bin"
        self.settings_dir = Path(decky.DECKY_PLUGIN_SETTINGS_DIR)
        self.runtime_dir = Path(decky.DECKY_PLUGIN_RUNTIME_DIR)
        self.log_dir = Path(decky.DECKY_PLUGIN_LOG_DIR)
        self.settings_path = self.settings_dir / "settings.json"
        self.config_path = self.runtime_dir / "mediamtx.yml"
        self.media_process: subprocess.Popen[bytes] | None = None
        self.stream_process: subprocess.Popen[bytes] | None = None
        self.log_handle: Any = None
        self.log_lock = threading.Lock()
        self.log_threads: list[threading.Thread] = []
        self.log_path = self.log_dir / "streaming.log"
        self.settings = self._load_settings()

    @staticmethod
    def _defaults() -> dict[str, Any]:
        return {
            "resolution": "1920x1080",
            "video_bitrate": 12000,
            "audio_bitrate": 192,
            "quality": "balanced",
            "rotation": 0,
            "destination": "none",
            "custom_url": "",
            "stream_key": "",
        }

    @classmethod
    def _validate_settings(cls, values: dict[str, Any]) -> dict[str, Any]:
        defaults = cls._defaults()
        resolution = values.get("resolution")
        if not isinstance(resolution, str) or resolution not in cls.RESOLUTIONS:
            resolution = defaults["resolution"]

        quality = values.get("quality")
        if not isinstance(quality, str) or quality not in cls.QUALITY_PRESETS:
            quality = defaults["quality"]

        destination = values.get("destination")
        if destination not in ("none", "twitch", "youtube", "custom_rtmp", "custom_rtsp"):
            destination = "none"

        def bounded_int(name: str, minimum: int, maximum: int) -> int:
            try:
                value = int(values.get(name, defaults[name]))
            except (TypeError, ValueError):
                value = defaults[name]
            return min(max(value, minimum), maximum)

        custom_url = values.get("custom_url", "")
        stream_key = values.get("stream_key", "")
        return {
            "resolution": resolution,
            "video_bitrate": bounded_int("video_bitrate", 1000, 150000),
            "audio_bitrate": bounded_int("audio_bitrate", 32, 512),
            "quality": quality,
            "rotation": values.get("rotation") if values.get("rotation") in (0, 90, 180, 270) else 0,
            "destination": destination,
            "custom_url": custom_url.strip() if isinstance(custom_url, str) else "",
            "stream_key": stream_key.strip() if isinstance(stream_key, str) else "",
        }

    def _load_settings(self) -> dict[str, Any]:
        settings = self._defaults()
        for path in (self.settings_path, self.runtime_dir / "settings.json"):
            try:
                with path.open(encoding="utf-8") as settings_file:
                    loaded = json.load(settings_file)
                if isinstance(loaded, dict):
                    settings.update(loaded)
                    break
            except (OSError, json.JSONDecodeError):
                continue
        return self._validate_settings(settings)

    def _save_settings(self) -> None:
        self.settings_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.settings_path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(self.settings, indent=2) + "\n", encoding="utf-8")
        os.chmod(temp_path, 0o600)
        temp_path.replace(self.settings_path)

    def _host_path(self) -> str | None:
        for candidate in (self.bin_dir / "mediamtx", self.bin_dir / "mediamtx.AppImage"):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        return None

    @staticmethod
    def _architecture_matches(asset_name: str, machine: str) -> bool:
        name = asset_name.lower()
        if machine in ("x86_64", "amd64"):
            return "linux_amd64" in name
        if machine in ("aarch64", "arm64"):
            return "linux_arm64" in name
        return False

    def _download_host(self) -> dict[str, Any]:
        request = urllib.request.Request(
            self.MEDIA_REPO,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "YASP-Decky-Plugin"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                release = json.load(response)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Could not check MediaMTX releases: {error}") from error

        machine = platform.machine().lower()
        asset = next(
            (
                item for item in release.get("assets", [])
                if item.get("name", "").endswith(".tar.gz")
                and self._architecture_matches(item.get("name", ""), machine)
                and 0 < item.get("size", 0) <= 100 * 1024 * 1024
                and item.get("browser_download_url", "").startswith("https://github.com/")
            ),
            None,
        )
        if asset is None:
            raise RuntimeError(f"No supported MediaMTX Linux release was found for {machine}.")

        self.bin_dir.mkdir(parents=True, exist_ok=True)
        destination = self.bin_dir / "mediamtx"
        try:
            with tempfile.TemporaryDirectory(prefix="mediamtx-", dir=self.bin_dir) as temporary_dir:
                archive_path = Path(temporary_dir) / "release.tar.gz"
                download_request = urllib.request.Request(
                    asset["browser_download_url"], headers={"User-Agent": "YASP-Decky-Plugin"}
                )
                downloaded_size = 0
                with urllib.request.urlopen(download_request, timeout=60) as response:
                    with archive_path.open("wb") as downloaded:
                        while chunk := response.read(1024 * 1024):
                            downloaded_size += len(chunk)
                            if downloaded_size > 100 * 1024 * 1024:
                                raise RuntimeError("The MediaMTX release archive exceeded the 100 MiB limit.")
                            downloaded.write(chunk)
                if archive_path.stat().st_size > 100 * 1024 * 1024:
                    raise RuntimeError("The MediaMTX release archive exceeded the 100 MiB limit.")
                with tarfile.open(archive_path, "r:gz") as archive:
                    member = next(
                        (entry for entry in archive.getmembers() if entry.name == "mediamtx" and entry.isfile()),
                        None,
                    )
                    if member is None or member.size > 200 * 1024 * 1024:
                        raise RuntimeError("The MediaMTX release did not contain a valid executable.")
                    executable = archive.extractfile(member)
                    if executable is None:
                        raise RuntimeError("Could not read the MediaMTX executable from its release archive.")
                    temporary_binary = Path(temporary_dir) / "mediamtx"
                    with executable, temporary_binary.open("wb") as output:
                        shutil.copyfileobj(executable, output)
                    os.chmod(temporary_binary, 0o755)
                    os.replace(temporary_binary, destination)
        except (OSError, urllib.error.URLError, tarfile.TarError) as error:
            raise RuntimeError(f"Could not install MediaMTX: {error}") from error

        return {"installed": True, "host": destination.name, "version": release.get("tag_name", "latest")}

    async def download_host(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._download_host)

    @staticmethod
    def _lan_ip() -> str | None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("192.0.2.1", 9))
            address = sock.getsockname()[0]
            parsed = ipaddress.ip_address(address)
            return address if parsed.is_private and not parsed.is_loopback else None
        except OSError:
            return None
        finally:
            sock.close()

    def _capture_preflight(self, destination: tuple[str, str] | None = None) -> tuple[str, str, str]:
        if platform.system() != "Linux":
            raise RuntimeError("Full-display capture is supported only on Linux.")
        if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
            raise RuntimeError(
                "Wayland display capture is not available yet: this plugin does not establish an "
                "xdg-desktop-portal/PipeWire screen-capture session. No stream was started."
            )
        display = os.environ.get("DISPLAY")
        if not display:
            raise RuntimeError("No X11 DISPLAY was provided to the Decky plugin process.")

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("FFmpeg is required for capture and encoding; install it in the user's environment.")
        devices = subprocess.run(
            [ffmpeg, "-hide_banner", "-devices"], capture_output=True, text=True, timeout=10, check=False
        )
        if "x11grab" not in devices.stdout + devices.stderr:
            raise RuntimeError("The installed FFmpeg does not include its x11grab screen-capture input.")
        if "pulse" not in devices.stdout + devices.stderr:
            raise RuntimeError("The installed FFmpeg does not include its PulseAudio system-audio input.")
        encoders = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10, check=False
        )
        available_encoders = encoders.stdout + encoders.stderr
        required_encoders = {"libx264", "libopus"}
        if destination is not None:
            required_encoders.add("aac")
        if any(name not in available_encoders for name in required_encoders):
            raise RuntimeError(f"FFmpeg must include the {', '.join(sorted(required_encoders))} encoders.")
        muxers = subprocess.run(
            [ffmpeg, "-hide_banner", "-muxers"], capture_output=True, text=True, timeout=10, check=False
        )
        available_muxers = muxers.stdout + muxers.stderr
        required_muxers = {"rtsp"}
        if destination is not None:
            required_muxers.update(("tee", destination[1]))
        if any(not re.search(rf"\b{name}\b", available_muxers) for name in required_muxers):
            raise RuntimeError(f"FFmpeg must include the {', '.join(sorted(required_muxers))} muxers.")

        display_info = shutil.which("xdpyinfo")
        if not display_info:
            raise RuntimeError("xdpyinfo is required to determine the complete X11 display dimensions.")
        result = subprocess.run(
            [display_info, "-display", display], capture_output=True, text=True, timeout=10, check=False
        )
        dimensions = re.search(r"dimensions:\s+(\d+x\d+)\s+pixels", result.stdout)
        if result.returncode != 0 or dimensions is None:
            raise RuntimeError("Could not read the active X11 display dimensions with xdpyinfo.")

        pactl = shutil.which("pactl")
        if not pactl:
            raise RuntimeError("pactl is required to capture the system audio monitor on PulseAudio/PipeWire.")
        default_sink = subprocess.run(
            [pactl, "get-default-sink"], capture_output=True, text=True, timeout=10, check=False
        )
        sink = default_sink.stdout.strip()
        if default_sink.returncode != 0 or not sink:
            raise RuntimeError("Could not find a default PulseAudio/PipeWire audio output.")
        sources = subprocess.run(
            [pactl, "list", "sources", "short"], capture_output=True, text=True, timeout=10, check=False
        )
        monitor = f"{sink}.monitor"
        if sources.returncode != 0 or not any(
            line.split() and line.split()[1] == monitor for line in sources.stdout.splitlines()
        ):
            raise RuntimeError(f"The system audio monitor '{monitor}' is unavailable.")
        return ffmpeg, display, dimensions.group(1)

    def _destination_url(self) -> tuple[str, str] | None:
        destination = self.settings["destination"]
        if destination == "none":
            return None
        if destination == "twitch":
            key = self.settings["stream_key"]
            if not key:
                raise ValueError("Enter a Twitch stream key before starting.")
            if any(ord(character) < 32 for character in key):
                raise ValueError("The Twitch stream key contains invalid characters.")
            return f"rtmp://live.twitch.tv/app/{key}", "flv"
        if destination == "youtube":
            key = self.settings["stream_key"]
            if not key:
                raise ValueError("Enter a YouTube stream key before starting.")
            if any(ord(character) < 32 for character in key):
                raise ValueError("The YouTube stream key contains invalid characters.")
            return f"rtmp://a.rtmp.youtube.com/live2/{key}", "flv"

        url = self.settings["custom_url"]
        expected_scheme = "rtmp" if destination == "custom_rtmp" else "rtsp"
        allowed_schemes = (expected_scheme, f"{expected_scheme}s")
        if any(character in url for character in ("\n", "\r", "\0")):
            raise ValueError("The destination URL contains invalid characters.")
        try:
            parsed_url = urlsplit(url)
        except ValueError as error:
            raise ValueError("The custom publish URL is malformed.") from error
        if parsed_url.scheme.lower() not in allowed_schemes or not parsed_url.hostname:
            protocol = "RTMP/RTMPS" if expected_scheme == "rtmp" else "RTSP/RTSPS"
            raise ValueError(f"Enter a valid {protocol} publish URL.")
        return url, "flv" if expected_scheme == "rtmp" else "rtsp"

    @staticmethod
    def _tee_escape(url: str) -> str:
        return url.replace("\\", "\\\\").replace("|", "\\|").replace("[", "\\[").replace("]", "\\]")

    def _write_mediamtx_config(self, lan_ip: str | None) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        address = lan_ip or "127.0.0.1"
        config = (
            "logLevel: warn\n"
            "rtsp: yes\n"
            "rtspAddress: 127.0.0.1:8554\n"
            "webrtc: yes\n"
            f"webrtcAddress: {address}:8889\n"
            f"webrtcLocalUDPAddress: {address}:8189\n"
            "webrtcAdditionalHosts:\n"
            f"  - {json.dumps(address)}\n"
            "paths:\n"
            "  yasp:\n"
            "    source: publisher\n"
        )
        self.config_path.write_text(config, encoding="utf-8")
        os.chmod(self.config_path, 0o600)

    def _build_stream_command(
        self, ffmpeg: str, display: str, dimensions: str, destination: tuple[str, str] | None
    ) -> list[str]:
        width, height = (int(value) for value in dimensions.split("x"))
        output_width, output_height = (int(value) for value in self.settings["resolution"].split("x"))
        rotation_filters = {
            0: [],
            90: ["transpose=clock"],
            180: ["hflip", "vflip"],
            270: ["transpose=cclock"],
        }
        if self.settings["rotation"] in (90, 270):
            output_width, output_height = output_height, output_width
        video_filters = rotation_filters[self.settings["rotation"]] + [
            f"scale={output_width}:{output_height}:flags=fast_bilinear"
        ]
        preset = self.QUALITY_PRESETS[self.settings["quality"]]
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostdin",
            "-thread_queue_size",
            "512",
            "-f",
            "x11grab",
            "-framerate",
            "60",
            "-video_size",
            f"{width}x{height}",
            "-i",
            display,
            "-thread_queue_size",
            "512",
            "-f",
            "pulse",
            "-i",
            f"{self._default_sink()}.monitor",
            "-map",
            "0:v:0",
            "-vf",
            ",".join(video_filters),
            "-r",
            "60",
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-tune",
            "zerolatency",
            "-b:v",
            f"{self.settings['video_bitrate']}k",
            "-maxrate",
            f"{self.settings['video_bitrate']}k",
            "-bufsize",
            f"{self.settings['video_bitrate'] * 2}k",
            "-pix_fmt",
            "yuv420p",
            "-g",
            "120",
        ]

        if destination is None:
            command.extend([
                "-map", "1:a:0",
                "-c:a", "libopus",
                "-b:a", f"{self.settings['audio_bitrate']}k",
                "-ar", "48000",
                "-ac", "2",
                "-f", "rtsp",
                "-rtsp_transport", "tcp",
                "rtsp://127.0.0.1:8554/yasp",
            ])
        else:
            url, container = destination
            command.extend([
                "-map", "1:a:0",
                "-map", "1:a:0",
                "-c:a:0", "aac",
                "-b:a:0", f"{self.settings['audio_bitrate']}k",
                "-ar:a:0", "48000",
                "-ac:a:0", "2",
                "-c:a:1", "libopus",
                "-b:a:1", f"{self.settings['audio_bitrate']}k",
                "-ar:a:1", "48000",
                "-ac:a:1", "2",
            ])
            local = r"[f=rtsp:rtsp_transport=tcp:select=v\\:0,a\\:1]rtsp://127.0.0.1:8554/yasp"
            external = rf"[f={container}:onfail=ignore:select=v\\:0,a\\:0]"
            if container == "rtsp":
                external = r"[f=rtsp:onfail=ignore:rtsp_transport=tcp:select=v\\:0,a\\:0]"
            tee = f"{local}|{external}{self._tee_escape(url)}"
            command.extend(["-f", "tee", tee])
        return command

    def _default_sink(self) -> str:
        pactl = shutil.which("pactl")
        if not pactl:
            raise RuntimeError("pactl is required to capture system audio.")
        result = subprocess.run(
            [pactl, "get-default-sink"], capture_output=True, text=True, timeout=10, check=False
        )
        sink = result.stdout.strip()
        if result.returncode != 0 or not sink:
            raise RuntimeError("Could not find a default PulseAudio/PipeWire audio output.")
        return sink

    def _safe_settings(self) -> dict[str, Any]:
        return {
            key: value for key, value in self.settings.items()
            if key not in ("custom_url", "stream_key")
        }

    def _attach_log_reader(self, process: subprocess.Popen[bytes]) -> None:
        if process.stdout is None:
            raise RuntimeError("Could not capture streaming process logs.")

        def copy_output() -> None:
            for line in process.stdout:
                text = self._redact_log_text(line.decode("utf-8", errors="replace"))
                if self.log_handle is not None:
                    with self.log_lock:
                        self.log_handle.write(text.encode("utf-8", errors="replace"))
                        self.log_handle.flush()
            process.stdout.close()

        reader = threading.Thread(target=copy_output, daemon=True)
        self.log_threads.append(reader)
        reader.start()

    def _redact_log_text(self, text: str) -> str:
        secrets = {self.settings["stream_key"], self.settings["custom_url"]}
        if self.settings["custom_url"]:
            try:
                parsed_url = urlsplit(self.settings["custom_url"])
            except ValueError:
                parsed_url = None
            if parsed_url is not None:
                secrets.update((parsed_url.username or "", parsed_url.password or ""))
                secrets.update(value for _, value in parse_qsl(parsed_url.query, keep_blank_values=False))
                if parsed_url.path:
                    secrets.add(parsed_url.path.rsplit("/", 1)[-1])
        for secret in sorted((value for value in secrets if value), key=len, reverse=True):
            text = text.replace(secret, "[redacted]")
        return text

    def _recent_log_errors(self) -> str | None:
        try:
            text = self.log_path.read_bytes()[-256 * 1024 :].decode("utf-8", errors="replace")
        except OSError:
            return None
        errors = [
            line for line in text.splitlines()
            if any(marker in line.lower() for marker in ("error", "failed", "unable", "could not"))
        ]
        if not errors:
            return None
        return self._redact_log_text("\n".join(errors[-3:]))[-1200:]

    def _status(self) -> dict[str, Any]:
        media_running = self.media_process is not None and self.media_process.poll() is None
        stream_running = self.stream_process is not None and self.stream_process.poll() is None
        if not media_running:
            self.media_process = None
        if not stream_running:
            self.stream_process = None
        running = media_running and stream_running
        active = media_running or stream_running
        ffmpeg_available = shutil.which("ffmpeg") is not None
        lan_ip = self._lan_ip() if running else None
        return {
            "running": running,
            "active": active,
            "host_available": self._host_path() is not None,
            "host": self._host_path(),
            "ffmpeg_available": ffmpeg_available,
            "lan_ip": lan_ip,
            "webrtc_url": f"http://{lan_ip}:8889/yasp" if lan_ip else None,
            "destination_configured": self.settings["destination"] == "none"
            or bool(self.settings["stream_key"] if self.settings["destination"] in ("twitch", "youtube") else self.settings["custom_url"]),
            "stream_error": self._recent_log_errors(),
            "settings": self._safe_settings(),
        }

    async def get_status(self) -> dict[str, Any]:
        return self._status()

    async def save_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(settings, dict):
            raise ValueError("Settings must be an object.")
        if self._status()["active"]:
            raise RuntimeError("Stop streaming before changing settings.")
        updated = dict(self.settings)
        updated.update(settings)
        next_settings = self._validate_settings(updated)
        if next_settings["destination"] != self.settings["destination"]:
            if "stream_key" not in settings:
                next_settings["stream_key"] = ""
            if "custom_url" not in settings:
                next_settings["custom_url"] = ""
        self.settings = next_settings
        self._save_settings()
        return self._status()

    async def start_host(self) -> dict[str, Any]:
        if (
            self.stream_process is not None
            and self.stream_process.poll() is None
            and self.media_process is not None
            and self.media_process.poll() is None
        ):
            return self._status()
        await self.stop_host()

        host = self._host_path()
        if host is None:
            raise RuntimeError("Install the portable MediaMTX host before starting.")
        destination = self._destination_url()
        ffmpeg, display, dimensions = self._capture_preflight(destination)
        lan_ip = self._lan_ip()

        self._write_mediamtx_config(lan_ip)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.log_dir / "streaming.log"
        self.log_handle = self.log_path.open("wb")
        self.log_threads = []
        environment = os.environ.copy()

        try:
            self.media_process = subprocess.Popen(
                [host, str(self.config_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=environment,
                start_new_session=True,
            )
            self._attach_log_reader(self.media_process)
            await asyncio.sleep(0.5)
            if self.media_process.poll() is not None:
                raise RuntimeError("MediaMTX exited during startup; check the plugin streaming.log.")

            command = self._build_stream_command(ffmpeg, display, dimensions, destination)
            self.stream_process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=environment,
                start_new_session=True,
            )
            self._attach_log_reader(self.stream_process)
            await asyncio.sleep(0.75)
            if self.stream_process.poll() is not None:
                raise RuntimeError("FFmpeg exited during startup; check the plugin streaming.log.")
        except (OSError, subprocess.SubprocessError) as error:
            await self.stop_host()
            raise RuntimeError(f"Could not start the streaming processes: {error}") from error
        except RuntimeError:
            await self.stop_host()
            raise

        return self._status()

    async def stop_host(self) -> dict[str, Any]:
        for process in (self.stream_process, self.media_process):
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.to_thread(process.wait, 8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait)
        for reader in self.log_threads:
            reader.join(timeout=2)
        self.log_threads.clear()
        self.stream_process = None
        self.media_process = None
        if self.log_handle is not None:
            self.log_handle.close()
            self.log_handle = None
        return self._status()

    async def _main(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        decky.logger.info("YASP streaming plugin loaded")

    async def _unload(self) -> None:
        await self.stop_host()

    async def _uninstall(self) -> None:
        await self.stop_host()
