#!/usr/bin/env bash
################################################################################
# 🟡 run.sh — one-command runner for the Pac-Man BTEng game (the lazy path)
#
# This is a convenience wrapper, NOT a replacement for the documented workflow.
# Everything it does is the same handful of commands from README.md /
# docs/USAGE.md — python3 -m venv, pip install -r requirements.txt,
# python -m pacman_bteng, pytest — just checked, chained and defaulted for you.
#
# There is no Docker and no ROS 2 here: the whole project lives in one host
# virtualenv (./.venv), so the "build" step is `pip install` and the "test" step
# is `pytest`.
#
#   ./run.sh                 # venv → play (you drive Pac-Man)
#   ./run.sh auto            # Pac-Man drives himself, all five trees ticking
#   ./run.sh tree pinky      # live BT view in a second terminal
#   ./run.sh help            # all subcommands
#
# Nothing here is magic: run `./run.sh --dry-run <cmd>` to print the underlying
# commands instead of executing them.
################################################################################

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

VENV="$REPO_ROOT/.venv"
PY="$VENV/bin/python"
REQUIREMENTS="$REPO_ROOT/requirements.txt"
REQUIREMENTS_DEV="$REPO_ROOT/requirements-dev.txt"
REQ_STAMP="$VENV/.requirements.sha256"
REQ_DEV_STAMP="$VENV/.requirements-dev.sha256"
ZMQ_PORT=2667
AGENTS="blinky pinky inky clyde pacman"
DRY_RUN=0

# ─── pretty output ────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'
  BLUE=$'\033[0;34m'; BOLD=$'\033[1m'; NC=$'\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; BLUE=''; BOLD=''; NC=''
fi

info()  { echo -e "${BLUE}ℹ️  $*${NC}"; }
ok()    { echo -e "${GREEN}✅ $*${NC}"; }
warn()  { echo -e "${YELLOW}⚠️  $*${NC}" >&2; }
die()   { echo -e "${RED}❌ $*${NC}" >&2; exit 1; }
step()  { echo; echo -e "${BOLD}${BLUE}━━ $* ━━${NC}"; }

# Echo a command (shell-quoted, so it is copy-pasteable), then run it — or only
# echo it under --dry-run.
run() {
  local arg shown=()
  for arg in "$@"; do
    [[ "$arg" == *[[:space:]]* ]] && shown+=("\"$arg\"") || shown+=("$arg")
  done
  echo -e "${BOLD}\$ ${shown[*]}${NC}"
  [[ $DRY_RUN -eq 1 ]] && return 0
  "$@"
}

# ─── prerequisite checks ──────────────────────────────────────────────────────
check_python() {
  command -v python3 >/dev/null 2>&1 \
    || die "python3 not found. Install Python 3.10+ — https://www.python.org/downloads/"
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || die "Python 3.10+ is required (found $(python3 --version 2>&1))."
}

# Debian/Ubuntu ship venv as a separate package, and the failure is confusing.
# $1 = "report" to only report the state (doctor), anything else to be fatal.
check_venv_module() {
  local mode="${1:-fix}"
  if python3 -m venv --help >/dev/null 2>&1; then
    return 0
  fi
  if [[ "$mode" == "report" ]]; then
    warn "python3 cannot create virtualenvs. On Debian/Ubuntu: sudo apt install python3-venv"
    return 0
  fi
  die "python3 -m venv is unavailable. On Debian/Ubuntu: sudo apt install python3-venv"
}

# pygame needs somewhere to draw. Missing a display is a warning, never fatal:
# the game runs fine headless, it just has no window.
# $1 = "report" for doctor phrasing.
check_gui() {
  if [[ -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]]; then
    ok "GUI available (DISPLAY=${DISPLAY:-–} WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-–})"
    return 0
  fi
  warn "Neither DISPLAY nor WAYLAND_DISPLAY is set — no window will open."
  warn "Run with --headless to play without a display (the BT stream still works)."
}

# 0 = listening, 1 = free, 2 = could not tell (no ss, no lsof).
port_in_use() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltnH 2>/dev/null | grep -Eq "[:.]${port}[[:space:]]"
  elif command -v lsof >/dev/null 2>&1; then
    lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
  else
    return 2
  fi
}

# ─── host virtualenv ──────────────────────────────────────────────────────────
# Reinstall only when the requirements file changed since the last install — the
# sha256 of the file is stamped inside .venv and compared on every run. A venv
# that predates this script simply has no stamp, so it installs once (harmless).
ensure_requirements() {
  local req="$1" stamp="$2" label="$3"
  local want have=""
  want="$(sha256sum "$req" | cut -d' ' -f1)"
  [[ -f "$stamp" ]] && have="$(cat "$stamp")"
  if [[ "$want" != "$have" ]]; then
    step "Installing $label ($(basename "$req"))"
    run "$PY" -m pip install --quiet -r "$req"
    [[ $DRY_RUN -eq 1 ]] || echo "$want" > "$stamp"
    ok "Installed: $(grep -v '^\s*#' "$req" | grep -v '^\s*$' | awk '{print $1}' | tr '\n' ' ')"
  else
    ok "$label already satisfied (.venv)"
  fi
}

