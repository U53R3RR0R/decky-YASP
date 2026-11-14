import os
import json
import re
import shutil
import socket
import subprocess
import tempfile
import urllib.error
import urllib.request
import ipaddress
import asyncio
import platform
from pathlib import Path
from typing import Any

import decky


class Plugin:
    def __init__(self) -> None:
        self.plugin_dir = Path(decky.DECKY_PLUGIN_DIR)
        self.runtime_dir = Path(decky.DECKY_PLUGIN_RUNTIME_DIR)
        self.bin_dir = self.plugin_dir / "bin"
        self.config_dir = self.runtime_dir / "sunshine-config"
        self.settings_path = self.runtime_dir / "settings.json"
        self.process: subprocess.Popen[bytes] | None = None
        self.log_handle: Any = None
        self.original_rotation: tuple[str, str, str] | None = None
        self.settings = self._load_settings()

    @staticmethod
    def _defaults() -> dict[str, Any]:
        return {
            "resolution": "1920x1080",
            "video_bitrate": 20000,
            "audio_bitrate": 192,
            "quality": "balanced",
            "rotation": 0,
        }

    def _load_settings(self) -> dict[str, Any]:
        settings = self._defaults()
        try:
            with self.settings_path.open(encoding="utf-8") as settings_file:
                loaded = json.load(settings_file)
            if isinstance(loaded, dict):
                settings.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass
        return self._validate_settings(settings)

    @staticmethod
    def _validate_settings(settings: dict[str, Any]) -> dict[str, Any]:
        defaults = Plugin._defaults()
        resolution = settings.get("resolution")
        if not isinstance(resolution, str) or not re.fullmatch(r"\d{3,5}x\d{3,5}", resolution):
            resolution = defaults["resolution"]

        quality = settings.get("quality")
        if quality not in ("low", "balanced", "high", "custom"):
            quality = defaults["quality"]

        try:
            video_bitrate = int(settings.get("video_bitrate", defaults["video_bitrate"]))
        except (TypeError, ValueError):
            video_bitrate = defaults["video_bitrate"]

        try:
            audio_bitrate = int(settings.get("audio_bitrate", defaults["audio_bitrate"]))
        except (TypeError, ValueError):
            audio_bitrate = defaults["audio_bitrate"]

        try:
            rotation = int(settings.get("rotation", defaults["rotation"]))
        except (TypeError, ValueError):
            rotation = defaults["rotation"]

        return {
            "resolution": resolution,
            "video_bitrate": min(max(video_bitrate, 1000), 150000),
            "audio_bitrate": min(max(audio_bitrate, 32), 512),
            "quality": quality,
            "rotation": rotation if rotation in (0, 90, 180, 270) else 0,
        }

    def _save_settings(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.settings_path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(self.settings, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(self.settings_path)

    def _host_path(self) -> str | None:
        for candidate in (self.bin_dir / "sunshine.AppImage", self.bin_dir / "sunshine"):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        return shutil.which("sunshine")

    @staticmethod
    def _arch_matches(asset_name: str, machine: str) -> bool:
        name = asset_name.lower()
        if machine in ("x86_64", "amd64"):
            return "x86_64" in name or "amd64" in name
        if machine in ("aarch64", "arm64"):
            return "aarch64" in name or "arm64" in name
        return False

    def _download_host(self) -> dict[str, Any]:
        request = urllib.request.Request(
            "https://api.github.com/repos/LizardByte/Sunshine/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "YASP-Decky-Plugin"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                release = json.load(response)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Could not check Sunshine releases: {error}") from error

        machine = platform.machine().lower()
        asset = next(
            (
                candidate
                for candidate in release.get("assets", [])
                if candidate.get("name", "").lower().endswith(".appimage")
                and self._arch_matches(candidate.get("name", ""), machine)
                and candidate.get("size", 0) <= 600 * 1024 * 1024
                and candidate.get("browser_download_url", "").startswith("https://github.com/")
            ),
            None,
        )

        if asset is None:
            raise RuntimeError(f"No matching Sunshine AppImage is published for {machine}.")

        self.bin_dir.mkdir(parents=True, exist_ok=True)
        destination = self.bin_dir / "sunshine.AppImage"
        fd, temp_name = tempfile.mkstemp(prefix="sunshine-", dir=self.bin_dir)
        try:
            download_request = urllib.request.Request(
                asset["browser_download_url"], headers={"User-Agent": "YASP-Decky-Plugin"}
            )
            with os.fdopen(fd, "wb") as downloaded, urllib.request.urlopen(download_request, timeout=60) as response:
                shutil.copyfileobj(response, downloaded)
            os.chmod(temp_name, 0o755)
            os.replace(temp_name, destination)
        except (OSError, urllib.error.URLError) as error:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise RuntimeError(f"Could not download Sunshine: {error}") from error

        return {"installed": True, "host": destination.name, "version": release.get("tag_name", "latest")}

    async def download_host(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._download_host)

    @staticmethod
    def _lan_ip() -> str | None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("1.1.1.1", 80))
            address = sock.getsockname()[0]
            return address if ipaddress.ip_address(address).is_private else None
        except OSError:
            return None
        finally:
            sock.close()

    def _write_sunshine_config(self) -> None:
        config_path = self.config_dir / "sunshine" / "sunshine.conf"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            "# Managed by YASP\n"
            f"resolutions = {self.settings['resolution']}\n"
            f"bitrate = {self.settings['video_bitrate']}\n",
            encoding="utf-8",
        )

    def _rotate_display(self) -> None:
        rotation = self.settings["rotation"]
        if rotation == 0:
            return

        wlr_randr = shutil.which("wlr-randr")
        if wlr_randr:
            result = subprocess.run([wlr_randr], check=True, capture_output=True, text=True, timeout=5)
            output = next((line.split()[0] for line in result.stdout.splitlines() if line and not line[0].isspace()), None)
            if output:
                subprocess.run(
                    [wlr_randr, "--output", output, "--transform", str(rotation)],
                    check=True, capture_output=True, timeout=5,
                )
                self.original_rotation = ("wlr", wlr_randr, output)
                return

        xrandr = shutil.which("xrandr")
        if xrandr:
            result = subprocess.run([xrandr, "--query"], check=True, capture_output=True, text=True, timeout=5)
            output = next(
                (match.group(1) for line in result.stdout.splitlines()
                 if (match := re.match(r"^(\S+) connected(?: primary)?\b", line))),
                None,
            )
            if output:
                transform = {90: "left", 180: "inverted", 270: "right"}[rotation]
                subprocess.run(
                    [xrandr, "--output", output, "--rotate", transform],
                    check=True, capture_output=True, timeout=5,
                )
                self.original_rotation = ("xrandr", xrandr, output)
                return

        raise RuntimeError("Display rotation requires wlr-randr (Wayland) or xrandr (X11).")

    def _restore_display(self) -> None:
        if not self.original_rotation:
            return
        kind, command, output = self.original_rotation
        try:
            if kind == "wlr":
                subprocess.run([command, "--output", output, "--transform", "normal"], check=True, timeout=5)
            else:
                subprocess.run([command, "--output", output, "--rotate", "normal"], check=True, timeout=5)
        except (OSError, subprocess.SubprocessError) as error:
            decky.logger.warning("Could not restore display rotation: %s", error)
        self.original_rotation = None

    def _status(self) -> dict[str, Any]:
        running = self.process is not None and self.process.poll() is None
        if not running:
            self.process = None
        return {
            "running": running,
            "host_available": self._host_path() is not None,
            "host": self._host_path(),
            "lan_ip": self._lan_ip() if running else None,
            "settings": self.settings.copy(),
        }

    async def get_status(self) -> dict[str, Any]:
        return self._status()

    async def save_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(settings, dict):
            raise ValueError("Settings must be an object.")
        self.settings = self._validate_settings(settings)
        self._save_settings()
        return self._status()

    async def start_host(self) -> dict[str, Any]:
        if self.process is not None and self.process.poll() is None:
            return self._status()

        host = self._host_path()
        if host is None:
            raise RuntimeError("Download the portable Sunshine host or install sunshine on this system first.")

        self._write_sunshine_config()
        self._rotate_display()

        log_dir = Path(decky.DECKY_PLUGIN_LOG_DIR)
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_handle = (log_dir / "sunshine.log").open("ab")

        environment = os.environ.copy()
        environment["XDG_CONFIG_HOME"] = str(self.config_dir)
        if host.endswith(".AppImage"):
            environment["APPIMAGE_EXTRACT_AND_RUN"] = "1"

        try:
            self.process = subprocess.Popen(
                [host], stdout=self.log_handle, stderr=subprocess.STDOUT,
                env=environment, start_new_session=True,
            )
        except OSError:
            self.log_handle.close()
            self.log_handle = None
            self._restore_display()
            raise

        await asyncio.sleep(0.5)
        if self.process.poll() is not None:
            self._restore_display()
            raise RuntimeError("Sunshine exited during startup. Check the plugin sunshine.log.")

        return self._status()

    async def stop_host(self) -> dict[str, Any]:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                await asyncio.to_thread(self.process.wait, 8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                await asyncio.to_thread(self.process.wait)

        self.process = None
        self._restore_display()
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


"""Decky plugin backend for Moonlight/Sunshine hosting."""


async def _noop() -> None:
    return None


# The Decky loader calls Plugin methods directly. Keeping the class above is the important part.
# This placeholder ensures the module remains import-safe and easy to read in editors.
_ = _noop




























































































































