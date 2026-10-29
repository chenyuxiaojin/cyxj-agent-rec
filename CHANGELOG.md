# Changelog

## 0.1.0 — 2026-10-29

First public release.

- Background terminal sessions in a private tmux server: `start`, `type`, `key`, `paste`, `screen`, `wait` (text / regex / gone / idle / `--claude`), `mark`, `stop`, `attach`, `list`, `rm`.
- Export to MP4 with CoreText rendering matched to Terminal.app (SF Mono 11 pt cell rules, macOS 26 window frame), idle compression, spinner speed-up with optional badge, MP4 chapters and a chapter list.
- `shot`: render any moment as a Retina PNG.
- Themes: `terminal` (Basic, light), `terminal-dark`, `monokai`.
- `rec win`: ScreenCaptureKit recording of one window or app, `--desktop` mode with wallpaper and menu bar, constant 30 fps export; `rec notify` for app-defined switches.
- Tested on macOS 26.7.1, tmux 3.6a, asciinema 3.2.1, ffmpeg 8.1, Python 3.13.7, Claude Code 2.1.312; 7 unit tests passing.
