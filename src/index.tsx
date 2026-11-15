import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  staticClasses,
} from "@decky/ui";
import { callable, definePlugin } from "@decky/api";
import { useCallback, useEffect, useState, type CSSProperties } from "react";
import { FaDesktop } from "react-icons/fa";

type Quality = "low" | "balanced" | "high";
type Destination = "none" | "twitch" | "youtube" | "custom_rtmp" | "custom_rtsp";

type Settings = {
  resolution: string;
  video_bitrate: number;
  audio_bitrate: number;
  quality: Quality;
  rotation: 0 | 90 | 180 | 270;
  destination: Destination;
};

type Status = {
  running: boolean;
  active: boolean;
  host_available: boolean;
  host: string | null;
  ffmpeg_available: boolean;
  lan_ip: string | null;
  webrtc_url: string | null;
  destination_configured: boolean;
  stream_error: string | null;
  settings: Settings;
};

const getStatus = callable<[], Status>("get_status");
const saveSettings = callable<[settings: Settings & { custom_url?: string; stream_key?: string }], Status>(
  "save_settings",
);
const startHost = callable<[], Status>("start_host");
const stopHost = callable<[], Status>("stop_host");
const downloadHost = callable<[], { installed: boolean; host: string; version: string }>("download_host");

const initialSettings: Settings = {
  resolution: "1920x1080",
  video_bitrate: 12000,
  audio_bitrate: 192,
  quality: "balanced",
  rotation: 0,
  destination: "none",
};

