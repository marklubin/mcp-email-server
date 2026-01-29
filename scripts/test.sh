#!/bin/bash
# Run integration tests
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

echo "Running integration tests..."
uv run --extra dev pytest tests/ -v "$@"
