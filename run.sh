#!/bin/sh
set -eu
export OSTROVOK_ALLOW_LOCAL_WRITE=true
uvicorn intelligence.api:app --host 127.0.0.1 --port 8090