function Content() {
  const [status, setStatus] = useState<Status | null>(null);
  const [settings, setSettings] = useState<Settings>(initialSettings);
  const [customUrl, setCustomUrl] = useState("");
  const [streamKey, setStreamKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const refresh = useCallback(async (syncSettings = false) => {
    try {
      const current = await getStatus();
      setStatus(current);
      if (syncSettings) setSettings(current.settings);
    } catch (error) {
      setMessage(String(error));
    }
  }, []);

  useEffect(() => {
    void refresh(true);
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const run = async (operation: () => Promise<unknown>, success: string) => {
    setBusy(true);
    setMessage("");
    try {
      await operation();
      await refresh();
      setMessage(success);
    } catch (error) {
      setMessage(String(error));
    } finally {
      setBusy(false);
    }
  };

  const update = <K extends keyof Settings>(key: K, value: Settings[K]) => {
    setSettings((current) => ({ ...current, [key]: value }));
  };

  const save = () => {
    const payload: Settings & { custom_url?: string; stream_key?: string } = { ...settings };
    if (customUrl.trim()) payload.custom_url = customUrl.trim();
    if (streamKey.trim()) payload.stream_key = streamKey.trim();
    return run(async () => {
      const current = await saveSettings(payload);
      setSettings(current.settings);
      return current;
    }, "Settings saved");
  };

  const fieldStyle: CSSProperties = {
    width: "100%",
    boxSizing: "border-box",
    marginTop: 6,
    padding: "8px 10px",
    color: "white",
    background: "#242832",
    border: "1px solid #59616f",
    borderRadius: 4,
  };
  const labelStyle: CSSProperties = { display: "block", marginBottom: 12 };
  const disabled = busy || status?.active === true;
  const needsStreamKey = settings.destination === "twitch" || settings.destination === "youtube";
  const needsCustomUrl = settings.destination === "custom_rtmp" || settings.destination === "custom_rtsp";

  return (
    <PanelSection title="YASP · Live Streaming">
      <PanelSectionRow>
        <div style={{ width: "100%" }}>
          <div style={{ marginBottom: 10 }}>
            <div>
              Status: {status?.running ? "Streaming" : status?.active ? "Degraded (one process is still running)" : "Stopped"}
            </div>
            {status?.host ? (
              <div style={{ opacity: 0.7, overflowWrap: "anywhere" }}>MediaMTX: {status.host}</div>
            ) : null}
            {status?.webrtc_url ? (
              <div style={{ overflowWrap: "anywhere" }}>
                LAN WebRTC: <a href={status.webrtc_url}>{status.webrtc_url}</a>
              </div>
            ) : null}
            {status?.stream_error ? (
              <div style={{ marginTop: 6, color: "#ff8080", whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
                Stream warning: {status.stream_error}
              </div>
            ) : null}
            {!status?.ffmpeg_available ? (
              <div style={{ marginTop: 6 }}>FFmpeg is required in the user's environment to capture and encode.</div>
            ) : null}
            {status?.running && settings.destination !== "none" ? (
              <div style={{ marginTop: 6 }}>
                External publishing is best-effort; MediaMTX/WebRTC remains available if the destination disconnects.
              </div>
            ) : null}
          </div>

          <ButtonItem
            layout="below"
            disabled={
              busy ||
              (!status?.active && (!status?.host_available || !status?.ffmpeg_available))
            }
            onClick={() => void run(status?.active ? stopHost : startHost, status?.active ? "Streaming stopped" : "Streaming started")}
          >
            {status?.active ? "Stop streaming" : "Start streaming"}
          </ButtonItem>

          {!status?.host_available ? (
            <ButtonItem
              layout="below"
              disabled={busy}
              onClick={() => void run(downloadHost, "MediaMTX installed in the plugin directory")}
            >
              Download portable MediaMTX host
            </ButtonItem>
          ) : null}
        </div>
      </PanelSectionRow>

      <PanelSectionRow>
        <div style={{ width: "100%" }}>
          <label style={labelStyle}>
            Streaming destination
            <select
              style={fieldStyle}
              value={settings.destination}
              disabled={disabled}
              onChange={(event) => update("destination", event.currentTarget.value as Destination)}
            >
              <option value="none">LAN WebRTC only</option>
              <option value="twitch">Twitch</option>
              <option value="youtube">YouTube</option>
              <option value="custom_rtmp">Custom RTMP / RTMPS</option>
              <option value="custom_rtsp">Custom RTSP / RTSPS</option>
            </select>
          </label>

          {needsStreamKey ? (
            <label style={labelStyle}>
              {settings.destination === "twitch" ? "Twitch" : "YouTube"} stream key
              <input
                style={fieldStyle}
                type="password"
                autoComplete="new-password"
                value={streamKey}
                disabled={disabled}
                placeholder={status?.destination_configured ? "Saved; leave blank to keep it" : "Required"}
                onChange={(event) => setStreamKey(event.currentTarget.value)}
              />
            </label>
          ) : null}

          {needsCustomUrl ? (
            <label style={labelStyle}>
              Custom publish URL
              <input
                style={fieldStyle}
                type="password"
                autoComplete="new-password"
                value={customUrl}
                disabled={disabled}
                placeholder={status?.destination_configured ? "Saved; leave blank to keep it" : "rtmp(s):// or rtsp(s)://"}
                onChange={(event) => setCustomUrl(event.currentTarget.value)}
              />
            </label>
          ) : null}

          <label style={labelStyle}>
            Quality preset
            <select
              style={fieldStyle}
              value={settings.quality}
              disabled={disabled}
              onChange={(event) => update("quality", event.currentTarget.value as Quality)}
            >
              <option value="low">Low latency (ultrafast)</option>
              <option value="balanced">Balanced (veryfast)</option>
              <option value="high">High quality (faster)</option>
            </select>
          </label>

          <label style={labelStyle}>
            Output resolution (swapped for 90°/270° rotation)
            <select
              style={fieldStyle}
              value={settings.resolution}
              disabled={disabled}
              onChange={(event) => update("resolution", event.currentTarget.value)}
            >
              {["1280x720", "1920x1080", "2560x1440", "3840x2160"].map((resolution) => (
                <option key={resolution} value={resolution}>{resolution}</option>
              ))}
            </select>
          </label>

          <label style={labelStyle}>
            Video bitrate (kbps)
            <input
              style={fieldStyle}
              type="number"
              min={1000}
              max={150000}
              step={1000}
              value={settings.video_bitrate}
              disabled={disabled}
              onChange={(event) => update("video_bitrate", Number(event.currentTarget.value))}
            />
          </label>

          <label style={labelStyle}>
            Audio bitrate (kbps)
            <input
              style={fieldStyle}
              type="number"
              min={32}
              max={512}
              step={32}
              value={settings.audio_bitrate}
              disabled={disabled}
              onChange={(event) => update("audio_bitrate", Number(event.currentTarget.value))}
            />
          </label>

          <label style={labelStyle}>
            Stream rotation
            <select
              style={fieldStyle}
              value={settings.rotation}
              disabled={disabled}
              onChange={(event) => update("rotation", Number(event.currentTarget.value) as Settings["rotation"])}
            >
              <option value={0}>Landscape (0°)</option>
              <option value={90}>Rotate 90° clockwise</option>
              <option value={180}>Rotate 180°</option>
              <option value={270}>Rotate 90° counter-clockwise</option>
            </select>
          </label>

          <ButtonItem layout="below" disabled={disabled} onClick={() => void save()}>
            Save streaming settings
          </ButtonItem>

          {message ? <div style={{ marginTop: 10, overflowWrap: "anywhere" }}>{message}</div> : null}
        </div>
      </PanelSectionRow>

      <PanelSectionRow>
        <div style={{ width: "100%", opacity: 0.8, fontSize: "0.9em" }}>
          Full display capture currently requires an X11 session, x11grab-enabled FFmpeg, xdpyinfo, and a
          PulseAudio/PipeWire monitor. Wayland portal capture is not implemented; startup reports this instead
          of silently streaming a partial or blank screen.
        </div>
      </PanelSectionRow>
    </PanelSection>
  );
}

export default definePlugin(() => ({
  name: "YASP Live",
  titleView: <div className={staticClasses.Title}>YASP · Live Streaming</div>,
  content: <Content />,
  icon: <FaDesktop />,
}));
