# GUI guideline

Notes taken from the reference video `~/Downloads/dashboard1.mp4` ("Glassy Dashboard V5", @code.xr, ~10 s, portrait, no audio), and what was done with it in this project.

> Caveat: the dashboard is small in the frame and the video is compressed, so these are observations of the *look*. The overlaid code snippet is a React/Tailwind/framer-motion stat card (`glass-card p-6 rounded-2xl`, `whileHover={{ y: -5 }}`, trend arrow in emerald). It is not usable here, since this project is pygame.

## What the video shows

A "spatial" / visionOS-style dashboard:

1. **Blurred photo backdrop** (a room with a big window) with the UI floating over it. Everything else is translucent.
2. **Frosted-glass cards**: translucent fill, soft blur of what is behind, a thin light border, large corner radius, subtle top highlight.
3. **Floating pill top nav** (Home / Projects / Invoices / Settings) centred, with round icon buttons at the left and right corners (search, bell with a red badge, avatar with a green presence dot).
4. **Floating vertical icon dock** on the left. The active item is a solid white circle; a small tooltip label appears beside it.
5. **Tinted cards**: the "Saved" screen uses cards with blue / amber / violet / green / red gradient tints, each with a small tag chip and an icon.
6. **Stat cards**: big number, small label, trend arrow with a percentage, a sparkline.
7. **Charts**: bars in muted glass, with *one* bar highlighted in saturated blue. Small ring gauge for a goal (e.g. 80%).
8. **Messaging panel**: list on the left, bubbles on the right, own messages in blue.
9. **Motion**: the dashboard fades in from a blur; cards lift slightly on hover; screens cross-fade when the dock selection changes.

## Is it useful for this project?

Partly. The look suits the side panel. The structure suits a *dashboard* app, and the game is not one.

| Idea | Verdict | Why |
|---|---|---|
| Frosted cards with top highlight and tint | **Done** | The agent cards were flat rectangles; glass gives depth and ties each card to its ghost colour. |
| One highlighted element among muted ones (the blue bar) | **Already used, keep** | The proximity strip and the status chip follow the same rule: only the live thing is saturated. |
| Ring gauge for a goal | **Done** | The power-pellet timer is a ring in the mode pill (see below). Pellets-left ring is in the top bar as well. |
| Floating pill nav and icon dock | **Done** | Tab dock at the top of the side panel: TREES / STATS / LOG (see below). |
| Blurred photo backdrop | **No** | Competes with the maze. The maze must be the highest-contrast thing on screen. Real blur is also costly per frame in pygame. |
| Hover lift | **Done, repurposed** | Driven by ghost proximity instead of a mouse (see below). Tab cross-fade: see below. |
| Messaging UI, avatars, invoices | No | No equivalent in a game. |

## Implemented

`pacman_bteng/gui/hud.py`: `Hud._glass()`, used by `_draw_agent_card()`.

- Vertical sheen: top of the card is about 9% lighter than the bottom.
- Colour wash: the agent's own colour fades in over the left half (max alpha 46/255), so the card matches its ghost.
- 1 px bright top edge, inset by the corner radius.
- Rounded mask applied once with `BLEND_RGBA_MIN`.
- **Cached per (width, height, tint)**, so a frame is one blit per card. This follows the HUD rule "nothing costs a Surface per frame".
- Card geometry is unchanged, so F3 (lite/deluxe) still never reflows the HUD.

Gotcha found while doing it: `pygame.draw.line` on an `SRCALPHA` surface *overwrites* pixels instead of blending. A tint drawn that way leaves a hard seam. Draw the tint on a separate layer and `blit` it.

### Power-timer ring

`Hud._ring()`, drawn by `_mode_pill()` in place of the dot while a power pellet burns.

- Dim track plus a bright arc that starts at 12 o'clock and unwinds as time runs out. A near-empty pellet keeps a short stub.
- The arc is a polyline with round caps. `pygame.draw.arc` with a thick stroke breaks into gaps.
- The ring and the mode dot share one slot, so the pill is the same width in every mode and nothing shifts when the pellet ends.
- It replaces the old draining fill. Two meters in one pill said the same thing twice, and the ring does it with less ink.

### Score sparkline

`Hud._sample_score()` and `Hud._sparkline()`, in the top bar between LEVEL and the mode pill.

- Plots **points gained per second** over the last 30 s, not the cumulative score. The cumulative score only climbs, so its line says nothing.
- Newest sample is pinned to the right edge and history grows leftward, so a fresh game shows a short line at the right, not a stretched one.
- Trend arrow compares the last third of the window with the third before it: green up, red down, flat dash when equal. It is a triangle drawn with polygons, not a font glyph, which may be missing. Direction is also carried by shape, so it is not red-vs-green only.
- Sampled on `world.elapsed`, so a pause freezes the graph. A restart (elapsed going backwards) clears it.
- It takes whatever room is left after the pill (max 120 px) and is dropped below 48 px, so a narrow window never overlaps the scoreboard.
- History is draw-layer state inside `Hud`; the game and behavior layers do not know about it.

### Tab dock

`Hud._panel_heading()` draws the dock, `tab_at()` hit-tests it, `draw_panel(tab=...)` picks the view.

