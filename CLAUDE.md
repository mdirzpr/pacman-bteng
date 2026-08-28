# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

The arcade Pac-Man, with **every ghost driven by its own BTEng behavior tree**
loaded from XML, a pygame GUI, and BTEng's `ZmqPublisher` streaming every node
transition live.

- **No ROS 2, no Docker, no colcon, no Groot2.** Everything runs on the host in
  one virtualenv (`./.venv`). "Build" is `pip install`, "test" is `pytest`.
- `bteng` is installed **from PyPI** (`bteng[zmq]>=0.3.2`), not built from
  source.
- **Groot2 is deliberately not used** — it speaks BehaviorTree.CPP's wire
  protocol, which BTEng's event stream is not. Introspection is the BTEng
  `Inspector` fanned out to a ZMQ publisher, an optional console `Logger`, an
  optional JSONL file, and the GUI's own side panel. `tools/bt_tree_viewer.py`
  is the visual stand-in.

Five trees ship: one per ghost (`bt_structures/ghost_*.xml`) plus Pac-Man's
autopilot (`pacman_auto.xml`, loaded by `--auto` / `F2`).

**The central design point:** the four ghosts differ by exactly **one node in
one XML file**, because target selection is split from movement. A `Set*Target`
action writes `target_x` / `target_y` as output ports; `StepTowardTarget` reads
them back as input ports and owns all the movement mechanics. Swap the selector
and you have swapped the personality.

## Development Environment

No container. One venv on the host.

```bash
python3 -m venv .venv                        # Python 3.10+
.venv/bin/pip install -r requirements.txt    # bteng[zmq], pygame, PyYAML, rich
.venv/bin/pip install -r requirements-dev.txt  # + pytest
```

`./run.sh` is an optional wrapper that chains exactly those commands, skipping
what is already done (it stamps the requirements sha256 inside `.venv`). It
adds no behavior of its own — the manual flow stays the reference.
`./run.sh --dry-run <cmd>` prints the underlying command; `./run.sh doctor`
checks python3, the venv, the requirements stamp, the display and port 2667
without changing anything.

## Running

```bash
.venv/bin/python -m pacman_bteng                       # play
.venv/bin/python -m pacman_bteng --auto                # Pac-Man plays himself
.venv/bin/python -m pacman_bteng --auto --loop         # unattended demo, restarts itself
.venv/bin/python -m pacman_bteng --ghosts blinky       # one ghost, for reading the tree
.venv/bin/python -m pacman_bteng --headless --max-seconds 20   # smoke test, no window
.venv/bin/python -m pacman_bteng --list-nodes          # every node and its ports
```

`./run.sh` / `./run.sh auto` / `./run.sh test` / `./run.sh tree <agent>` /
`./run.sh viewer` / `./run.sh nodes` are the shortcuts. Everything after
`play`/`auto` is passed straight to the CLI.

Flag groups: **game** (`--config --maze --level --ghosts --auto --loop --seed`),
**window** (`--fps --tile --visuals --no-panel --debug`), **introspection**
(`--zmq-port --no-zmq --log-console --log-file`), **headless/tooling**
(`--headless --max-seconds --list-nodes --version`). CLI flags win over
`config/game.yaml`.

In-game keys (`pacman_bteng/gui/input.py`, next to `KEY_HINT`): arrows/WASD
move, `SPACE`/`P` pause, `R` restart, `TAB` panel, `F1` debug overlay, `F2`
autopilot, `F3` lite/deluxe visuals, `ESC`/`Q` quit.

## Architecture

### Layers and the import rule

```
pacman_bteng/game/            pure rules — imports neither bteng nor pygame
pacman_bteng/behaviors/       BTEng node library + runners — does not import pygame
pacman_bteng/gui/             pygame only — does not import behaviors/
pacman_bteng/introspection/   Inspector / Logger / ZmqPublisher
pacman_bteng/app.py           the only module that wires them together
```

`gui/hud.py` duck-types the brain objects (`.name`, `.status`, `.branch`,
`.active_node`, `.describe()`), so the GUI works with no trees attached and the
two packages never form an import cycle. `app.py` imports pygame late, inside
`PacmanApp.__init__`.

### The frame loop (`app.py`)

`input → tick trees → world.update → render`, in that order. Step 2 sits before
step 3 on purpose: the tree decides which tile to head for and the world
carries it out in the same frame; reversing them gives every ghost a frame-old
view of the maze. Trees tick only while `GamePhase.PLAYING` and not paused.
`dt` is clamped to `MAX_FRAME_DT = 0.05`; `--headless` uses a fixed `1/fps`.

### The decision-point contract

The single handshake between game and trees:

- `agent.direction is Direction.NONE` means **"parked on a tile centre, waiting
  for a controller"**. `Agent.is_deciding` is that check.
- `Agent.advance()` clears `direction` the instant the agent reaches a tile
  centre, stashing the incoming direction as `last_direction` (the reversal
  ghosts may not take). **Nothing else may clear it.**
- Controllers set a direction only through
  `world.request_direction(agent, direction)`, which returns `False` mid-tile
  or when blocked, and applies `through_door=agent.may_pass_door`.

That is the whole interface `behaviors/` needs, which is why `game/world.py`
never imports BTEng. Player input is a *request*
(`pacman.desired_direction`), applied at the next decision point, falling back
to `last_direction` when the turn is into a wall.

### The looping-tree pattern (`behaviors/runner.py`)

BTEng has no `Repeat` decorator and a tree that returns a terminal status is
finished, so `AgentBrain.tick()` calls `executor.reset_tree()` on any
non-RUNNING result and keeps ticking. **One completed tree = one tile of travel
decided.** The XML root is a bare `<ReactiveFallback>`; there is no wrapper.

Executor is manual: `ExecutorConfig(tick_interval=0.0, halt_on_completion=False,
thread_pool_size=0)` plus one `tick_once()` per frame (the game already owns a
clock). `halt_on_completion=False` so a halt between cycles cannot throw away a
RUNNING mover mid-tile.

`StepAction` (a `StatefulActionNode`) stays RUNNING for a whole tile:
`on_start` commits a direction, `on_running` returns SUCCESS the frame the
world clears `direction`, `on_halted` drops the commitment. Activated mid-tile
it returns RUNNING, never FAILURE.

### Blackboard contract (`behaviors/blackboard_keys.py`)

Each brain gets a **private** `Blackboard(scope_name=f"bt.{name}")`.

| Kind | Keys | Purpose |
|------|------|---------|
| Object refs (seeded once) | `agent`, `world` | The node library reads the real game objects; a BFS does not fit through a scalar port |
| Ports | `target_x`, `target_y` | Output of every `Set*Target`, input of `StepTowardTarget` |
| Scalar mirrors (every tick, `K.sync`) | `agent_name`, `agent_state`, `agent_x`, `agent_y`, `agent_direction`, `pacman_x`, `pacman_y`, `distance_to_pacman`, `mode`, `power_remaining`, `pellets_left`, `level` | See below |

The mirrors exist for two reasons: **(1)** they are what appears in the ZMQ
stream and the viewers — the difference between "SelectTarget RUNNING" and
seeing *why*; **(2)** writing them every tick marks the blackboard dirty every
tick, which keeps `ReactiveFallback` out of its skip-ahead fast path so the
guards stay genuinely reactive. `K.MIRRORED` is the display order.

### Introspection (`introspection/stream.py`)

**One shared `Inspector` for all five trees** — the viewers subscribe to one
ZMQ endpoint, and a ghost's decision only makes sense next to what the others
were doing. That is why every node instance name in the XML carries an
`Agent/` prefix (`Blinky/Chase`, `Pacman/Evade`): it is what keeps one stream
readable and what `--filter` / `--agent` key off. `AgentBrain._short()` strips
it again for the GUI panel.

**The Logger is wired by hand, not via `TreeExecutor.set_logger()`** — that
method subscribes the logger to the inspector *per executor*, so five executors
sharing one inspector would print every transition five times. `BTIntrospection`
subscribes a single relay callback to the Inspector instead.

Sinks: `ZmqPublisher` on `tcp://*:2667` (`enable_zmq` / `--zmq-port`), colored
console `Logger` (`console_log` / `--log-console`), JSONL file (`log_file` /
`--log-file`), and the GUI panel (which reads the brains directly). A missing
`pyzmq` is a banner warning, never a startup failure.

### BTEng gotchas (do not deviate)

- **Static XML attributes arrive as strings.** `lead_tiles="4"` → `"4"`,
  `allow_reverse="true"` → `"true"`. Cast through `as_float` / `as_int` /
  `as_bool` in `behaviors/nodes.py`. Never test `get_input()` for truthiness —
  the string `"false"` is truthy.
- **An input port with `default=None` is REQUIRED.** Validation fails with
  `required input port has no blackboard mapping or static value` unless the
  XML maps it or gives it a static value. Every input in this library has a
  real default.
- **Every port used in the XML must be declared in `provided_ports()`.** This
  *is* validated, inside `TreeExecutor.set_tree()`:
  `[StepTowardTarget] 'Blinky/StepAway' / port 'speed': mapped as input but not declared in provided_ports()`.
