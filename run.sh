#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate venv
source "$SCRIPT_DIR/venv/bin/activate"

# Launch app
streamlit run app.py
