#!/bin/bash
# Boots the dosbox-x-remotedebug build used by the `dosbox` MCP server,
# with the same conf (mounts game/ as C:). Set DOSBOX_X_BIN / DOSBOX_X_CONF
# to override; defaults match the local ~/dosbox-mcp-tools setup.
#
# XMODIFIERS is cleared to work around an SDL1+ibus crash on some desktops.

DOSBOX_X_BIN="${DOSBOX_X_BIN:-$HOME/dosbox-mcp-tools/dosbox-x-remotedebug/src/dosbox-x}"
DOSBOX_X_CONF="${DOSBOX_X_CONF:-$HOME/dosbox-mcp-tools/dune-dosbox.conf}"

exec env XMODIFIERS="" "$DOSBOX_X_BIN" -conf "$DOSBOX_X_CONF"
