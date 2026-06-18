### This project was vibecoded. It is provided as is. I generated it because I wanted it. I have tested the FlatPak and the native builds on my NixOS distro and given the code a cursory look to ensure it's not doing anything inherently unsafe. Other than that, it does what I need so there won't be any active development. If you'd like to contribute, please open a PR and I'd be happy to take a look (you might have to ping me.) 

# OpenDeck Caffeine

An [OpenDeck](https://github.com/nekename/OpenDeck) plugin that toggles the GNOME
[Caffeine](https://extensions.gnome.org/extension/517/caffeine/) extension from a
key — and keeps the key's icon **in sync with Caffeine's real state**, even when
you toggle it from the GNOME top-bar or after a reboot.

| State | Icon |
|-------|------|
| Caffeine **on** (display/idle inhibited) | steaming cup |
| Caffeine **off** | dimmed cup |

Press the key to toggle. Changes made elsewhere are picked up event-driven via
`dconf watch`, so the icon never drifts out of sync.

## Requirements

- Linux with GNOME and the **Caffeine** GNOME Shell extension (tested with v60).
- OpenDeck (tested with 7.1.0), installed either natively or via Flatpak
  (`me.amankhanna.opendeck`).
- Python 3 — no third-party packages, stdlib only. It must be on the `PATH` of
  the process that launches OpenDeck, since OpenDeck spawns the plugin via its
  `#!/usr/bin/env python3` shebang. The Flatpak runtime already bundles it.

  > **NixOS (native):** there is no global `python3`, so launch OpenDeck from a
  > shell that provides one, e.g.
  > `nix-shell -p python3 --run /path/to/opendeck`.

## How it works

The plugin speaks OpenDeck's Stream-Deck WebSocket protocol (implemented in pure
Python stdlib, no dependencies). It reads and toggles Caffeine through `dconf`:

- read state: `dconf read /org/gnome/shell/extensions/caffeine/user-enabled`
- toggle:     flip `/org/gnome/shell/extensions/caffeine/cli-toggle` (changing
  this key is what triggers Caffeine to toggle)

When OpenDeck runs as a Flatpak, the plugin executes inside the sandbox, so all
`dconf` calls are routed to the host session via `flatpak-spawn --host` (detected
via `/.flatpak-info`); on a native install they run directly.

It pushes a `setState` so the key shows state `0` (on) or state `1` (off) to
match reality — on `willAppear`/`keyDown`, and whenever `dconf watch` reports a
change under the Caffeine path (from the top-bar, CLI, or a reboot).

## Install

### Via the OpenDeck UI (recommended)

1. Open OpenDeck → **Settings → Plugins → Install from file**.
2. Choose `dist/com.fazal.caffeine.streamDeckPlugin`.
3. Fully quit OpenDeck (including the tray icon) and relaunch it so the plugin
   loads.
4. Drag the **Caffeine Toggle** action onto a key.

### Manually

Copy (or symlink) the plugin directory into OpenDeck's plugins folder and restart
OpenDeck:

```sh
# Flatpak install:
cp -r com.fazal.caffeine.sdPlugin \
  ~/.var/app/me.amankhanna.opendeck/config/opendeck/plugins/

# Native install:
cp -r com.fazal.caffeine.sdPlugin ~/.config/opendeck/plugins/
```

> Flatpak note: if you symlink the plugin to a location outside the app's data
> dir, grant the sandbox access to it, e.g.
> `flatpak override --user --filesystem=$PWD me.amankhanna.opendeck`.

## Building

The shipped PNG icons are checked in, so no build step is required. To regenerate
them from the SVG sources and repackage the `.streamDeckPlugin`:

```sh
scripts/build.sh
```

Requires one of `rsvg-convert`, `resvg`, or `inkscape` for rasterization, and
`zip` for packaging.

## Layout

```
com.fazal.caffeine.sdPlugin/   # the plugin (this is what OpenDeck loads)
  manifest.json
  code/caffeine-plugin.py
  icons/
assets/                        # SVG icon sources
dist/                          # packaged .streamDeckPlugin for UI install
scripts/build.sh               # regenerate icons + repackage
```

## License

[PolyForm Noncommercial License 1.0.0](LICENSE) — free for any noncommercial
purpose (personal use, hobby projects, education, nonprofits, etc.). Commercial
use is not permitted.
