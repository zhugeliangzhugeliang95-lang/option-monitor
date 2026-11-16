#!/bin/sh
cd "$(dirname "$0")"
exec python3 scripts/launch.py --stop
