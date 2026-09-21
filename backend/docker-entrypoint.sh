#!/bin/sh
set -eu

# CLI and MCP commands can override CMD without reapplying the schema.
if [ "${1:-}" = "novel-webapp" ]; then
    novel-pipeline init-db
fi

exec "$@"