- **`<TreeNodesModel>` directions override `provided_ports()` and are NOT
  validated.** The parser routes `attr="{key}"` to `input_ports` or
  `output_ports` from the model, falling back to the node's declaration only
  when the model has no entry. A wrong direction therefore does not raise — it
  silently misroutes, and `set_output()` returns `False` / `get_input()` never
  sees the key. Keep the model in step by hand: `Set*Target.target_*` are
  `<output_port>`, `StepTowardTarget.target_*` are `<input_port>`.
- **`--` is illegal inside an XML comment.** Plain XML, not BTEng — and it bit
  this project. The parse dies with
  `not well-formed (invalid token): line N, column M`. Use `-` or an em dash.
- **`ZmqPublisher` deadlocks the interpreter if its port is taken.**
  `ZmqPublisher._run()` calls `sock.bind()` *outside* its `try/finally`, so a
  clash leaks an unclosed socket into the global ZMQ context and pyzmq's
  `atexit` `ctx.term()` blocks forever. The process then ignores Ctrl-C **and**
  SIGTERM and needs `kill -9`. Starting the game twice is enough to trigger it.
  `BTIntrospection._start_publisher()` probes the port with a plain socket
  first and degrades to a warning — do not remove that check.
- **`Blackboard.create()` memoises by name.** Four ghosts asking for one would
  share a single board and overwrite each other's target every tick, so
  `AgentBrain` builds a private `Blackboard(scope_name=...)` — never
  `Blackboard.create()`.
- **Nodes are constructed with only a name and a `NodeConfig`.** The XML parser
  builds them; they pick `agent` / `world` off the blackboard at tick time.
  Never touch the game in `__init__`.
- **`ReactiveFallback` re-ticks every guard on every tick.** Conditions must be
  cheap and side-effect free. That reactivity is what lets a power pellet
  interrupt a ghost mid-corridor.
- **Node tag == class name.** `@register_node()` registers with BTEng's global
  `NodeFactory`; the XML uses `<SetChaseTarget/>` with no `ID=`. `runner.py`
  imports `actions` and `conditions` so registration happens before any parse.

### Adding a new BT node

1. Subclass the right base in `behaviors/nodes.py` — `GameCondition`
   (implement `tick()`, return `self.verdict(ok, "why")`), `TargetSelector`
   (implement `choose() -> Optional[Tile]`), `StepAction` (implement
   `choose_direction() -> Direction`), or `GameNode` + a raw BTEng base for
   anything that spans several tiles (`LeaveHouse` is the example).
2. Put it in `behaviors/conditions.py` or `behaviors/actions.py` and decorate
   with `@register_node()`.
3. Declare ports in `provided_ports()`, chaining `*super().provided_ports()`,
   with a non-`None` default on every input.
4. Cast every input; call `self.report("…")` so the reason reaches the
   Inspector, the ZMQ `reason` field and the GUI panel at once.
5. Use it in XML by tag name with an agent-prefixed instance name.
6. Add a matching `<Condition ID="…">` / `<Action ID="…">` entry to the
   `<TreeNodesModel>` of **every** XML that uses it, directions matching
   `provided_ports()`. Nothing checks this — a mismatch is a silent bug.
7. Verify with `--list-nodes`, then
   `python -m pacman_bteng --headless --max-seconds 20`.

## Configuration

`config/game.yaml` is the single source of truth for tunables; every key is
optional (missing keys fall back to the dataclass defaults, unknown keys are
ignored). Four sections:

- **`window`** — `tile_size` (24), `fps` (60), `show_panel` (true), `caption`.
- **`rules`** — speeds in tiles/s (`pacman_speed` 8.0, `ghost_speed` 7.4,
  `frightened_speed` 4.8, `eaten_speed` 16.0, `tunnel_speed_factor` 0.55),
  timers (`power_duration` 7.0, `ready_duration`, `dying_duration`,
  `level_clear_duration`), progression (`level_speed_bonus` 0.35, `max_speed`
  12.0), `release_delays`, `mode_schedule` (scatter/chase pairs; a negative
  duration means "rest of the level"; every switch reverses the ghosts; frozen
  while a power pellet burns), `scatter_corners`, scoring
  (`points_pellet`, `points_power`, `ghost_chain`, `extra_life_at`).
- **`behavior`** — `tick_hz` (0 = tick with the frame, recommended), `trees`
  (agent → XML, relative to `bt_structures/`), `pacman_auto`.
- **`introspection`** — `enable_zmq`, `zmq_port` (2667), `console_log`,
  `log_file`.

