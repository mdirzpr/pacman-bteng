# Behavior Trees

Reference for the five XML trees in `bt_structures/` and for every node they
can use. For how the trees are ticked, see
[ARCHITECTURE.md](ARCHITECTURE.md); for running and watching them, see
[USAGE.md](USAGE.md).

Two things hold for all five files:

- The root is a bare `<ReactiveFallback>`. There is no `Repeat` wrapper — BTEng
  ships none, and `AgentBrain` resets the tree to IDLE and keeps ticking
  instead. **One completed tree = one tile of travel decided.**
- `ReactiveFallback` re-evaluates **every guard on every tick**, so a power
  pellet, a mode switch or Pac-Man rounding a corner interrupts a ghost in the
  frame it happens — even mid-corridor, because `StepTowardTarget` stays
  RUNNING for the length of a whole tile.
- Every instance `name=` is prefixed with its agent (`Blinky/Chase`). All five
  trees publish onto one ZMQ socket, and that prefix is what
  `bt_zmq_viewer.py --filter` and `bt_tree_viewer.py --agent` key off.

---

## 1. The four ghosts: one shape, one difference

All four ghost trees have the same six branches in the same order. Only branch
5 differs, and only by its target selector.

| # | Branch | Guard | Why it is at this priority |
|---|--------|-------|----------------------------|
| 1 | `ReturnHome` | `IsEaten` | Nothing else matters to a pair of eyes. Uses `pathfinder="bfs"` and `allow_reverse="true"`: getting home is navigation, not personality. `SetHouseTarget` **fails on arrival**, which is the handoff — the branch stops matching and the fallback drops straight into `ExitHouse` in the same tick. |
| 2 | `ExitHouse` | `IsInHouse` | A penned ghost cannot chase anything. `LeaveHouse` holds RUNNING through the release timer *and* the walk out, then hands the ghost back to the world. |
| 3 | `Flee` | `IsFrightened` | A power pellet outranks any chase plan; the ghost is edible and must run. |
| 4 | `Scatter` | `IsScatterMode` | The level's mode schedule says retreat; the corner is hard-coded per ghost. |
| 5 | `Chase` | *(none)* | The default, and **the branch that carries the personality**. |
| 6 | `Wander` | *(none)* | Safety net: a ghost walled in by the reversal ban keeps moving rather than standing in a corridor looking broken. |

```
ReactiveFallback  "<Ghost>/Brain"
├── Sequence  "<Ghost>/ReturnHome"
│     ├── IsEaten
│     ├── SetHouseTarget            → target_x/target_y   (FAILURE once home)
│     └── StepTowardTarget          pathfinder="bfs"  allow_reverse="true"
├── Sequence  "<Ghost>/ExitHouse"
│     ├── IsInHouse
│     └── LeaveHouse                RUNNING until it is out of the door
├── Sequence  "<Ghost>/Flee"
│     ├── IsFrightened
│     ├── SetFleeTarget             lookahead="10"
│     └── StepTowardTarget          pathfinder="greedy"
├── Sequence  "<Ghost>/Scatter"
│     ├── IsScatterMode
│     ├── SetScatterTarget          corner_x/corner_y
│     └── StepTowardTarget          (greedy, the default)
├── Sequence  "<Ghost>/Chase"
│     ├── ◄── THE ONE NODE THAT DIFFERS ──►
│     └── StepTowardTarget          (greedy, the default)
└── WanderStep  "<Ghost>/Wander"
```

The one node, per ghost:

| Ghost | File | Chase node as written in the XML | Scatter corner |
|-------|------|----------------------------------|----------------|
| Blinky | `ghost_blinky.xml` | `<SetChaseTarget name="Blinky/AimAtPacman"/>` | `corner_x="26" corner_y="1"` |
| Pinky | `ghost_pinky.xml` | `<SetAmbushTarget name="Pinky/AimAheadOfPacman" lead_tiles="4"/>` | `corner_x="1" corner_y="1"` |
| Inky | `ghost_inky.xml` | `<SetFlankTarget name="Inky/AimAcrossPacman" partner="blinky" lead_tiles="2"/>` | `corner_x="26" corner_y="29"` |
| Clyde | `ghost_clyde.xml` | `<SetShyTarget name="Clyde/ChaseOrBolt" flee_radius="8" corner_x="1" corner_y="29"/>` | `corner_x="1" corner_y="29"` |

