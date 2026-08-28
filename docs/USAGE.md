# Usage Guide

Hands-on guide to installing, running, configuring and observing
`pacman-bteng`. For how the thing is built, see
[ARCHITECTURE.md](ARCHITECTURE.md); for the trees themselves, see
[BEHAVIOR_TREES.md](BEHAVIOR_TREES.md); for the overview, see
[../README.md](../README.md).

There is **no Docker and no ROS 2** in this project. Everything lives in one
host virtualenv, so "build" is `pip install` and "test" is `pytest`.

---

## 1. Installing

### 1.1 The manual path (the reference)

Python **3.10+** is required.

```bash
cd pacman-bteng
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pacman_bteng
```

That installs four packages:

| Package | Why |
|---------|-----|
| `bteng[zmq]>=0.3.2` | Behavior-tree engine + the `ZmqPublisher` live stream |
| `pygame>=2.5` | Window, rendering, keyboard input |
| `PyYAML>=6.0` | Reading `config/game.yaml` |
| `rich>=13` | `tools/bt_tree_viewer.py`'s terminal tree view |

For the tests, install the dev set instead (it includes the runtime one):

```bash
.venv/bin/pip install -r requirements-dev.txt   # adds pytest>=7.4
```

> Debian/Ubuntu ship `venv` separately. If `python3 -m venv` fails:
> `sudo apt install python3-venv`.

### 1.2 The lazy path: `./run.sh`

`run.sh` chains exactly the commands above, skipping the ones already done. It
creates `.venv` on first use and reinstalls only when `requirements.txt`
changes — the file's sha256 is stamped inside `.venv` and compared each run.

```bash
./run.sh          # venv → install if needed → play
./run.sh setup    # just prepare the venv, do not start a game
```

Nothing is hidden: `--dry-run` prints the underlying commands instead of
running them.

```bash
$ ./run.sh --dry-run play --level 3
$ /path/to/pacman-bteng/.venv/bin/python -m pacman_bteng --level 3
```

---

## 2. `./run.sh` subcommands

| Command | What it does | Example |
|---------|--------------|---------|
| `play [args…]` | Ensure the venv, then start the game. **Default command.** Every argument goes straight to `python -m pacman_bteng`. | `./run.sh play --level 3 --seed 7` |
| `game` | Alias of `play`. | `./run.sh game` |
| `auto [args…]` | `play` plus `--auto`: Pac-Man drives himself from his own tree. | `./run.sh auto --seed 7` |
| `demo` | Alias of `auto` — the hands-off way to show the project off. | `./run.sh demo` |
| `setup` / `install` | Create `.venv` and install `requirements.txt`. Nothing else. | `./run.sh setup` |
| `test [args…]` | Install `requirements-dev.txt`, then run `pytest -q`. Extra args go to pytest. | `./run.sh test -k maze` |
| `tree [agent\|args…]` | Live visual BT viewer. A bare `blinky`/`pinky`/`inky`/`clyde`/`pacman` becomes `--agent <name>`; anything else is passed through. | `./run.sh tree clyde` |
| `watch` | Alias of `tree`. | `./run.sh watch pinky` |
| `viewer [args…]` | Flat live tick log (`tools/bt_zmq_viewer.py`). | `./run.sh viewer --filter Blinky --status RUNNING` |
| `log` | Alias of `viewer`. | `./run.sh log --raw` |
| `nodes` / `list-nodes` | `python -m pacman_bteng --list-nodes` — every registered node and its ports. | `./run.sh nodes` |
| `doctor` / `check` | Check python3, venv, requirements stamp, display and port 2667. Changes nothing. | `./run.sh doctor` |
| `clean` | Remove `__pycache__` and `.pytest_cache`. The `.venv` is deliberately kept. | `./run.sh clean` |
| `help` | The subcommand list. | `./run.sh help` |

A bare flag also works — `./run.sh --level 3` is `./run.sh play --level 3`.
`--dry-run` may prefix any of them.

