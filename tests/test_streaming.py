import os
import subprocess
import sys
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


class StreamingCommandTests(unittest.TestCase):
    def _plugin(self) -> main.Plugin:
        plugin = main.Plugin.__new__(main.Plugin)
        plugin.settings = main.Plugin._defaults()
        return plugin

    def test_twitch_requires_a_key_and_uses_its_ingest_url(self) -> None:
        plugin = self._plugin()
        plugin.settings["destination"] = "twitch"
        with self.assertRaisesRegex(ValueError, "stream key"):
            plugin._destination_url()

        plugin.settings["stream_key"] = "abc123"
        self.assertEqual(
            plugin._destination_url(),
            ("rtmp://live.twitch.tv/app/abc123", "flv"),
        )

    def test_custom_destinations_require_matching_protocols(self) -> None:
        plugin = self._plugin()
        plugin.settings.update({"destination": "custom_rtsp", "custom_url": "rtmp://host/live"})
        with self.assertRaisesRegex(ValueError, "RTSP"):
            plugin._destination_url()

        plugin.settings["custom_url"] = "rtsps://host/live"
        self.assertEqual(plugin._destination_url(), ("rtsps://host/live", "rtsp"))

    def test_tee_command_keeps_local_web_rtc_feed_when_external_is_selected(self) -> None:
        plugin = self._plugin()
        plugin.settings.update({"destination": "custom_rtmp", "custom_url": "rtmp://host/live/key"})
        with patch.object(plugin, "_default_sink", return_value="sink"):
            command = plugin._build_stream_command(
                "/usr/bin/ffmpeg", ":0", "2560x1600", plugin._destination_url()
            )

        self.assertEqual(command[-2], "tee")
        tee_target = command[-1]
        self.assertIn("rtsp://127.0.0.1:8554/yasp", tee_target)
        self.assertIn(r"select=v\\:0,a\\:1", tee_target)
        self.assertIn("onfail=ignore", tee_target)
        self.assertIn(r"select=v\\:0,a\\:0", tee_target)
        self.assertIn("rtmp://host/live/key", tee_target)
        self.assertIn("libopus", command)
        self.assertIn("aac", command)

    def test_portrait_rotation_swaps_encoded_dimensions(self) -> None:
        plugin = self._plugin()
        plugin.settings.update({"resolution": "1920x1080", "rotation": 90})
        with patch.object(plugin, "_default_sink", return_value="sink"):
            command = plugin._build_stream_command("/usr/bin/ffmpeg", ":0", "1600x2560", None)

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

    def test_supported_release_architectures_are_explicit(self) -> None:
        matches = main.Plugin._architecture_matches
        self.assertTrue(matches("mediamtx_linux_amd64.tar.gz", "x86_64"))
        self.assertTrue(matches("mediamtx_linux_arm64.tar.gz", "aarch64"))
        self.assertFalse(matches("mediamtx_linux_armv7.tar.gz", "armv7l"))


if __name__ == "__main__":
    unittest.main()