ensure_venv() {
  if [[ ! -x "$PY" ]]; then
    step "Creating the virtualenv (.venv)"
    check_python
    check_venv_module
    if ! run python3 -m venv "$VENV"; then
      die "Failed to create .venv. On Debian/Ubuntu: sudo apt install python3-venv"
    fi
    [[ $DRY_RUN -eq 1 ]] || run "$PY" -m pip install --quiet --upgrade pip
    ok "Created $VENV"
  fi
  ensure_requirements "$REQUIREMENTS" "$REQ_STAMP" "Runtime requirements"
}

ensure_dev_venv() {
  ensure_venv
  ensure_requirements "$REQUIREMENTS_DEV" "$REQ_DEV_STAMP" "Dev requirements"
}

# ─── subcommands ──────────────────────────────────────────────────────────────

# Launch the game. Every argument is passed straight through to the CLI, so
# `./run.sh play --level 3 --seed 7 --no-panel` behaves exactly as documented.
cmd_play() {
  ensure_venv
  check_gui
  step "Starting Pac-Man"
  info "Live BT stream on tcp://localhost:$ZMQ_PORT — run './run.sh tree' in another terminal."
  info "Arrows/WASD to move, P to pause, Q or Ctrl-C to quit."
  run "$PY" -m pacman_bteng "$@"
}

# Same, with the autopilot tree driving Pac-Man too (five trees on one stream).
cmd_auto() {
  cmd_play --auto "$@"
}

cmd_setup() {
  step "Setting up the virtualenv"
  ensure_venv
  echo
  ok "Ready. Run './run.sh' to play, or './run.sh auto' to watch."
}

cmd_test() {
  ensure_dev_venv
  step "Running the test suite (pytest -q)"
  run "$PY" -m pytest -q "$@"
}

# Bare agent names are shorthand for --agent <name>; everything else goes to the
# viewer untouched (e.g. --port 3000, --xml some/tree.xml).
cmd_tree() {
  ensure_venv
  local args=() arg
  for arg in "$@"; do
    case " $AGENTS " in
      *" $arg "*) args+=(--agent "$arg") ;;
      *)          args+=("$arg") ;;
    esac
  done
  step "BTEng live tree view (Ctrl-C to quit)"
  run "$PY" tools/bt_tree_viewer.py "${args[@]+"${args[@]}"}"
}

cmd_viewer() {
  ensure_venv
  step "BTEng tick log (Ctrl-C to quit)"
  run "$PY" tools/bt_zmq_viewer.py "$@"
}

cmd_nodes() {
  ensure_venv
  step "Registered behavior-tree nodes"
  run "$PY" -m pacman_bteng --list-nodes
}

# Regenerate docs/images/. Headless and seeded, so the images move when the
# rendering moves and not because a different run happened to be on screen.
cmd_shots() {
  ensure_venv
  step "Regenerating the screenshots in docs/images/"
  run "$PY" tools/shots.py "$@"
}

cmd_doctor() {
  step "Checking prerequisites"
  if command -v python3 >/dev/null 2>&1; then
    ok "python3 present ($(python3 --version 2>&1))"
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
      || warn "Python 3.10+ is required — this interpreter is older."
  else
    warn "python3 not found — nothing in this project can run without it."
  fi
  check_venv_module report

  if [[ -x "$PY" ]]; then
    ok ".venv present ($("$PY" --version 2>&1))"
    local want have=""
    want="$(sha256sum "$REQUIREMENTS" | cut -d' ' -f1)"
    [[ -f "$REQ_STAMP" ]] && have="$(cat "$REQ_STAMP")"
    if [[ "$want" == "$have" ]]; then
      ok "requirements.txt satisfied (stamp matches)"
    elif [[ -z "$have" ]]; then
      warn "No install stamp in .venv — the next run does one pip install (harmless)."
    else
      warn "requirements.txt changed since the last install — the next run reinstalls."
    fi
    "$PY" -c 'import bteng' 2>/dev/null && ok "bteng importable" \
      || warn "bteng not importable in .venv — './run.sh setup' installs it."
    PYGAME_HIDE_SUPPORT_PROMPT=1 "$PY" -c 'import pygame' >/dev/null 2>&1 && ok "pygame importable" \
      || warn "pygame not importable in .venv — './run.sh setup' installs it."
  else
    warn ".venv missing — './run.sh setup' creates it."
  fi

  check_gui report

  if port_in_use "$ZMQ_PORT"; then
    warn "TCP port $ZMQ_PORT is already in use — another game is running, or pass --zmq-port N."
  else
    case $? in
      1) ok "TCP port $ZMQ_PORT free for the BT stream" ;;
      *) info "Could not check port $ZMQ_PORT (no 'ss' and no 'lsof' on PATH)." ;;
    esac
  fi

  echo
  info "Nothing above is fatal except a missing python3 — everything else './run.sh setup' fixes."
}