`./run.sh doctor` is the fastest way to see what state a checkout is in:

```
━━ Checking prerequisites ━━
✅ python3 present (Python 3.12.3)
✅ .venv present (Python 3.12.3)
✅ requirements.txt satisfied (stamp matches)
✅ bteng importable
✅ pygame importable
✅ GUI available (DISPLAY=:0 WAYLAND_DISPLAY=–)
✅ TCP port 2667 free for the BT stream

ℹ️  Nothing above is fatal except a missing python3 — everything else './run.sh setup' fixes.
```

Typical two-terminal session:

```bash
./run.sh auto          # terminal 1 — the game, playing itself
./run.sh tree pinky    # terminal 2 — Pinky's tree, live
```

---

## 3. Command-line flags

Everything below is `python -m pacman_bteng --help`. Flags **win over**
`config/game.yaml`.

### Game

| Flag | Default | What it does |
|------|---------|--------------|
| `--config PATH` | `config/game.yaml` | YAML settings file. A missing *default* file is fine (the dataclass defaults are a complete configuration); an explicitly named missing file is an error. |
| `--maze PATH` | `assets/mazes/classic.txt` | ASCII maze file to play. |
| `--level N` | `1` | Start at this level. Everyone gains `rules.level_speed_bonus` tiles/s per level, capped at `rules.max_speed`. Clamped to ≥ 1. |
| `--ghosts A,B` | `blinky,pinky,inky,clyde` | Which ghosts to spawn, comma separated. An unknown name is a configuration error. Each spawned ghost gets its tree. |
| `--auto` | off | Hand Pac-Man to `bt_structures/pacman_auto.xml` — five trees on one stream. |
| `--seed N` | none | Seed the world RNG (used by `WanderStep`), so a run is reproducible. |
| `--loop` | off | Deal a new game four seconds after `GAME OVER`. Pairs with `--auto` for an unattended demo that does not stop on its first death. |

### Window

| Flag | Default | What it does |
|------|---------|--------------|
| `--fps N` | `60` | Frame-rate cap. In headless mode this is also the fixed time step (`dt = 1/fps`). Clamped to ≥ 1. |
| `--tile PX` | `24` | Pixels per maze tile. Shrunk automatically to fit the desktop; clamped to ≥ 6. |
| `--visuals lite\|deluxe` | `lite` | `lite` is clean, crisp and cheap (~1.6 ms/frame) and is also the calm option; `deluxe` adds neon bloom, particles, score popups, screen shake and CRT scanlines (~4 ms/frame). `F3` switches live. |
| `--no-panel` | off | Hide the right-hand live behavior-tree panel (`TAB` toggles it at runtime). |
| `--debug` | off | Draw each ghost's current target and the line to it, and print every non-pellet game event to stdout with a timestamp. |

### Introspection

| Flag | Default | What it does |
|------|---------|--------------|
| `--zmq-port N` | `2667` | Port for the BTEng `ZmqPublisher` stream. |
| `--no-zmq` | off | Do not publish the tick stream at all. |
| `--log-console` | off | Colored `Logger` console sink — every node transition. Very chatty. |
| `--log-file PATH` | none | Append every node transition as JSON lines. |

### Headless / tooling

| Flag | Default | What it does |
|------|---------|--------------|
| `--headless` | off | SDL dummy video driver plus a fixed time step, so a smoke run is reproducible and machine-independent. The real drawing code still runs. |
| `--max-seconds S` | `0` | Stop after this much *game* time. `0` = run until quit. Clamped to ≥ 0. |
| `--list-nodes` | — | Print every registered BT node with its ports, then exit. Needs no display, no maze and no config file. |
| `--version` | — | `pacman-bteng 1.0.0`. |

Examples straight from the CLI epilog:

