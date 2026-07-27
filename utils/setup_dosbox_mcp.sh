#!/bin/bash
# Downloads, builds, and registers the DOSBox-X + dosbox-mcp toolchain used
# for automated in-game QA of the translation (see README.md's "Setting up
# DOSBox-X" section and CLAUDE.md's dosbox-mcp notes for the full picture).
#
# This is a per-machine dev-tool bootstrap, not part of the build pipeline —
# nothing under utils/ depends on it. Safe to re-run; every step is
# idempotent (skips/updates rather than redoing work from scratch). Works on
# Linux (apt) and macOS/arm64 or x86_64 (Homebrew) — see the OS branches
# below for what differs.
#
# What it sets up, and why it isn't just "clone upstream":
#   - lokkju/dosbox-x-remotedebug is the GDB/QMP-automatable DOSBox-X fork
#     dosbox-mcp drives. We build from https://github.com/anetanel/dosbox-x-remotedebug
#     (a personal fork) on branch fix-gdb-breakpoint-address-decomposition,
#     which is upstream's `remotedebug` branch plus one fix not yet merged
#     upstream (GDB Z0/z0 breakpoints used seg:off instead of a linear
#     address). Upstream's `remotedebug` branch also ships with a
#     poll-based mitigation for a `handle_screendump` race (intermittent
#     "no file created" errors) that we validated as good enough in
#     practice — the more thorough root-cause fix for that race,
#     lokkju/dosbox-x-remotedebug#3, is a separate, still-unmerged PR; see
#     CLAUDE.md's dosbox-mcp section for the full history if that race
#     resurfaces.
#   - jdmichaud/dosbox-mcp is used unmodified straight from upstream.
#   - install.py (from the dosbox-mcp clone) registers the MCP server, but
#     doesn't set XMODIFIERS — this script patches that in afterward, on
#     Linux only. It's required on desktops with ibus (XMODIFIERS=@im=ibus):
#     this SDL1 build segfaults on window init otherwise. Not applicable on
#     macOS (no X11/ibus), so that step is skipped there.
#
# Env overrides:
#   DOSBOX_TOOLS_DIR         install location (default: ~/dosbox-mcp-tools)
#   DOSBOX_REMOTEDEBUG_REPO  git URL for the dosbox-x-remotedebug fork
#   DOSBOX_REMOTEDEBUG_BRANCH branch to build
#   DOSBOX_MCP_REPO          git URL for dosbox-mcp
#
# Flags:
#   --yes         don't prompt (package install, Homebrew install,
#                 install.py); for CI/unattended use
#   --skip-deps   don't touch system packages at all (apt/Homebrew)
#   --rebuild     rebuild dosbox-x-remotedebug even if the binary exists

set -euo pipefail

TOOLS_DIR="${DOSBOX_TOOLS_DIR:-$HOME/dosbox-mcp-tools}"
REMOTEDEBUG_REPO="${DOSBOX_REMOTEDEBUG_REPO:-https://github.com/anetanel/dosbox-x-remotedebug.git}"
REMOTEDEBUG_BRANCH="${DOSBOX_REMOTEDEBUG_BRANCH:-fix-gdb-breakpoint-address-decomposition}"
MCP_REPO="${DOSBOX_MCP_REPO:-https://github.com/jdmichaud/dosbox-mcp.git}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OS_NAME="$(uname -s)"

ASSUME_YES=0
SKIP_DEPS=0
REBUILD=0
for arg in "$@"; do
    case "$arg" in
        --yes) ASSUME_YES=1 ;;
        --skip-deps|--skip-apt) SKIP_DEPS=1 ;;
        --rebuild) REBUILD=1 ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

REMOTEDEBUG_DIR="$TOOLS_DIR/dosbox-x-remotedebug"
MCP_DIR="$TOOLS_DIR/dosbox-mcp"
CONF_PATH="$TOOLS_DIR/dune-dosbox.conf"

log()  { printf '\n== %s ==\n' "$1"; }
info() { printf '   %s\n' "$1"; }

confirm() {
    # confirm "prompt text" -> 0 (yes) or 1 (no). Always yes under --yes.
    [ "$ASSUME_YES" -eq 1 ] && return 0
    local ans
    read -r -p "     $1 [Y/n] " ans
    [[ -z "$ans" || "$ans" =~ ^[Yy] ]]
}

mkdir -p "$TOOLS_DIR"