All four `<TreeNodesModel>` blocks are identical and list every ghost node,
including `IsPacmanWithin` and the three selectors a given ghost does not use —
so you can paste any personality into any file without touching the model.

### 1.1 Blinky — `ghost_blinky.xml`

```
ReactiveFallback "Blinky/Brain"
├── Sequence "Blinky/ReturnHome"    IsEaten → SetHouseTarget → StepTowardTarget[bfs, reverse]
├── Sequence "Blinky/ExitHouse"     IsInHouse → LeaveHouse
├── Sequence "Blinky/Flee"          IsFrightened → SetFleeTarget[10] → StepTowardTarget[greedy]
├── Sequence "Blinky/Scatter"       IsScatterMode → SetScatterTarget[26,1] → StepTowardTarget
├── Sequence "Blinky/Chase"         SetChaseTarget → StepTowardTarget
└── WanderStep "Blinky/Wander"
```

Aims at Pac-Man's own tile, every decision. No lookahead, no cleverness — and
unbeatable down a straight corridor, because a greedy step toward where he is
standing never lets him gain ground.

### 1.2 Pinky — `ghost_pinky.xml`

```
ReactiveFallback "Pinky/Brain"
├── Sequence "Pinky/ReturnHome"     IsEaten → SetHouseTarget → StepTowardTarget[bfs, reverse]
├── Sequence "Pinky/ExitHouse"      IsInHouse → LeaveHouse
├── Sequence "Pinky/Flee"           IsFrightened → SetFleeTarget[10] → StepTowardTarget[greedy]
├── Sequence "Pinky/Scatter"        IsScatterMode → SetScatterTarget[1,1] → StepTowardTarget
├── Sequence "Pinky/Chase"          SetAmbushTarget[lead 4] → StepTowardTarget
└── WanderStep "Pinky/Wander"
```

Aims four tiles in *front* of Pac-Man rather than at him. Harmless alone;
together with Blinky pushing from behind it is a pincer, and it is why running
down a long corridor away from Blinky so often ends in Pinky's arms.

### 1.3 Inky — `ghost_inky.xml`

```
ReactiveFallback "Inky/Brain"
├── Sequence "Inky/ReturnHome"      IsEaten → SetHouseTarget → StepTowardTarget[bfs, reverse]
├── Sequence "Inky/ExitHouse"       IsInHouse → LeaveHouse
├── Sequence "Inky/Flee"            IsFrightened → SetFleeTarget[10] → StepTowardTarget[greedy]
├── Sequence "Inky/Scatter"         IsScatterMode → SetScatterTarget[26,29] → StepTowardTarget
├── Sequence "Inky/Chase"           SetFlankTarget[partner blinky, lead 2] → StepTowardTarget
└── WanderStep "Inky/Wander"
```

Takes the vector from Blinky to a point two tiles ahead of Pac-Man and
**doubles** it. The most interesting target in the game: while Blinky is far
away Inky drifts almost aimlessly, but the moment Blinky closes in, the doubled
vector lands on the far side of Pac-Man and the two of them shut a trap from
both ends. With no partner on the board he chases directly rather than freezing.

### 1.4 Clyde — `ghost_clyde.xml`

```
ReactiveFallback "Clyde/Brain"
├── Sequence "Clyde/ReturnHome"     IsEaten → SetHouseTarget → StepTowardTarget[bfs, reverse]
├── Sequence "Clyde/ExitHouse"      IsInHouse → LeaveHouse
├── Sequence "Clyde/Flee"           IsFrightened → SetFleeTarget[10] → StepTowardTarget[greedy]
├── Sequence "Clyde/Scatter"        IsScatterMode → SetScatterTarget[1,29] → StepTowardTarget
├── Sequence "Clyde/Chase"          SetShyTarget[radius 8, corner 1,29] → StepTowardTarget
└── WanderStep "Clyde/Wander"
```

Chases exactly like Blinky while further than eight tiles away, and bolts for
his own corner the moment he gets closer. He then leaves the radius, turns
around, comes back, and does it again — the oscillation is what reads as
cowardice rather than as a ghost that cannot aim. He is also why the
bottom-left of the maze is the safest place to stand.

---

## 2. Pac-Man's autopilot — `pacman_auto.xml`

The same node library pointed the other way. Loaded by `--auto`, or built on
demand the first time you press `F2`.