```bash
python -m pacman_bteng                       # play, four ghosts, four trees
python -m pacman_bteng --auto                # watch Pac-Man play himself
python -m pacman_bteng --auto --loop         # unattended demo; restarts itself
python -m pacman_bteng --ghosts blinky       # one ghost, for reading the tree
python -m pacman_bteng --debug               # targets on screen, events on stdout
python -m pacman_bteng --headless --max-seconds 20    # smoke test, no window
python -m pacman_bteng --list-nodes          # every BT node and its ports
```

A headless smoke run prints its banner and a one-line summary:

```
$ .venv/bin/python -m pacman_bteng --headless --max-seconds 5 --zmq-port 2671
pac-man x BTEng   maze classic (28x31), 242 pellets
  window     972x811 (tile 24px)  [headless]
  trees      blinky, pinky, inky, clyde
  introspect ZMQ tcp://*:2671
  watch it   ./run.sh tree blinky   |   ./run.sh viewer

L1 score=0 lives=3 pellets=242 mode=scatter  (301 frames, 5.0s game time, 150 fps)
```

---

## 4. In-game keys

| Key | Action |
|-----|--------|
| Arrows / `W` `A` `S` `D` | Steer Pac-Man. The request is buffered and applied at the next tile centre, so you can turn slightly early. |
| `SPACE` / `P` | Pause / resume |
| `R` | Restart from level 1 (also resets every tree to IDLE) |
| `TAB` | Show/hide the behavior-tree panel — the window is rebuilt at the new size |
| `F1` | Debug overlay (ghost targets + tunnel row labels) |
| `F2` | Toggle Pac-Man's autopilot. The tree is parsed and validated on first use, not at start-up. |
| `F3` | Switch between the `lite` and `deluxe` visual presets. The window is rebuilt, the game keeps going. |
| `ESC` / `Q` | Quit |

---

## 5. `config/game.yaml`, section by section

Every key is optional — a missing one falls back to the dataclass default in
`pacman_bteng/config.py` (or `Rules` in `pacman_bteng/game/world.py`), so a
two-line override file is perfectly valid. Unknown keys are ignored rather than
fatal.

```bash
python -m pacman_bteng --config config/game.yaml
./run.sh play --config my_overrides.yaml
```

### `window:`

| Key | Default | Meaning |
|-----|---------|---------|
| `tile_size` | `24` | Pixels per maze tile. Auto-shrinks to fit the screen (floor: 6 px). |
| `fps` | `60` | Frame-rate cap. |
| `show_panel` | `true` | Start with the right-hand behavior-tree panel visible. |
| `caption` | `"Pac-Man · BTEng"` | Window title. |

### `rules:`

Speeds are in **tiles per second**; the maze is 28 x 31 tiles.

| Key | Default | Meaning |
|-----|---------|---------|
| `lives` | `3` | Lives per run (the icons in the bottom bar show the *spare* ones). |
| `pacman_speed` | `8.0` | Pac-Man's base speed. |
| `ghost_speed` | `7.4` | A ghost's normal speed. |
| `frightened_speed` | `4.8` | While edible. |
| `eaten_speed` | `16.0` | Eyes racing back to the house. |
| `tunnel_speed_factor` | `0.55` | Multiplier while a non-eaten ghost is in the side tunnel. |
| `power_duration` | `7.0` | Seconds of fright per power pellet. |
| `power_blink_lead` | `2.0` | Start blinking this long before the fright ends. |
| `level_speed_bonus` | `0.35` | Tiles/s added to everyone per level. |
| `max_speed` | `12.0` | Ceiling for the per-level bonus. |
| `ready_duration` | `2.0` | READY! freeze before a round. |
| `dying_duration` | `1.6` | Freeze after Pac-Man is caught. |
| `level_clear_duration` | `2.4` | Freeze after the board is cleared. |
| `release_delays` | `blinky 0.0, pinky 2.0, inky 6.0, clyde 10.0` | Seconds each ghost waits in the pen at the start of a round. |
| `mode_schedule` | 8 entries, `scatter 7 → chase 20 → …` | The scatter/chase rhythm, in order, as `[mode, seconds]` pairs. A negative duration means "for the rest of the level". Every switch reverses the ghosts on the board. The schedule is **frozen** while a power pellet burns. |
| `scatter_corners` | `blinky [26,1]`, `pinky [1,1]`, `inky [26,29]`, `clyde [1,29]` | Each ghost's private corner, as `[column, row]`. |
| `points_pellet` | `10` | Points per pellet. |
| `points_power` | `50` | Points per power pellet. |
| `ghost_chain` | `[200, 400, 800, 1600]` | Consecutive ghosts eaten during one power pellet. |
| `extra_life_at` | `10000` | Score interval that grants a life. |

