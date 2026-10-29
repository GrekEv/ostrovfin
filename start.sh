#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
. .venv/bin/activate
python -m pip install -r requirements.txt
export OSTROVOK_ALLOW_LOCAL_WRITE=true
python -m uvicorn intelligence.api:app --host 127.0.0.1 --port 8090
