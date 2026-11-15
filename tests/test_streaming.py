import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


sys.modules.setdefault("decky", types.SimpleNamespace())
import main


def create_plugin(root: Path) -> main.Plugin:
    fake_decky = types.SimpleNamespace(
        DECKY_PLUGIN_DIR=str(root / "plugin"),
        DECKY_PLUGIN_SETTINGS_DIR=str(root / "settings"),
        DECKY_PLUGIN_RUNTIME_DIR=str(root / "runtime"),
        DECKY_PLUGIN_LOG_DIR=str(root / "logs"),
    )
    with patch.object(main, "decky", fake_decky):
        plugin = main.Plugin()
    return plugin


class SettingsTests(unittest.TestCase):
    def test_settings_are_bounded_and_invalid_choices_fall_back(self) -> None:
        settings = main.Plugin._validate_settings({
            "resolution": "800x600",
            "video_bitrate": 500000,
            "audio_bitrate": 1,
            "quality": "not-a-preset",
            "rotation": 45,
            "destination": "unknown",
        })

        self.assertEqual(settings["resolution"], "1920x1080")
        self.assertEqual(settings["video_bitrate"], 150000)
        self.assertEqual(settings["audio_bitrate"], 32)
        self.assertEqual(settings["quality"], "balanced")
        self.assertEqual(settings["rotation"], 0)
        self.assertEqual(settings["destination"], "none")

    def test_all_requested_resolutions_are_supported(self) -> None:
        self.assertEqual(
            main.Plugin.RESOLUTIONS,
            {
                "1280x720",
                "1600x900",
                "1920x1080",
                "1280x800",
                "1440x900",
                "1920x1200",
            },
        )

    def test_existing_runtime_settings_are_loaded_after_settings_path_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            runtime.mkdir()
            (runtime / "settings.json").write_text('{"video_bitrate": 9000}', encoding="utf-8")
            plugin = main.Plugin.__new__(main.Plugin)
            plugin.settings_path = root / "settings" / "settings.json"
            plugin.runtime_dir = runtime

            settings = plugin._load_settings()

        self.assertEqual(settings["video_bitrate"], 9000)

    def test_status_never_returns_destination_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plugin = create_plugin(Path(temporary))
            plugin.settings.update({
                "destination": "custom_rtmp",
                "custom_url": "rtmp://private.example/live/secret",
                "stream_key": "private-key",
            })

            status = plugin._status()

            self.assertNotIn("custom_url", status["settings"])
            self.assertNotIn("stream_key", status["settings"])
            self.assertTrue(status["destination_configured"])

    def test_stream_logs_redact_publisher_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plugin = create_plugin(Path(temporary))
            plugin.settings.update({"custom_url": "rtmp://private/live/private-key"})
            plugin.log_dir.mkdir(parents=True)
            plugin.log_path = plugin.log_dir / "streaming.log"
            plugin.log_handle = plugin.log_path.open("wb")
            process = subprocess.Popen(
                [sys.executable, "-c", "print('private-key rtmp://private/live/private-key')"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            plugin._attach_log_reader(process)
            process.wait(timeout=5)
            plugin.log_threads[0].join(timeout=5)
            plugin.log_handle.close()

            log_text = plugin.log_path.read_text(encoding="utf-8")

        self.assertNotIn("private-key", log_text)
        self.assertIn("[redacted]", log_text)

    def test_stream_logs_redact_mediamtx_style_rtmp_fragment_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plugin = create_plugin(Path(temporary))
            plugin.settings.update({"custom_url": "rtmp://private/live#private-key"})

            redacted = plugin._redact_log_text("publishing to rtmp://private/live#private-key")

        self.assertNotIn("private-key", redacted)
        self.assertIn("[redacted]", redacted)


class StreamingCommandTests(unittest.TestCase):
    def _plugin(self) -> main.Plugin:
        plugin = main.Plugin.__new__(main.Plugin)
        plugin.settings = main.Plugin._defaults()
        return plugin

    def test_twitch_requires_a_key_and_uses_mediamtx_destination_syntax(self) -> None:
        plugin = self._plugin()
        plugin.settings["destination"] = "twitch"
        with self.assertRaisesRegex(ValueError, "stream key"):
            plugin._destination_url()

        plugin.settings["stream_key"] = "abc123"
        self.assertEqual(
            plugin._destination_url(),
            ("rtmp://live.twitch.tv/app#abc123", "rtmp"),
        )

    def test_restream_uses_its_ingest_url_and_key(self) -> None:
        plugin = self._plugin()
        plugin.settings.update({"destination": "restream", "stream_key": "restream-key"})
        self.assertEqual(
            plugin._destination_url(),
            ("rtmp://live.restream.io/live#restream-key", "rtmp"),
        )

    def test_custom_destinations_require_matching_protocols(self) -> None:
        plugin = self._plugin()
        plugin.settings.update({"destination": "custom_rtsp", "custom_url": "rtmp://host/live"})
        with self.assertRaisesRegex(ValueError, "RTSP"):
            plugin._destination_url()

        plugin.settings["custom_url"] = "rtsps://host/live"
        self.assertEqual(plugin._destination_url(), ("rtsps://host/live", "rtsp"))

    def test_custom_rtmp_path_key_is_normalized_for_mediamtx(self) -> None:
        plugin = self._plugin()
        plugin.settings.update({"destination": "custom_rtmp", "custom_url": "rtmps://host/app/key"})
        self.assertEqual(plugin._destination_url(), ("rtmps://host/app#key", "rtmp"))

    def test_ffmpeg_always_publishes_to_mediamtx_and_adds_rtmp_audio(self) -> None:
        plugin = self._plugin()
        plugin.settings.update({"destination": "custom_rtmp", "custom_url": "rtmp://host/live/key"})
        with patch.object(plugin, "_default_sink", return_value="sink"):
            command = plugin._build_stream_command("/usr/bin/ffmpeg", ":0", "2560x1600")

        self.assertEqual(command[-1], "rtsp://127.0.0.1:8554/yasp")
        self.assertEqual(command[command.index("-f", command.index("-ac")) + 1], "rtsp")
        self.assertIn("aac", command)
        self.assertIn("libopus", command)

    def test_local_only_feed_uses_opus(self) -> None:
        plugin = self._plugin()
        with patch.object(plugin, "_default_sink", return_value="sink"):
            command = plugin._build_stream_command("/usr/bin/ffmpeg", ":0", "1920x1080")

        self.assertIn("libopus", command)
        self.assertNotIn("aac", command)

    def test_portrait_rotation_swaps_encoded_dimensions(self) -> None:
        plugin = self._plugin()
        plugin.settings.update({"resolution": "1920x1080", "rotation": 90})
        with patch.object(plugin, "_default_sink", return_value="sink"):
            command = plugin._build_stream_command("/usr/bin/ffmpeg", ":0", "1600x2560")

        self.assertTrue(any("scale=1080:1920:flags=fast_bilinear" in item for item in command))

    def test_wayland_capture_fails_explicitly_before_startup(self) -> None:
        plugin = self._plugin()
        with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}):
            with self.assertRaisesRegex(RuntimeError, "Wayland display capture is not available"):
                plugin._capture_preflight()