### `behavior:`

| Key | Default | Meaning |
|-----|---------|---------|
| `tick_hz` | `0.0` | `0` = tick every rendered frame (recommended — the tree then decides at exactly the rate the world moves). A positive value ticks on a fixed accumulator, with bounded catch-up of at most 4 steps per frame. |
| `trees` | `{blinky: ghost_blinky.xml, …}` | One XML per ghost. Relative names resolve against `bt_structures/`; absolute paths are used as-is. |
| `pacman_auto` | `pacman_auto.xml` | The tree used by `--auto` and by `F2`. |

### `introspection:`

| Key | Default | Meaning |
|-----|---------|---------|
| `enable_zmq` | `true` | Attach BTEng's `ZmqPublisher` to the shared Inspector. |
| `zmq_port` | `2667` | The publisher binds `tcp://*:<port>`. |
| `console_log` | `false` | Colored `Logger` console sink — every node transition. |
| `log_file` | `null` | Path for a JSON-lines sink, e.g. `logs/bt_run.jsonl`. |

---

## 6. Watching the trees live

**There is no Groot2 here.** Groot2 speaks BehaviorTree.CPP's wire protocol,
which BTEng's event stream is not — they cannot interoperate. BTEng streams its
own JSON tick events over a ZMQ `PUB` socket (topic `bteng`) instead, and two
viewers ship in `tools/`.

All five trees publish onto **one** socket. Every node instance name in the XML
carries its agent as a prefix (`Blinky/Chase`, `Pacman/Evade`), which is exactly
what the viewers use to tell the streams apart.

### 6.1 Flat tick log — `tools/bt_zmq_viewer.py`

```bash
.venv/bin/python tools/bt_zmq_viewer.py                        # everything
.venv/bin/python tools/bt_zmq_viewer.py --filter blinky        # one ghost only
.venv/bin/python tools/bt_zmq_viewer.py --filter Inky/ --status RUNNING,SUCCESS
.venv/bin/python tools/bt_zmq_viewer.py --raw | jq .
```

| Flag | Default | Meaning |
|------|---------|---------|
| `--host HOST` | `localhost` | Where the publisher is reachable. |
| `--port PORT` | `2667` | Publisher port. |
| `--raw` | off | Print each event as raw JSON instead of a formatted line. |
| `--filter SUBSTR` | none | Only events whose node name contains `SUBSTR`, case-insensitive. Node names are agent-prefixed, so `blinky` watches one ghost. |
| `--status S[,S…]` | none | Only these statuses: `SUCCESS`, `FAILURE`, `RUNNING`, `IDLE`, `SKIPPED`. ANDed with `--filter`. |

```
Listening for BTEng tick events on tcp://localhost:2667 … (Ctrl-C to quit)
Pinky/WasEaten               FAILURE    0.01 ms state=normal
Pinky/ReturnHome             FAILURE    0.02 ms
Pinky/StepToCorner           RUNNING    0.00 ms UP toward (1, 1) [greedy]
Pinky/Scatter                RUNNING    0.01 ms
Pinky/Brain                  RUNNING    0.10 ms
```

`--raw` gives the wire format:

```json
{"ts": 322412.517, "uid": "540b5f89...", "name": "Pinky/ScatterPhase",
 "type": "condition", "status": "FAILURE", "dur_ms": 0.01, "reason": "mode=chase"}
```

