import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  staticClasses,
} from "@decky/ui";
import { callable, definePlugin } from "@decky/api";
import { useCallback, useEffect, useState, type CSSProperties } from "react";
import { FaDesktop } from "react-icons/fa";

type Quality = "low" | "balanced" | "high" | "custom";

type Settings = {
  resolution: string;
  video_bitrate: number;
  audio_bitrate: number;
  quality: Quality;
  rotation: 0 | 90 | 180 | 270;
};

type Status = {
  running: boolean;
  host_available: boolean;
  host: string | null;
  lan_ip: string | null;
  settings: Settings;
};

const getStatus = callable<[], Status>("get_status");
const saveSettings = callable<[settings: Settings], Status>("save_settings");
const startHost = callable<[], Status>("start_host");
const stopHost = callable<[], Status>("stop_host");
const downloadHost = callable<[], { installed: boolean; host: string; version: string }>("download_host");

const initialSettings: Settings = {
  resolution: "1920x1080",
  video_bitrate: 20000,
  audio_bitrate: 192,
  quality: "balanced",
  rotation: 0,
};

function Content() {
  const [status, setStatus] = useState<Status | null>(null);
  const [settings, setSettings] = useState<Settings>(initialSettings);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const refresh = useCallback(async () => {
    try {
      const current = await getStatus();
      setStatus(current);
      setSettings(current.settings);
    } catch (error) {
      setMessage(String(error));
    }
  }, []);

  useEffect(() => {
    void refresh();
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

  return (
    <PanelSection title="Moonlight Streaming">
      <PanelSectionRow>
        <div style={{ width: "100%" }}>
          <div style={{ marginBottom: 10 }}>
            <div>Status: {status?.running ? "Streaming" : "Stopped"}</div>
            {status?.running && status.lan_ip ? <div>Moonlight: {status.lan_ip}</div> : null}
            {status?.host ? <div style={{ opacity: 0.7, overflowWrap: "anywhere" }}>Host: {status.host}</div> : null}
          </div>

          <ButtonItem
            layout="below"
            disabled={busy || !status?.host_available || status?.running}
            onClick={() => void run(startHost, "Sunshine started")}
          >
            Start streaming host
          </ButtonItem>

          <ButtonItem
            layout="below"
            disabled={busy || !status?.running}
            onClick={() => void run(stopHost, "Sunshine stopped")}
          >
            Stop streaming host
          </ButtonItem>

          {!status?.host_available ? (
            <ButtonItem
              layout="below"
              disabled={busy}
              onClick={() => void run(downloadHost, "Portable Sunshine downloaded")}
            >
              Download portable Sunshine host
            </ButtonItem>
          ) : null}
        </div>
      </PanelSectionRow>

      <PanelSectionRow>
        <div style={{ width: "100%" }}>
          <label style={labelStyle}>
            Quality preset
            <select
              style={fieldStyle}
              value={settings.quality}
              onChange={(event) => update("quality", event.currentTarget.value as Quality)}
            >
              <option value="low">Low latency</option>
              <option value="balanced">Balanced</option>
              <option value="high">High quality</option>
              <option value="custom">Custom</option>
            </select>
          </label>

          <label style={labelStyle}>
            Resolution
            <select
              style={fieldStyle}
              value={settings.resolution}
              onChange={(event) => update("resolution", event.currentTarget.value)}
            >
              {[
                "1280x720",
                "1920x1080",
                "2560x1440",
                "3840x2160",
              ].map((resolution) => (
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
              onChange={(event) => update("audio_bitrate", Number(event.currentTarget.value))}
            />
          </label>

          <label style={labelStyle}>
            Display rotation
            <select
              style={fieldStyle}
              value={settings.rotation}
              onChange={(event) => update("rotation", Number(event.currentTarget.value) as Settings["rotation"])}
            >
              <option value={0}>Landscape (0°)</option>
              <option value={90}>90°</option>
              <option value={180}>180°</option>
              <option value={270}>270°</option>
            </select>
          </label>

          <ButtonItem
            layout="below"
            disabled={busy}
            onClick={() => void run(() => saveSettings(settings), "Settings saved")}
          >
            Save streaming settings
          </ButtonItem>

          {message ? <div style={{ marginTop: 10, overflowWrap: "anywhere" }}>{message}</div> : null}
        </div>
      </PanelSectionRow>
    </PanelSection>
  );
}

export default definePlugin(() => ({
  name: "YASP Moonlight",
  titleView: <div className={staticClasses.Title}>YASP · Moonlight Host</div>,
  content: <Content />,
  icon: <FaDesktop />,
}));