The maze is ASCII (`assets/mazes/classic.txt`, 28x31): `#` wall, `X` void, `.`
pellet, `o` power pellet, `-` door, `S B P I C` spawns, rows between `[maze]` /
`[/maze]`. All rows must be exactly the same width and all five spawn markers
must be present, or `Maze.from_string` raises a `MazeError` naming what is
wrong.

## Key file locations

| Path | Purpose |
|------|---------|
| `pacman_bteng/app.py` | `PacmanApp` — the frame loop, key intents, startup banner, shutdown summary |
| `pacman_bteng/cli.py` | `build_parser()`, `settings_from_args()`, `main()`, `--list-nodes` |
| `pacman_bteng/config.py` | `Settings`, `WindowSettings`, `BehaviorSettings`, `IntrospectionSettings`, `tree_path()`, `active_agents()` |
| `pacman_bteng/game/geometry.py` | `Direction` (incl. `NONE`), `Tile`, `MOVES` (UP, LEFT, DOWN, RIGHT tie-break order), `manhattan`, `euclidean` |
| `pacman_bteng/game/maze.py` | `Maze` (frozen), ASCII parsing, walls/door/house/tunnel, `door_exit()`, `house_centre()` |
| `pacman_bteng/game/entities.py` | `Agent`, `Pacman`, `Ghost`, `GhostState`; the decision-point contract lives in `Agent.advance()` |
| `pacman_bteng/game/world.py` | `GameWorld`, `Rules`, `GamePhase`, `GlobalMode`, `GameEvent`, `request_direction()`, `on_ghost_left_house()`, `on_ghost_reached_house()` |
| `pacman_bteng/game/pathfinding.py` | `legal_moves`, `greedy_step` (arcade rule), `bfs_step` / `bfs_path`, `random_step`, `distance_field`, `STEP_STRATEGIES`, `resolve_strategy` |
| `pacman_bteng/behaviors/nodes.py` | `GameNode` mixin, `GameCondition`, `TargetSelector`, `StepAction`, `as_float/as_int/as_bool`, the shared target port definitions |
| `pacman_bteng/behaviors/conditions.py` | All condition nodes (`@register_node()`) |
| `pacman_bteng/behaviors/actions.py` | All target selectors and movers (`@register_node()`) |
| `pacman_bteng/behaviors/blackboard_keys.py` | The blackboard contract: `seed()`, `sync()`, `MIRRORED` |
| `pacman_bteng/behaviors/runner.py` | `AgentBrain`, `BrainPool`, `BrainError`, `node_catalogue()`, `registered_nodes()` |
| `pacman_bteng/gui/renderer.py` | Whole-frame drawing (`pygame.draw` only, no image assets), cached board, debug overlay |
| `pacman_bteng/gui/hud.py` | Top/bottom bars and the live behavior-tree panel |
| `pacman_bteng/gui/layout.py` | The only place tiles become pixels |
| `pacman_bteng/gui/theme.py` | `VisualStyle`, the two frozen palettes, `agent_colour()` |
| `pacman_bteng/gui/effects.py` | The deluxe-only effect layer: bloom, particles, popups, scanlines, shake |
| `pacman_bteng/gui/input.py` | Key maps + `KEY_HINT`, `Intent` |
| `pacman_bteng/introspection/stream.py` | `BTIntrospection` — one Inspector, Logger relay, `ZmqPublisher` |
| `bt_structures/ghost_*.xml` | The four ghost trees; identical except the chase branch |
| `bt_structures/pacman_auto.xml` | Pac-Man's autopilot tree |
| `bt_structures/pacman.btproj` | Project index (agent → XML); notes that these are BTEng, not Groot2, files |
| `config/game.yaml` | All tunables |
| `assets/mazes/classic.txt` | The 28x31 maze, legend in the header |
| `tools/bt_zmq_viewer.py` | Flat live tick log (`--filter`, `--status`, `--raw`) |
| `tools/bt_tree_viewer.py` | Live visual tree (`--agent`, `--xml`) |
| `run.sh` | Optional wrapper: play / auto / setup / test / tree / viewer / nodes / doctor / clean |
| `requirements.txt`, `requirements-dev.txt` | Runtime deps; + pytest |
| `tests/` | pytest target for `./run.sh test` |

## Docs

- [README.md](README.md) — front door, quick start, ghost table, introspection.
- [docs/USAGE.md](docs/USAGE.md) — install, every `run.sh` subcommand, every
  CLI flag, `config/game.yaml` walked through, the viewers, troubleshooting.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — layers, frame loop, the
  decision-point contract, looping trees, blackboard, gotchas, how to add a node.
- [docs/BEHAVIOR_TREES.md](docs/BEHAVIOR_TREES.md) — the five trees branch by
  branch, the full node/port catalogue, tuning recipes.