The `reason` field is whatever the node passed to `report()` — it is the same
string the GUI panel shows.

### 6.2 Visual live tree — `tools/bt_tree_viewer.py`

Draws the actual tree structure (parsed from the agent's XML in
`bt_structures/`) and recolors each node in place as events arrive. Needs
`rich` and `pyzmq`; both are in `requirements.txt`.

```bash
.venv/bin/python tools/bt_tree_viewer.py                 # Blinky (default)
.venv/bin/python tools/bt_tree_viewer.py --agent clyde
.venv/bin/python tools/bt_tree_viewer.py --agent pacman  # only ticks under --auto
.venv/bin/python tools/bt_tree_viewer.py --xml bt_structures/ghost_inky.xml
```

| Flag | Default | Meaning |
|------|---------|---------|
| `--host HOST` | `localhost` | Publisher host. |
| `--port PORT` | `2667` | Publisher port. |
| `--agent {blinky,pinky,inky,clyde,pacman}` | `blinky` | Which agent's tree to draw the skeleton from. Events whose name prefix is not this agent's are counted, not drawn. |
| `--xml XML` | none | Explicit path to a behavior-tree XML; overrides `--agent`. |

### 6.3 Through `run.sh`

```bash
./run.sh viewer                          # = tools/bt_zmq_viewer.py
./run.sh viewer --filter Blinky --status RUNNING
./run.sh tree                            # = tools/bt_tree_viewer.py
./run.sh tree clyde                      # 'clyde' → --agent clyde
./run.sh tree --xml bt_structures/ghost_inky.xml
```

### 6.4 Rolling your own subscriber

The whole client is a handful of lines:

```python
import json
import zmq

ctx = zmq.Context.instance()
sock = ctx.socket(zmq.SUB)
sock.connect("tcp://localhost:2667")
sock.setsockopt(zmq.SUBSCRIBE, b"bteng")   # topic prefix

while True:
    raw = sock.recv()
    _topic, _, payload = raw.partition(b" ")   # "bteng <json>"
    evt = json.loads(payload)
    print(f"{evt['name']:<28} {evt['status']:<8} {evt['dur_ms']:>6.2f} ms {evt.get('reason','')}")
```

### 6.5 Without ZMQ at all

The right-hand GUI panel shows the same information with no socket involved:
per agent, which branch is running, which leaf node is active, what the last
tick returned and which tile the tree is steering toward. `--log-console` and
`--log-file` are the third and fourth sinks on the same Inspector.

---

## 7. Running the tests

```bash
./run.sh test                # installs requirements-dev.txt, then pytest -q
./run.sh test -k maze        # extra arguments go straight to pytest
```

or manually:

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Tests live in `tests/`. The game layer is importable with no display and no
pygame, and `--headless` runs the real rendering code against SDL's dummy
driver with a fixed time step, so both are testable in CI:

```bash
.venv/bin/python -m pacman_bteng --headless --max-seconds 20 --no-zmq
```

---

## 8. Troubleshooting