class MediaMTXTests(unittest.TestCase):
    def test_media_config_binds_web_rtc_to_the_private_lan_address(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plugin = create_plugin(Path(temporary))
            plugin._write_mediamtx_config("192.168.1.20")
            config = plugin.config_path.read_text(encoding="utf-8")

        self.assertIn("rtspAddress: 127.0.0.1:8554", config)
        self.assertIn("webrtcAddress: 192.168.1.20:8889", config)
        self.assertIn("webrtcLocalUDPAddress: 192.168.1.20:8189", config)
        self.assertIn("source: publisher", config)

    def test_external_destination_is_forwarded_by_mediamtx(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plugin = create_plugin(Path(temporary))
            plugin._write_mediamtx_config(
                "192.168.1.20", ("rtmp://live.restream.io/live#secret", "rtmp")
            )
            config = plugin.config_path.read_text(encoding="utf-8")
            mode = plugin.config_path.stat().st_mode & 0o777

        self.assertIn('      - dest: "rtmp://live.restream.io/live#secret"', config)
        self.assertEqual(mode, 0o600)

    def test_supported_release_architectures_are_explicit(self) -> None:
        matches = main.Plugin._architecture_matches
        self.assertTrue(matches("mediamtx_linux_amd64.tar.gz", "x86_64"))
        self.assertTrue(matches("mediamtx_linux_arm64.tar.gz", "aarch64"))
        self.assertFalse(matches("mediamtx_linux_armv7.tar.gz", "armv7l"))


class FFmpegTests(unittest.TestCase):
    @staticmethod
    def _asset(name: str, archive: bytes) -> dict[str, object]:
        return {
            "name": name,
            "size": len(archive),
            "digest": f"sha256:{hashlib.sha256(archive).hexdigest()}",
            "browser_download_url": f"https://github.com/FFmpeg-Builds/FFmpeg-Builds/releases/download/latest/{name}",
        }

    @staticmethod
    def _archive() -> bytes:
        contents = io.BytesIO()
        with tarfile.open(fileobj=contents, mode="w:xz") as archive:
            for name, data, mode in (
                ("ffmpeg-n9.0/bin/ffmpeg", b"test ffmpeg", 0o755),
                (
                    "ffmpeg-n9.0/LICENSE.txt",
                    b"GNU GENERAL PUBLIC LICENSE\nVersion 3\n",
                    0o644,
                ),
            ):
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                entry.mode = mode
                archive.addfile(entry, io.BytesIO(data))
        return contents.getvalue()

    def test_release_selection_uses_supported_architecture_and_verified_assets(self) -> None:
        release = {
            "assets": [
                self._asset("ffmpeg-n8.1-latest-linux64-gpl-8.1.tar.xz", b"old"),
                self._asset("ffmpeg-n9.0-latest-linux64-gpl-9.0.tar.xz", b"new"),
                self._asset("ffmpeg-n9.0-latest-linuxarm64-gpl-9.0.tar.xz", b"arm"),
            ]
        }

        selected = main.Plugin._ffmpeg_release_asset(release, "x86_64")

        self.assertIsNotNone(selected)
        self.assertEqual(selected[1], "9.0")
        self.assertEqual(selected[0]["name"], "ffmpeg-n9.0-latest-linux64-gpl-9.0.tar.xz")
        self.assertIsNone(main.Plugin._ffmpeg_release_asset(release, "armv7l"))

    def test_portable_ffmpeg_download_verifies_digest_and_keeps_license(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plugin = create_plugin(Path(temporary))
            archive = self._archive()
            name = "ffmpeg-n9.0-latest-linux64-gpl-9.0.tar.xz"
            release = {"assets": [self._asset(name, archive)]}
            responses = [io.BytesIO(json.dumps(release).encode()), io.BytesIO(archive)]

            with patch.object(main.platform, "machine", return_value="x86_64"), patch.object(
                main.urllib.request, "urlopen", side_effect=responses
            ):
                result = plugin._download_ffmpeg()

            installed = Path(plugin.bin_dir) / "ffmpeg"
            license_file = Path(plugin.plugin_dir) / "licenses" / "ffmpeg-9.0" / "LICENSE.txt"
            self.assertTrue(installed.is_file())
            self.assertTrue(os.access(installed, os.X_OK))
            self.assertEqual(installed.read_bytes(), b"test ffmpeg")
            self.assertEqual(license_file.read_bytes(), b"GNU GENERAL PUBLIC LICENSE\nVersion 3\n")
            self.assertEqual(result["version"], "9.0")

    def test_portable_ffmpeg_download_rejects_a_digest_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plugin = create_plugin(Path(temporary))
            archive = self._archive()
            name = "ffmpeg-n9.0-latest-linux64-gpl-9.0.tar.xz"
            asset = self._asset(name, archive)
            asset["digest"] = f"sha256:{'0' * 64}"
            release = {"assets": [asset]}
            responses = [io.BytesIO(json.dumps(release).encode()), io.BytesIO(archive)]

            with patch.object(main.platform, "machine", return_value="x86_64"), patch.object(
                main.urllib.request, "urlopen", side_effect=responses
            ):
                with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                    plugin._download_ffmpeg()

            self.assertFalse((Path(plugin.bin_dir) / "ffmpeg").exists())


if __name__ == "__main__":
    unittest.main()