cmd_clean() {
  step "Cleaning Python caches"
  run bash -c "find '$REPO_ROOT' -path '$VENV' -prune -o -type d -name __pycache__ -print0 | xargs -0r rm -rf"
  run rm -rf "$REPO_ROOT/.pytest_cache"
  ok "Cleaned — __pycache__ and .pytest_cache are gone."
  info "The virtualenv is untouched on purpose (rm -rf .venv to drop it, then './run.sh setup')."
}

cmd_help() {
  cat <<EOF
${BOLD}🟡 run.sh — one-command runner for the Pac-Man BTEng game${NC}

${BOLD}USAGE${NC}
  ./run.sh [--dry-run] [command] [args…]

${BOLD}COMMANDS${NC}
  ${GREEN}play${NC} [args…]         Ensure the venv, then start the game (default command).
                         You drive Pac-Man; the four ghosts are behavior trees.
                         Any argument is passed straight to python -m pacman_bteng.
  ${GREEN}auto${NC} [args…]         Same, plus --auto: Pac-Man plays himself from a tree too.
  ${GREEN}deluxe${NC} [args…]       Play with the deluxe visuals: neon bloom, particles,
                         score popups and CRT scanlines. ${GREEN}lite${NC} forces the
                         clean, cheap default back on. F3 switches in game.
  ${GREEN}demo${NC}                 Alias of 'auto' — the hands-off way to show the project off.
  ${GREEN}setup${NC}                Create .venv and install requirements.txt.
  ${GREEN}test${NC} [args…]         Install requirements-dev.txt, then run pytest -q.
  ${GREEN}tree${NC} [agent|args…]   Live visual BT viewer (tools/bt_tree_viewer.py).
                         A bare blinky/pinky/inky/clyde/pacman means --agent <name>.
  ${GREEN}viewer${NC} [args…]       Flat live tick log (tools/bt_zmq_viewer.py).
  ${GREEN}nodes${NC}                List the registered BT node types and their ports.
  ${GREEN}shots${NC} [name…]        Regenerate docs/images/ headlessly (lite, deluxe,
                         debug, panel). Seeded, so the images change only when
                         the rendering does.
  ${GREEN}doctor${NC}               Check python3, venv, requirements, display and port
                         $ZMQ_PORT — changes nothing.
  ${GREEN}clean${NC}                Remove __pycache__ / .pytest_cache (the .venv is kept).
  ${GREEN}help${NC}                 This message.

${BOLD}OPTIONS${NC}
  --dry-run              Print the underlying commands instead of running them.

${BOLD}EXAMPLES${NC}
  ./run.sh                              # play with the four classic ghosts
  ./run.sh auto --seed 7                # watch a reproducible self-playing run
  ./run.sh deluxe                       # the pretty one
  ./run.sh deluxe --auto --loop         # unattended neon demo
  ./run.sh play --level 3 --ghosts blinky,pinky
  ./run.sh play --headless --max-seconds 30
  ./run.sh tree clyde                   # in a second terminal, while the game runs
  ./run.sh viewer --filter Blinky --status RUNNING

${BOLD}NOTE${NC}
  This wrapper only chains the documented commands (python3 -m venv .venv,
  pip install -r requirements.txt, python -m pacman_bteng …, pytest). The manual
  workflow in README.md / docs/USAGE.md remains fully supported — use it whenever
  you want control over the individual steps.
EOF
}

# ─── argument dispatch ────────────────────────────────────────────────────────
main() {
  while [[ ${1:-} == --* ]]; do
    case "$1" in
      --dry-run) DRY_RUN=1; shift ;;
      --help) cmd_help; return 0 ;;
      *) break ;;   # anything else is a flag for the default 'play' command
    esac
  done

  local cmd="${1:-play}"
  case "$cmd" in
    --*) cmd="play" ;;             # ./run.sh --level 3  → play --level 3
    *) (( $# > 0 )) && shift ;;    # remaining "$@" are the subcommand's own args
  esac

  case "$cmd" in
    play|game)            cmd_play "$@" ;;
    auto|demo)            cmd_auto "$@" ;;
    deluxe|fancy)         cmd_play --visuals deluxe "$@" ;;
    lite)                 cmd_play --visuals lite "$@" ;;
    setup|install)        cmd_setup ;;
    test|tests)           cmd_test "$@" ;;
    tree|watch)           cmd_tree "$@" ;;
    viewer|log)           cmd_viewer "$@" ;;
    nodes|list-nodes)     cmd_nodes ;;
    shots|screenshots)    cmd_shots "$@" ;;
    doctor|check)         cmd_doctor ;;
    clean)                cmd_clean ;;
    help|-h|--help)       cmd_help ;;
    *) die "Unknown command: $cmd  (see ./run.sh help)" ;;
  esac
}

main "$@"