| Symptom | Fix |
|---------|-----|
| No window opens; `DISPLAY` / `WAYLAND_DISPLAY` unset | Run with `--headless` — SDL's dummy driver, fixed time step, and the BT stream still works. `./run.sh` warns about this before starting. |
| `ZMQ stream disabled: port 2667 is already in use` on the banner | Another game (or a stale one) holds the port. The game plays on without the stream; to get it back, start with `--zmq-port 2671` and point the viewers at `--port 2671`, or free the port. `./run.sh doctor` checks it. **The port is probed before the publisher is built, on purpose:** BTEng's `ZmqPublisher` binds outside its `try/finally`, so a clash leaks an unclosed socket into the global ZMQ context and pyzmq's `atexit` `ctx.term()` blocks forever — the process would then ignore Ctrl-C *and* SIGTERM and need `kill -9`. Never bypass that probe. |
| `ModuleNotFoundError: No module named 'pygame'` / `'bteng'` | The venv is missing or stale: `./run.sh setup` (or `pip install -r requirements.txt`). `./run.sh doctor` reports both imports. |
| `ZMQ stream disabled: …` warning on the banner | `pyzmq` is missing. `pip install 'bteng[zmq]'`. Everything else runs. |
| `error: <path>.xml: Tree port validation failed:` at start-up | See below. |
| `error: <path>.xml: not well-formed (invalid token): line N, column M` | Malformed XML. The classic cause here is a `--` **inside an XML comment**, which is illegal and bit this project during development. Use `-` or an em dash in comments. |
| `error: blinky: behavior tree not found at …` | `behavior.trees` in `config/game.yaml` points at a file that does not exist. Relative names resolve against `bt_structures/`. |
| `configuration error: unknown ghost(s) sue; choose from blinky, pinky, inky, clyde` | Typo in `--ghosts`. |
| `configuration error: config file not found: …` | An explicitly passed `--config` must exist. Only the *default* file is allowed to be absent. |
| Ghosts stand still / never leave the pen | Check `rules.release_delays` and that the maze has all five spawn markers (`S B P I C`). A malformed maze raises a `MazeError` naming what is missing. |
| A ghost behaves like Blinky when it should flank | `SetFlankTarget` falls back to chasing directly when its `partner` ghost is not on the board — e.g. under `--ghosts inky`. |

### Tree validation errors

Validation runs inside `TreeExecutor.set_tree()`, when the brain is built. The
message names the node class, the instance name and the port:

```
error: bt_structures/ghost_blinky.xml: Tree port validation failed:
  [StepTowardTarget] 'Blinky/StepAway' / port 'speed': mapped as input but not declared in provided_ports()
```

Two rules cause it:

1. **Every port used in the XML must be declared in the node's
   `provided_ports()`** — whether it is a `{key}` blackboard mapping or a
   static value. Run `python -m pacman_bteng --list-nodes` to see exactly which
   ports a node has; a port that is not listed there does not exist.
2. **An input port whose `default` is `None` is REQUIRED**, and must be given a
   mapping or a static value in the XML, or you get
   `required input port has no blackboard mapping or static value`. Every input
   in this project's library has a real default, so this only bites new nodes.

`<TreeNodesModel>` port directions are *not* validated — see
[ARCHITECTURE.md](ARCHITECTURE.md#bteng-gotchas-do-not-deviate) for why getting
them wrong fails silently instead.

---

## 9. Where things live

| Path | Purpose |
|------|---------|
| `pacman_bteng/app.py` | The frame loop and the app-level key intents |
| `pacman_bteng/cli.py` | Flags, config folding, `--list-nodes` |
| `pacman_bteng/config.py` | `Settings` / `WindowSettings` / `BehaviorSettings` / `IntrospectionSettings` |
| `pacman_bteng/game/world.py` | `GameWorld`, `Rules`, `GamePhase`, `GlobalMode`, `request_direction` |
| `pacman_bteng/game/maze.py` | ASCII map parsing, walls, door, tunnel, spawns |
| `pacman_bteng/game/pathfinding.py` | `greedy_step`, `bfs_step`, `random_step`, `legal_moves` |
| `pacman_bteng/behaviors/conditions.py` | The condition nodes (guards) |
| `pacman_bteng/behaviors/actions.py` | Target selectors and movers |
| `pacman_bteng/behaviors/runner.py` | `AgentBrain`, `BrainPool`, `node_catalogue()` |
| `pacman_bteng/introspection/stream.py` | Inspector + Logger + `ZmqPublisher` wiring |
| `pacman_bteng/gui/input.py` | The key maps and `KEY_HINT` |
| `bt_structures/*.xml` | The five trees |
| `config/game.yaml` | All tunables |
| `assets/mazes/classic.txt` | The maze, with its character legend in the header |
| `tools/` | The two ZMQ viewers |
| `run.sh` | Optional wrapper around everything above |
