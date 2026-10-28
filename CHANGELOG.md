# Changelog

## 1.1.0

- **Title screen**: the game opens on a title card (ENTER to play, F2 for the
  autopilot). Skipped for `--auto`, `--loop`, `--headless` and `--no-title`.
- **Sound**: synthesised effects for pellets, power pellets, eaten ghosts,
  death, level start/clear, extra life and game over. No audio files. `M` or
  `--mute` silences them; no sound device is not an error.
- **Overlay cards**: READY!, PAUSED, GAME OVER and the title share one rounded
  card with a headline and a caption, over a dimmed maze.
- `tools/shots.py` gains a `title` shot; CI runs the test suite on 3.10 to 3.13.

## 1.0.0

- First release: four ghost behavior trees plus Pac-Man's autopilot, a pygame
  GUI with `lite` and `deluxe` presets, and a live BTEng ZMQ stream.
