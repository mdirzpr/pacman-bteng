# Changelog

## Unreleased

- **Autopilot no longer paces back and forth.** The tree finished a tile and
  waited a frame before deciding again; in that frame the world walked Pac-Man
  on, so the next decision used a target picked one tile earlier. Standing on
  the pellet it had aimed at, the BFS found no path and a greedy fallback
  turned him round. `AgentBrain.tick()` now starts the next cycle in the same
  tick. Ghosts also stop losing a frame per tile.
- `IsGhostWithin` gains `hysteresis`, and `SetEscapeTarget` gains
  `reverse_penalty`; the autopilot uses both (3 and 1.5). Over six seeded
  90 s runs: reversals 1080 to 162, score 7560 to 39720, same lives lost.
- GUI: glass agent cards, tab dock (TREES / STATS / LOG), power-timer and
  pellets-left ring gauges, score sparkline, proximity lift, tab cross-fade.
  See `gui-guidline.md`.

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