| # | Branch | Guard | Rationale |
|---|--------|-------|-----------|
| 1 | `Hunt` | `IsPowerActive` + `IsGhostWithin distance="14" metric="path" hostile_only="false" edible_only="true"` | A burning power pellet with prey in reach is the highest-value thing on the board. BFS, because a ghost is a moving target and a greedy step into a wall wastes the whole fright timer. |
| 2 | `Evade` | `IsGhostWithin distance="7" metric="path" hostile_only="true"` | Below Hunt on purpose: `Evade` only counts *hostile* ghosts, and while a pellet burns the dangerous ones are eaten or penned — so the two guards cannot both be true about the same ghost. Greedy step, because fleeing is about direction, not about a route. |
| 3 | `Collect` | `HasPelletsLeft` | The job. "Nothing is happening, so make progress." |
| 4 | `Wander` | *(none)* | Board cleared mid-frame, or genuinely boxed in. |

```
ReactiveFallback "Pacman/Brain"
├── Sequence "Pacman/Hunt"
│     ├── IsPowerActive
│     ├── IsGhostWithin        distance="14" metric="path"
│     │                        hostile_only="false" edible_only="true"
│     ├── SetHuntGhostTarget
│     └── StepTowardTarget     pathfinder="bfs"     allow_reverse="true"
├── Sequence "Pacman/Evade"
│     ├── IsGhostWithin        distance="7" metric="path" hostile_only="true"
│     ├── SetEscapeTarget      openness="0.6"
│     └── StepTowardTarget     pathfinder="greedy"  allow_reverse="true"
├── Sequence "Pacman/Collect"
│     ├── HasPelletsLeft
│     ├── SetNearestPelletTarget  candidates="12"
│     └── StepTowardTarget     pathfinder="bfs"     allow_reverse="true"
└── WanderStep "Pacman/Wander"  allow_reverse="true"
```

`allow_reverse="true"` everywhere: a player can turn on the spot, so an
autopilot that could not would be playing a harder game than the human it
replaces.

---

## 3. Node catalogue

Straight from `python -m pacman_bteng --list-nodes`. **A port that is not
listed here does not exist** — using one anyway fails tree validation at
start-up. Tag name == class name; no `ID=` attribute is needed on instance tags.

Remember that XML attributes arrive as **strings**: `"false"` is a truthy
string, and the node code casts every port through `as_float` / `as_int` /
`as_bool`.

### 3.1 Conditions

| Node | Port | Default | What it does |
|------|------|---------|--------------|
| `IsEaten` | — | — | SUCCESS while this ghost is a pair of eyes heading home. |
| `IsInHouse` | — | — | SUCCESS while the ghost is in the pen **or** walking out of it. Both states are one branch: they want the same exit behavior. |
| `IsFrightened` | — | — | SUCCESS while a power pellet has this ghost running scared. |
| `IsScatterMode` | — | — | SUCCESS during the scatter phases of the level's mode schedule. |
| `IsPowerActive` | — | — | SUCCESS while a power pellet is burning down. |
| `HasPelletsLeft` | — | — | SUCCESS while anything is still on the board (power pellets included). |
| `IsPacmanWithin` | `distance` | `8.0` | Trigger radius in tiles. |
| | `metric` | `"euclidean"` | `euclidean`, `manhattan` or `path` — see below. |
| `IsGhostWithin` | `distance` | `6.0` | Trigger radius in tiles, measured to the nearest ghost of the selected kind. |
| | `hostile_only` | `"true"` | Ignore edible and eaten ghosts — "am I in danger". |
| | `edible_only` | `"false"` | Only count ghosts that can be eaten — "is there a meal nearby". Checked first, so it wins over `hostile_only`. |
| | `metric` | `"euclidean"` | `euclidean`, `manhattan` or `path`. |
| | `hysteresis` | `0.0` | Once the guard fires it holds until the ghost is `distance + hysteresis` tiles away. Stops a ghost hovering at the edge from flipping the branch every tile. |

Both proximity conditions share one distance metric port:

| `metric` | Measures |
|----------|----------|
| `euclidean` (default) | Straight line — the arcade ghosts' own metric. Cheap, and wrong through a wall. |
| `manhattan` | Along the axes; a rough corridor proxy. |
| `path` | The honest one in a maze: a single BFS distance field from the agent's tile, so a ghost on the other side of a wall does not read as close. One BFS per tick; unreachable tiles score `999.0`. |

