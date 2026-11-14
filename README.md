# YASP

Yet Another Sunshine Plugin.

This Decky plugin starts a lightweight Sunshine host from Steam Game Mode so Moonlight clients can stream in-game or desktop sessions.

Features
- Quick Access Menu controls to start and stop Sunshine
- Download a portable Sunshine Linux AppImage into the plugin directory
- Show the LAN IP address while the host is running for manual Moonlight connection
- Save resolution, bitrate, quality preset, and display rotation settings
- Supports Steam Deck/Steam Game Mode Linux systems, including immutable distributions when their GPU and capture stack is available

Requirements
- A Linux system running Steam Game Mode with Sunshine-compatible GPU capture and encoder support
- A Moonlight client on another device
- Optional: a native `sunshine` installation if you do not use the plugin bundle

Build
```sh
pnpm install
pnpm run build
```

Notes
- The plugin stores its runtime config in Decky's plugin runtime directory.
- Rotation support depends on `wlr-randr` or `xrandr` being available.
- The plugin downloads a matching Sunshine AppImage from the upstream project releases and stores it under `bin/` inside the plugin directory.
