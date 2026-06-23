#!/usr/bin/env bash
set -euo pipefail

python3.13 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r orchestrator/requirements-dev.txt

./.venv/bin/python -m compileall orchestrator -q