`IsPacmanWithin` is declared in all four ghost `<TreeNodesModel>` blocks but is
not used by any shipped branch — it is there for you to build a proximity guard
without touching the model.

### 3.2 Target selectors

Every selector below declares `target_x` and `target_y` as **output** ports
("Column/Row of the tile this agent is heading for"), writes the tile it picked,
and returns SUCCESS — or FAILURE, which fails its whole branch. Ports listed
are the extra ones.

**Ghost personalities**

| Node | Port | Default | What it does |
|------|------|---------|--------------|
| `SetChaseTarget` | — | — | **Blinky.** Aims straight at Pac-Man's tile. |
| `SetAmbushTarget` | `lead_tiles` | `4.0` | **Pinky.** Aims this far ahead of Pac-Man along his heading. `0` makes her a second Blinky. |
| `SetFlankTarget` | `partner` | `"blinky"` | **Inky.** Name of the ghost whose position anchors the vector. If that ghost is absent (or is this ghost), it chases Pac-Man directly instead of failing. |
| | `lead_tiles` | `2.0` | Pivot this far ahead of Pac-Man; the target is `2 × pivot − partner`. |
| `SetShyTarget` | `flee_radius` | `8.0` | **Clyde.** Closer than this to Pac-Man, retreat; further, chase. |
| | `corner_x` | `-1.0` | Retreat corner column. `-1` = the corner the world assigned this ghost. |
| | `corner_y` | `-1.0` | Retreat corner row. `-1` = the ghost's own. |

**Shared ghost behaviors**

| Node | Port | Default | What it does |
|------|------|---------|--------------|
| `SetScatterTarget` | `corner_x` | `-1.0` | Corner column; `-1` = the ghost's own (`rules.scatter_corners`). |
| | `corner_y` | `-1.0` | Corner row; `-1` = the ghost's own. |
| `SetFleeTarget` | `lookahead` | `10.0` | Projects an escape point this many tiles directly away from Pac-Man. If that lands on the current tile, it aims at the scatter corner instead. |
| `SetHouseTarget` | — | — | Aims at the middle of the ghost house. On arrival it calls back into the world to revive the ghost and returns **FAILURE** — that is the handoff to the `ExitHouse` branch, not an error. |

**Pac-Man's autopilot**

| Node | Port | Default | What it does |
|------|------|---------|--------------|
| `SetNearestPelletTarget` | `candidates` | `10.0` | Ranks this many nearest pellets by straight-line distance, then measures each with a BFS and picks the shortest *corridor* path. Straight-line nearest is wrong often enough to matter: the pellet across the wall is two tiles away and thirty tiles of walking. |
| `SetHuntGhostTarget` | — | — | Aims at the nearest edible ghost; FAILURE when nothing is edible. |
| `SetEscapeTarget` | `reverse_penalty` | `0.0` | Tiles of safety a U-turn must beat to win. With a ghost on each side two tiles score almost alike and the choice flips as they move; a small penalty (the autopilot uses `1.5`) keeps him going. |
| | `openness` | `0.6` | Scores each neighbouring tile as `distance to the closest hostile ghost + openness × number of exits`, and aims at the best. *All* hostile ghosts count — running from Blinky into Pinky is not an escape — and the exit bonus is what makes Pac-Man break for open ground instead of into a pocket. `openness="0"` gives the naive "straight away from him" behavior back, and he dies in corners. |

### 3.3 Movers

| Node | Port | Default | What it does |
|------|------|---------|--------------|
| `StepTowardTarget` | `target_x` | `0.0` | **Input.** Column to walk to — read back from what a selector wrote. |
| | `target_y` | `0.0` | **Input.** Row to walk to. |
| | `pathfinder` | `"greedy"` | `greedy` = the arcade rule: step to whichever legal neighbour is nearest the target as the crow flies, ties breaking UP, LEFT, DOWN, RIGHT. No memory, no lookahead — which is exactly why arcade ghosts can be out-manoeuvred around a corner. `bfs` = a real shortest path (falling back to greedy when the target is unreachable). Anything else silently means `greedy`. |
| | `allow_reverse` | `"false"` | Let the agent turn back onto the tile it came from. Ghosts normally may not; the world flips them itself on a mode change or a power pellet by rewriting `last_direction`. |
| `WanderStep` | `allow_reverse` | `"false"` | Take one tile in a random legal direction, using the world's seeded RNG. Two jobs: it is the authentic frightened-ghost behavior, and it is the safety net at the bottom of every tree. |
| `LeaveHouse` | — | — | Waits out the release timer (RUNNING), then BFS-walks to the tile just outside the door and hands the ghost back to the world as released. Deliberately **not** a `StepAction`: it stays RUNNING across several tiles, because "leaving the house" is one behavior from the tree's point of view. |

