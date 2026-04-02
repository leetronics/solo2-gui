#!/bin/sh
export PYTHONPATH="/home/manuel/opencode/solokeys-gui/src${PYTHONPATH:+:$PYTHONPATH}"
export SOLOKEYS_PATH=auto
exec "/usr/bin/python3" -m solo_gui.native_host "$@"
