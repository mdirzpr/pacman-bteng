# Architecture

How `pacman-bteng` is put together, for someone about to change it. For running
it, see [USAGE.md](USAGE.md); for the trees, see
[BEHAVIOR_TREES.md](BEHAVIOR_TREES.md).

---

## 1. Four layers, one import rule

```
pacman_bteng/game/            pure rules — maze, agents, world state, pathfinding
pacman_bteng/behaviors/       the BTEng node library + the per-agent tree runners
pacman_bteng/gui/             pygame rendering, HUD and keyboard
pacman_bteng/introspection/   Inspector / Logger / ZmqPublisher wiring
pacman_bteng/app.py           the only module that wires all four together
```

The rule, and it is strict:

| Layer | May import | Must **not** import |
|-------|-----------|---------------------|
| `game/` | stdlib only | `bteng`, `pygame` |
| `behaviors/` | `bteng`, `game/` | `pygame` |
| `gui/` | `pygame`, `game/` | `behaviors/`, `bteng` |
| `introspection/` | `bteng` | `game/`, `pygame` |
| `app.py` | all of them | — |

What that buys:

- The **game runs headless with no pygame installed at all**, which is what
  makes the rules testable and what lets `--list-nodes` work with no display,
  no maze and no config file.
- The **GUI works with no trees attached**. `gui/hud.py` duck-types the brain
  objects it is handed — it only ever touches `.name`, `.status`, `.branch`,
  `.active_node` and `.describe()` — so the panel falls back to raw ghost state
  rather than the two packages forming an import cycle.
- `app.py` imports pygame **late**, inside `PacmanApp.__init__`, for the same
  reason.

---

## 2. The frame loop

`PacmanApp.run()` does exactly this, in this order, once per frame:

1. **Drain the keyboard** — `gui.InputHandler.poll(world)`. Movement keys are
   written into `world.pacman.desired_direction`; everything else comes back as
   an `Intent` for the app to act on.
2. **Tick every behavior tree** — `behaviors.BrainPool.tick_all(dt)`. The
   ghosts decide.
3. **Advance the world** — `game.GameWorld.update(dt)`. Everyone moves, pellets
   are eaten, collisions resolve.
4. **Paint** — `gui.Renderer.draw(world, brains, …)`.

**Why step 2 sits before step 3.** A tree decides which tile to head for, and
the world then carries that decision out in the same frame. Reversing them
would make every ghost act on a frame-old view of the maze — exactly the kind
of one-frame lag that turns "the ghost cut me off" into "the ghost walked
through me".

Two more properties of the loop:

- The trees tick **only while the world is actually running** (`GamePhase.PLAYING`
  and not paused). A ghost frozen during READY or during the death animation
  must not be quietly committing to its next three tiles.
- `dt` is clamped to `MAX_FRAME_DT = 0.05`. A dragged window or a breakpoint
  produces a multi-second frame, and integrating that in one step teleports
  everyone through walls. Brief slow-motion beats a broken board. Under
  `--headless` the step is fixed at `1/fps` instead, so a smoke run is
  reproducible and machine-independent.

---

## 3. The decision-point contract

**This is the single handshake between the game and the trees.** Everything
else in `behaviors/` is built on it.

> `agent.direction is Direction.NONE` means *"parked on a tile centre, waiting
> for a controller to say where to go next."*

Three halves of one rule:

1. **The world clears it on arrival.** `Agent.advance()` sets
   `direction = Direction.NONE` the instant the agent lands on a tile centre,
   stashes the direction it came in on as `last_direction` (that is the
   reversal ghosts may not take), and carries the overshoot into `progress` so
   speed stays constant round corners. **Nothing else may clear `direction`.**
2. **`Agent.is_deciding` is the question.** It is just
   `not self.direction.is_moving`, and it is what every mover node checks
   before doing anything.
3. **Controllers answer through `world.request_direction(agent, direction)`.**
   It returns `False` when the agent is mid-tile or the move is blocked, and it
   is the only way in. The behavior-tree action nodes use it; so does
   `GameWorld._apply_pacman_input` for the keyboard. That is the whole
   interface the tree layer needs, which is why `game/world.py` never imports
   BTEng.

The door is the one asymmetry: `request_direction` passes
`through_door=agent.may_pass_door`, so a ghost crosses the ghost-house door
only while `LEAVING`, `EATEN` or `IN_HOUSE`, and Pac-Man never does.