- One rounded pill, three segments. The active tab is a filled accent segment with dark text; the others are dim text. The current view is a shape, not only a colour.
- Views: **TREES** (the agent cards, unchanged), **STATS** (six glass tiles plus a board-cleared meter), **LOG** (newest game event first; pellets are skipped because there are hundreds per level).
- Keys `1` `2` `3`, or click the dock. The click goes through `InputHandler.poll(hit_test=...)`, so `input.py` never learns where the dock is.
- The selected tab lives on `PacmanApp.panel_tab`, not on the Hud, so it survives the renderer rebuild that TAB and F3 cause. The LOG history and sparkline history do not survive it.
- The footer (phase, mode, pellets, elapsed) is shown on every tab.
- The glass tile from `_glass()` is reused for the STATS tiles, so a new view costs no new styling.

### Proximity lift

`Hud._card_lift()` and `Hud._lift_shadow()`, called per card from `_trees_view()`.

- A **hostile** ghost's card rises as it closes on Pac-Man: it starts around 10 tiles away and is fully raised at about 4. It uses the same proximity as the card's strip, but it reaches the eye without reading.
- Rise is at most `min(4, pad // 2)` px, so it never touches the neighbouring card, plus a dark slab underneath so it reads as lifted rather than moved.
- Smoothstep on the distance, then DELUXE eases toward it (about 9 per second). LITE, the calm preset, moves in one step with no animation.
- Edible, parked and eaten ghosts never lift, and neither does Pac-Man's card. A lift that fires for a harmless ghost is a false alarm.
- Eased against `world.elapsed`, so a pause holds the lift where it is. The step is clamped to 0.1 s so a stall does not snap it.
- Everything the card draws moves with it, so nothing inside the card has to know it is raised.

### Tab cross-fade

`Hud._snapshot()` and `Hud._cross_fade()`, driven from `draw_panel()`.

- On a tab change the **outgoing view is drawn once onto a scratch surface** and kept as a still. For the next 150 ms that still is blitted over the live new view with falling alpha, so the result is a true blend of the two.
- Only the body fades. The dock, its highlight and the footer switch instantly, so the control answers the click at once and only the content eases.
- The views do not know they are being photographed. They draw at the same coordinates onto any surface. The lift easing is saved and restored around the snapshot so the old view does not step it twice.
- Timed on the **wall clock**, not game time: tabs are usually switched while paused, and a fade on game time would freeze half-way.
- DELUXE only. LITE is the calm preset and switches tabs in one frame.
- The fade region starts 2 px in from the panel edge, because the scratch surface has no left border and would dim the real one.
- Cost is one panel-sized surface per tab change, none per frame otherwise.

### Pellets-left ring

`Hud._pellet_ring()`, in the top bar between LEVEL and the sparkline, as a ring plus `LEFT 191`.

- Same `_ring()` as the power timer, so the two gauges read alike: the arc is what is **left** and it unwinds toward the end of the level.
- Warm-white (the pellet colour), so the power ring's yellow stays reserved for "a power pellet is burning". Two rings, two meanings, two colours.
- Radius is 30% of the bar height with a 6 px floor, so it scales with the window.
- It sits before the sparkline, which takes whatever room remains and is the first thing dropped in a narrow window.
- The STATS tab keeps its linear BOARD CLEARED bar; that is progress made, the ring is work remaining.

## Guidelines to reuse

**Surfaces**
- Build glass as: base colour, plus a vertical gradient (lighter on top), plus a low-alpha tint, plus a 1 px top highlight, plus a rounded mask. Keep the tint alpha at or below about 20% so text contrast holds.
- Corner radius is one value across the UI (`self.gap` here). Mixed radii look accidental.
- Borders are low-contrast and light. Never a heavy dark outline.

**Colour**
- Neutral and muted by default, one saturated accent per card. The saturated element is the one the user must read.
- Tint by *meaning* (here: the ghost's colour), not by decoration.
- Keep the status colours spread across lightness as well as hue (see `theme.py`). Do not rely on red vs green alone.

**Hierarchy**
- One hero number, the rest quieter (the score already does this).
- Show the active item as a filled shape and everything else as an outline or dim.

**Layout**
- Floating elements need breathing room. Use a single spacing unit (`Hud.pad`, `Hud.gap`) and nothing else.
- Group related controls into one pill or one dock, not scattered buttons.

**Motion** (only if added)
- 150–250 ms ease-out (the tab fade uses 150 ms). Lift 2–5 px, never more.
- Animate in the draw layer with its own `dt`. Do not put animation state in the game or behavior layers (import rule).
- Respect LITE: it is the calm / reduced-motion look, so motion is DELUXE-only.

**Performance (pygame)**
- Cache every gradient, mask, glow and text surface. Allocate on size or colour change, never per frame.
- No real-time blur. If a blurred backdrop is ever wanted, blur one pre-rendered frame once (`smoothscale` down then up) and reuse it.

**Contrast**
- Text over glass must still meet WCAG AA against the *lightest* part of the card, which is the top-left tint, not the average.

## Suggested next steps (not done)

1. Keep the LOG and sparkline history across the renderer rebuild (move it to the app, as `panel_tab` is).