# --- 1. System build dependencies ---------------------------------------
if [ "$SKIP_DEPS" -eq 1 ]; then
    log "Skipping system dependency check (--skip-deps)"
elif [ "$OS_NAME" = "Linux" ]; then
    log "Checking system build dependencies (apt)"
    APT_PACKAGES=(
        git automake autoconf libtool pkg-config
        gcc g++ make libncurses-dev nasm
        libsdl-net1.2-dev libsdl2-net-dev libpcap-dev libslirp-dev
        fluidsynth libfluidsynth-dev
        libavdevice-dev libavformat-dev libavcodec-dev libavcodec-extra
        libswscale-dev libfreetype-dev libxkbfile-dev libxrandr-dev
    )
    MISSING=()
    for pkg in "${APT_PACKAGES[@]}"; do
        dpkg -s "$pkg" >/dev/null 2>&1 || MISSING+=("$pkg")
    done
    if [ "${#MISSING[@]}" -eq 0 ]; then
        info "all required apt packages already installed"
    else
        info "missing: ${MISSING[*]}"
        if confirm "install these with sudo apt-get?"; then
            sudo apt-get update
            sudo apt-get install -y "${MISSING[@]}"
        else
            info "skipping — build will likely fail if these are truly missing"
        fi
    fi
elif [ "$OS_NAME" = "Darwin" ]; then
    log "Checking system build dependencies (Homebrew)"

    if ! xcode-select -p >/dev/null 2>&1; then
        echo "Xcode Command Line Tools are required (git, clang, make)." >&2
        echo "Run: xcode-select --install" >&2
        echo "...then re-run this script." >&2
        exit 1
    fi

    # Homebrew lives at /opt/homebrew on Apple Silicon, /usr/local on Intel;
    # a fresh install in this same script run won't be on PATH yet either.
    if ! command -v brew >/dev/null 2>&1; then
        for cand in /opt/homebrew/bin/brew /usr/local/bin/brew; do
            [ -x "$cand" ] && eval "$("$cand" shellenv)" && break
        done
    fi

    if ! command -v brew >/dev/null 2>&1; then
        info "Homebrew not found"
        if confirm "install Homebrew now?"; then
            NONINTERACTIVE=1 /bin/bash -c \
                "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
            for cand in /opt/homebrew/bin/brew /usr/local/bin/brew; do
                [ -x "$cand" ] && eval "$("$cand" shellenv)" && break
            done
        else
            info "skipping — build will fail without Homebrew-installed dependencies"
        fi
    else
        info "found: $(command -v brew)"
    fi

    if command -v brew >/dev/null 2>&1; then
        # Matches BUILD.md's macOS SDL1 list (glfw/glew/sdl2_net are
        # SDL2-only — we build the SDL1 variant via build-macos, same as
        # build-debug does on Linux). fluid-synth/libslirp/pkg-config would
        # also be auto-installed by build-macos itself, but checking here
        # too keeps behavior consistent with the Linux branch.
        BREW_PACKAGES=(autoconf automake nasm pkg-config fluid-synth libslirp libpcap)
        for pkg in "${BREW_PACKAGES[@]}"; do
            brew list --formula "$pkg" >/dev/null 2>&1 || brew install "$pkg"
        done
    fi
else
    log "Unrecognized OS ($OS_NAME) — skipping automatic dependency install"
    info "see BUILD.md in the dosbox-x-remotedebug checkout for what to install by hand"
fi

# --- 2. uv (needed to run dosbox-mcp and its installer) ------------------
log "Checking for uv"
if command -v uv >/dev/null 2>&1; then
    info "found: $(command -v uv)"
else
    info "not found — installing via the official astral.sh installer"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# --- 3. Clone/update dosbox-x-remotedebug ---------------------------------
log "dosbox-x-remotedebug"
if [ -d "$REMOTEDEBUG_DIR/.git" ]; then
    # Fetch by explicit URL rather than relying on a remote named "origin"
    # matching REMOTEDEBUG_REPO — an existing checkout may have its
    # remotes named/pointed differently.
    info "already cloned at $REMOTEDEBUG_DIR — fetching + checking out $REMOTEDEBUG_BRANCH"
    git -C "$REMOTEDEBUG_DIR" fetch "$REMOTEDEBUG_REPO" "$REMOTEDEBUG_BRANCH"
    git -C "$REMOTEDEBUG_DIR" checkout -f -B "$REMOTEDEBUG_BRANCH" FETCH_HEAD