Player input is deliberately a *request*, not a move: pressing "up" writes
`pacman.desired_direction`, and the world applies it at the next decision
point — falling back to `last_direction` when the turn is into a wall, which is
what makes holding a direction into a corner feel right.

---

## 4. The looping-tree pattern

BTEng ships **no `Repeat` decorator**, and a tree that returns `SUCCESS` or
`FAILURE` is finished. A ghost's brain must never be finished.

`AgentBrain.tick()` therefore does what `turtlesim_bteng`'s controller does:

```python
self.status = self.executor.tick_once()
if self.status is not NodeStatus.RUNNING:
    self.executor.reset_tree()      # every node → IDLE
    self.cycles += 1
```

**One completed tree = one tile of travel decided** — which is exactly the
arcade's own decision granularity. The XML root is just the `ReactiveFallback`;
there is no `Repeat` / `ForceSuccess` wrapper in any of the five files.

Supporting details, all in `behaviors/runner.py`:

- The executor runs in **manual mode**: `ExecutorConfig(tick_interval=0.0,
  halt_on_completion=False, thread_pool_size=0)` and one `tick_once()` per
  frame. The game already owns a clock — the frame — so
  `start_event_loop()` is not used. That keeps tree, physics and picture in
  lockstep and makes a run reproducible from `--seed`.
- `halt_on_completion=False` because *this runner* decides what "done" means; a
  halt between cycles would throw away the RUNNING mover mid-tile.
- `StepAction` is a `StatefulActionNode` that stays **RUNNING for the length of
  a whole tile**: `on_start` picks a direction and hands it to the world,
  `on_running` returns SUCCESS the frame the world clears `direction` (i.e. the
  agent arrived), `on_halted` drops the commitment so the next activation
  re-decides. Staying RUNNING is what makes the live viewer's state meaningful
  *and* what lets a higher-priority guard interrupt a ghost mid-corridor
  through `ReactiveFallback`.
- If `on_start` is called mid-tile it returns **RUNNING, not FAILURE** — a
  direction can only be taken at a tile centre, so it waits rather than
  collapsing the branch.

---

## 5. The blackboard contract

Source of truth: `pacman_bteng/behaviors/blackboard_keys.py`.

Each brain owns a **private** blackboard, and two different kinds of entry live
on it.

### Object references — seeded once, at brain construction

| Key | Type | Why |
|-----|------|-----|
| `agent` | `Agent` / `Ghost` | The node library reads the real game objects through this |
| `world` | `GameWorld` | A maze lookup or a BFS is not something you want to squeeze through a scalar port |

`GameNode.world` / `GameNode.agent` are properties that read these and raise a
readable `RuntimeError` if a tree was built outside `AgentBrain`.

### Target ports — written by selectors, read by movers

| Key | Written by | Read by |
|-----|-----------|---------|
| `target_x`, `target_y` | every `Set*Target` node (as **output** ports) | `StepTowardTarget` (as **input** ports) |

### Scalar mirrors — refreshed every tick by `K.sync()`

`agent_name`, `agent_state`, `agent_x`, `agent_y`, `agent_direction`,
`pacman_x`, `pacman_y`, `distance_to_pacman`, `mode`, `power_remaining`,
`pellets_left`, `level`.

**Nothing in the node library needs them.** They exist for two reasons:

1. **They are what shows up in the ZMQ stream and the live viewers.** A
   mirrored key is the difference between "SelectTarget RUNNING" and actually
   being able to see *why*.
2. **They keep `ReactiveFallback` out of its skip-ahead fast path.** BTEng's
   `ReactiveFallback` subscribes to the blackboard and, when nothing is dirty,
   takes a fast path that resumes straight at the running child. Writing the
   mirrors every tick marks the board dirty every tick, so the fallback
   re-evaluates from the first child — which is what keeps the guards genuinely
   reactive.

`K.MIRRORED` is the display order the GUI panel and the viewers expect.

---

## 6. Introspection: one Inspector, five trees

`introspection/stream.py` builds **one** `Inspector.create()` for the whole
run, and `AgentBrain.__init__` calls `executor.set_inspector(...)` with it for
every brain. Two consequences worth knowing:

**Why one shared Inspector.** The viewers subscribe to a single ZMQ endpoint,
and a ghost's decision only makes sense next to what the other three were doing
at that instant. That is also why every node instance name in the XML carries
an `Agent/` prefix — `Blinky/Chase`, `Pacman/Evade`. It is what keeps one
stream readable, what `bt_zmq_viewer.py --filter blinky` keys off, and what
`bt_tree_viewer.py --agent` uses to decide which events are its own.
`AgentBrain._short()` strips the prefix again for the GUI panel.

**Why the Logger is wired by hand.** `TreeExecutor.set_logger()` subscribes the
logger to the inspector *per executor*. With five executors sharing one
inspector, every transition would be printed five times. So `BTIntrospection`
creates the `Logger` itself and subscribes a single relay callback to the
Inspector instead:

```python
self._logger_sub_id = self.inspector.subscribe(relay)
```

The sinks hanging off that one Inspector:

| Sink | Enabled by | Notes |
|------|-----------|-------|
| `ZmqPublisher` | `introspection.enable_zmq` / `--zmq-port` | Binds `tcp://*:<port>`, topic `bteng`. A missing `pyzmq` is a warning on the banner, never a failure to start. |
| Colored console `Logger` | `introspection.console_log` / `--log-console` | Very chatty — five trees. |
| JSON-lines file | `introspection.log_file` / `--log-file` | An unwritable path becomes a banner warning. |
| The GUI panel | `window.show_panel` / `TAB` | Reads the brain objects directly, not the Inspector. |

---

## 7. Data flow

```
                          ┌───────────────────────────────────────────┐
   keyboard ──────────────► gui.InputHandler                          │
                          │   movement → world.pacman.desired_direction
                          │   other    → Intent ──► app.PacmanApp     │
                          └───────────────────────────────────────────┘
                                            │
                                            ▼
   ┌───────────────────────────── app.PacmanApp._step(dt) ─────────────────────┐
   │                                                                           │
   │   1. behaviors.BrainPool.tick_all(dt)                                     │
   │        for each AgentBrain:                                               │
   │            K.sync(blackboard, agent, world)   ── mirrors, every tick       │
   │            executor.tick_once()                                           │
   │              ReactiveFallback                                             │
   │                ├── guards ....... read world via blackboard['world']      │
   │                ├── Set*Target ... set_output(target_x/target_y)           │
   │                └── StepTowardTarget                                       │
   │                        get_input(target_x/target_y)                       │
   │                        world.request_direction(agent, dir) ──┐            │
   │            terminal status → executor.reset_tree()           │            │
   │                                                              │            │
   │   2. game.GameWorld.update(dt)  ◄─────────────────────────────┘            │
   │        modes → house timers → agent.advance() → pellets → collisions      │
   │        Agent.advance() clears direction on arrival  (decision point)      │
   │        returns [GameEvent…]                                              │
   │                                                                           │
   │   3. gui.Renderer.draw(world, brains, debug, overlay)                     │
   └───────────────────────────────────────────────────────────────────────────┘
                                            │
                    every node transition    │
                                            ▼
                              introspection.Inspector  (one, shared)
                                 ├── ZmqPublisher  tcp://*:2667  ──► tools/bt_zmq_viewer.py
                                 │                                  tools/bt_tree_viewer.py
                                 ├── Logger console sink   (--log-console)
                                 └── Logger JSONL sink     (--log-file)
```

---

## 8. BTEng gotchas (do not deviate)

- **Static XML attributes arrive as strings.** `lead_tiles="4"` reads back as
  `"4"`, and `allow_reverse="true"` as `"true"`. Every numeric or boolean port
  goes through `as_float()` / `as_int()` / `as_bool()` in
  `behaviors/nodes.py`. Never `if self.get_input("allow_reverse")` — the string
  `"false"` is truthy.

- **An input port whose `default` is `None` is REQUIRED.** Validation rejects
  the tree with `required input port has no blackboard mapping or static value`
  unless the XML maps it or gives it a static value. Give every input a real
  default unless you genuinely want start-up to fail when the XML omits it.
  Every input in this library has one.

- **Every port used in the XML must be declared in `provided_ports()`** —
  `{key}` mapping or static value alike. This *is* checked, inside
  `TreeExecutor.set_tree()`, and the message names the class, the instance and
  the port:
  `[StepTowardTarget] 'Blinky/StepAway' / port 'speed': mapped as input but not declared in provided_ports()`.