Both `StepTowardTarget` and `WanderStep` return **RUNNING for the whole tile**,
FAILURE when boxed in or the chosen direction is blocked, and SUCCESS the frame
the agent lands on the next tile centre. If they are activated mid-tile they
return RUNNING and retry, never FAILURE.

---

## 4. Tuning the ghosts

Every edit below is a one-line change to an XML file. Nothing needs
recompiling; restart the game and watch the result in `./run.sh tree <agent>`.

| Want | Edit | Effect |
|------|------|--------|
| Pinky easier to dodge | `ghost_pinky.xml`: `<SetAmbushTarget lead_tiles="8"/>` | She cuts the corner far earlier, commits to a junction you were never going to reach, and is trivially dodged. |
| Pinky as a second Blinky | `lead_tiles="0"` | Aiming "0 tiles ahead" is Pac-Man's own tile — she becomes a straight chaser. |
| Take away the bottom-left refuge | `ghost_clyde.xml`: `<SetShyTarget flee_radius="3"/>` | Clyde only panics when almost touching you, so his corner stops being a safe place to park. `flee_radius="20"` does the opposite: he spends the level cowering. |
| Re-anchor Inky | `ghost_inky.xml`: `<SetFlankTarget partner="clyde"/>` | The doubled vector now pivots off Clyde, who wanders far more than Blinky — Inky's target becomes wilder and much less coordinated. Any ghost name works; an absent one makes him chase directly. |
| Two ghosts flanking each other | Give Blinky `<SetFlankTarget partner="inky"/>` too | Both trees now flank off each other. Legal, and instructive to watch — the pair oscillates instead of converging. |
| The arcade's random frightened turns | In any ghost XML, replace the `Flee` branch's `<SetFleeTarget…/>` + `<StepTowardTarget…/>` with a single `<WanderStep name="Blinky/FleeRandom"/>` | The original arcade turns frightened ghosts at random rather than steering them away. `WanderStep` is already in every `<TreeNodesModel>`, so the model needs no edit. You lose the visible escape target in the debug overlay, which is why the projected point is the default. |
| Make a ghost cheat | Any chase branch: `<StepTowardTarget … pathfinder="bfs"/>` | The greedy arcade rule becomes a real shortest path. The ghost stops being out-manoeuvrable around corners and the game gets much harder — which is exactly why `greedy` is the default everywhere except the eyes going home. |
| Let a ghost double back | Add `allow_reverse="true"` to a chase `<StepTowardTarget/>` | It can turn around mid-corridor. Removes the single biggest way of escaping a ghost. |
| Ignore the assigned corner | `ghost_blinky.xml`: `<SetScatterTarget corner_x="13" corner_y="23"/>` | Scatter now retreats to that tile instead of the top-right. Drop both attributes entirely to use `rules.scatter_corners` from `config/game.yaml`. |
| A less suicidal autopilot | `pacman_auto.xml`: `<IsGhostWithin name="Pacman/ThreatNearby" distance="10" metric="path"/>` | He starts evading three tiles earlier. Raise `openness` on `SetEscapeTarget` at the same time to make him prefer junctions even more strongly. |
| A cheaper (and blinder) autopilot | Drop `metric="path"` from both `IsGhostWithin` guards | Back to straight-line distance: one BFS per tick saved, and a ghost on the other side of a wall now reads as close enough to panic about. |
| A greedier autopilot | `pacman_auto.xml`: `<SetNearestPelletTarget candidates="25"/>` | More pellets get a real BFS measurement each decision, so the route is better and each tick costs more. `candidates="1"` reverts to plain straight-line nearest. |

**If you add a port to any of these**, add it to that file's
`<TreeNodesModel>` too, with the direction that matches `provided_ports()`.
A port that is in the XML but not in `provided_ports()` fails validation at
start-up with a named error; a *direction* that disagrees is not checked and
fails silently. See
[ARCHITECTURE.md](ARCHITECTURE.md#bteng-gotchas-do-not-deviate).