else
    info "cloning $REMOTEDEBUG_REPO ($REMOTEDEBUG_BRANCH) -> $REMOTEDEBUG_DIR"
    git clone --branch "$REMOTEDEBUG_BRANCH" "$REMOTEDEBUG_REPO" "$REMOTEDEBUG_DIR"
fi

BINARY="$REMOTEDEBUG_DIR/src/dosbox-x"
if [ -x "$BINARY" ] && [ "$REBUILD" -eq 0 ]; then
    info "binary already built at $BINARY (pass --rebuild to force)"
else
    if [ "$OS_NAME" = "Darwin" ]; then
        BUILD_SCRIPT="./build-macos"
    else
        BUILD_SCRIPT="./build-debug"
    fi
    log "Building dosbox-x-remotedebug via $BUILD_SCRIPT (compiles an in-tree SDL 1.x too — several minutes)"
    (
        cd "$REMOTEDEBUG_DIR"
        "$BUILD_SCRIPT" --enable-remotedebug --disable-libfluidsynth --disable-mt32
    )
fi

# --- 4. Clone/update dosbox-mcp -------------------------------------------
log "dosbox-mcp"
if [ -d "$MCP_DIR/.git" ]; then
    info "already cloned at $MCP_DIR — fetching latest"
    git -C "$MCP_DIR" fetch "$MCP_REPO" HEAD
    git -C "$MCP_DIR" checkout -f -B _setup FETCH_HEAD
else
    info "cloning $MCP_REPO -> $MCP_DIR"
    git clone "$MCP_REPO" "$MCP_DIR"
fi

# --- 5. Base dosbox.conf: mount this repo's game/ as C:, autorun COMM.BAT
log "Writing base conf"
if [ -f "$CONF_PATH" ]; then
    info "$CONF_PATH already exists — leaving it alone"
else
    cat > "$CONF_PATH" <<EOF
[sdl]
output=surface
fullscreen=false

[render]
scaler=none
aspect=false

[dosbox]
memsize=16

[cpu]
core=auto
cputype=auto

[autoexec]
MOUNT C: $REPO_ROOT/game
C:
COMM.BAT
EOF
    info "wrote $CONF_PATH (mounts $REPO_ROOT/game as C:, autoruns COMM.BAT)"
fi

# --- 6. Register the MCP server with Claude Code --------------------------
log "Registering dosbox-mcp with Claude Code"
INSTALL_ARGS=(
    --remotedebug-dir "$REMOTEDEBUG_DIR"
    --conf "$CONF_PATH"
    --project-dir "$REPO_ROOT"
)
[ "$ASSUME_YES" -eq 1 ] && INSTALL_ARGS+=(--yes)
(cd "$MCP_DIR" && ./install.py "${INSTALL_ARGS[@]}")

# --- 7. Patch in XMODIFIERS="" (install.py doesn't set this) -------------
# Only relevant on Linux (SDL1 + ibus input method segfault workaround) —
# macOS has no X11/ibus, so there's nothing to patch there.
if [ "$OS_NAME" = "Linux" ]; then
    log "Patching XMODIFIERS into the registered MCP entry"
    python3 - "$REPO_ROOT" <<'PYEOF'
import json, pathlib, sys

project_dir = sys.argv[1]
path = pathlib.Path.home() / ".claude.json"
data = json.loads(path.read_text())

servers = (data.get("projects", {})
               .get(project_dir, {})
               .get("mcpServers", {}))
entry = servers.get("dosbox")
if entry is None:
    print("   ! no projects[...].mcpServers.dosbox entry found — skipping", file=sys.stderr)
    sys.exit(0)

env = entry.setdefault("env", {})
if env.get("XMODIFIERS") == "":
    print("   = XMODIFIERS already set")
else:
    env["XMODIFIERS"] = ""
    path.write_text(json.dumps(data, indent=2) + "\n")
    print("   ✓ set XMODIFIERS=\"\" (works around an SDL1+ibus crash on some desktops)")
PYEOF
else
    log "Skipping XMODIFIERS patch (Linux+ibus-only workaround, not applicable on $OS_NAME)"
fi

log "Done"
info "binary:  $BINARY"
info "conf:    $CONF_PATH"
info "Restart Claude Code (or run 'claude mcp list') to pick up the new MCP registration."
info "Manual smoke test without Claude: DOSBOX_X_BIN=$BINARY DOSBOX_X_CONF=$CONF_PATH ./utils/run_dune.sh"