- **`<TreeNodesModel>` directions override `provided_ports()` and are *not*
  validated.** The parser uses the model to route `attr="{key}"` into
  `input_ports` or `output_ports`; only when a node has no model entry does it
  fall back to the node's own declaration. So a wrong direction does not raise —
  it silently misroutes the mapping, and `set_output()` returns `False` or
  `get_input()` never sees the key. Keep the model in step with
  `provided_ports()` by hand: `Set*Target.target_x/target_y` are
  `<output_port>`, `StepTowardTarget.target_x/target_y` are `<input_port>`.

- **`--` is illegal inside an XML comment.** It is not a BTEng rule, it is XML,
  and it bit this project: the parse dies with
  `not well-formed (invalid token): line N, column M` before BTEng ever sees
  the file. Use a single `-` or an em dash in tree comments.

- **`Blackboard.create()` memoises by name.** Four ghosts asking for one would
  all share a single board and overwrite each other's `target_x` every tick, so
  `AgentBrain` builds a private `Blackboard(scope_name=f"bt.{self.name}")`
  instead — never `Blackboard.create()`.

- **Nodes are constructed with nothing but a name and a `NodeConfig`.** The XML
  parser builds them, so they cannot be handed the game objects; they pick
  `agent` and `world` up off the blackboard at tick time. Never touch the game
  in `__init__`.

- **`ReactiveFallback` re-ticks every guard on every tick.** That is what makes
  a power pellet, a mode switch or Pac-Man turning a corner interrupt a ghost
  in the same frame it happens — so conditions must stay cheap and free of side
  effects. The scalar mirrors (§5) are what keep the reactivity honest.

- **Node tag == class name.** `@register_node()` puts the class in BTEng's
  global `NodeFactory`; the XML addresses it as `<SetChaseTarget/>` with no
  `ID=` attribute. Importing `pacman_bteng.behaviors` is what runs the
  decorators, and `runner.py` imports `actions` and `conditions` itself so the
  registration always happens before any tree is parsed.

---

## 9. How to add a new BT node

1. **Pick a base** in `pacman_bteng/behaviors/nodes.py`:
   - `GameCondition` for a side-effect-free guard (implement `tick()`, return
     `self.verdict(ok, "why")`);
   - `TargetSelector` for a "where do I want to be" action (implement
     `choose() -> Optional[Tile]`; returning `None` fails the branch, and the
     base writes and clamps the target for you);
   - `StepAction` for a mover (implement `choose_direction() -> Direction`; the
     base owns the three-phase RUNNING-for-one-tile lifecycle);
   - `GameNode` + a raw BTEng base for anything that does not fit — `LeaveHouse`
     is the example, because it stays RUNNING across several tiles.
2. **Write it in `conditions.py` or `actions.py`** and decorate it with
   `@register_node()`. Put conditions in `conditions.py` and everything else in
   `actions.py` — those are the two modules `runner.py` imports for
   registration.
3. **Declare its ports in `provided_ports()`.** Chain `*super().provided_ports()`
   so you keep the base's ports (`target_x`/`target_y` for a selector,
   `allow_reverse` for a mover), and give **every input a non-`None` default**.
4. **Cast every input** with `as_float` / `as_int` / `as_bool` — XML gives you
   strings.
5. **Call `self.report("…")`** with a short human-readable reason. It reaches
   the Inspector record, the ZMQ `reason` field and, for ghosts, the GUI panel
   in one call.
6. **Use it in the XML** by tag name, with an agent-prefixed instance name:
   `<MyNode name="Blinky/DoThing" my_port="3"/>`.
7. **Update the `<TreeNodesModel>` block** of *every* XML that uses the node —
   add a `<Condition ID="MyNode">` or `<Action ID="MyNode">` entry listing each
   port as `<input_port name="…"/>` or `<output_port name="…"/>`, matching
   `provided_ports()` exactly. Nothing checks this for you (§8), so a mismatch
   is a silent bug, not a start-up error.
8. **Verify** with `python -m pacman_bteng --list-nodes` (the node and its ports
   must appear, with the defaults you expect) and then a real run:
   `python -m pacman_bteng --headless --max-seconds 20`.
