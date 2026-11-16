#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; fi
.venv/bin/python -m pip install -r requirements-futu.txt
printf '\n富途支持已安装。请打开并登录 OpenD，然后启动研究室。\n'
