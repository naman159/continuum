#!/usr/bin/env bash
#
# Per-branch Postgres database management for the continuum backend.
#
# Each git branch gets its own isolated database named:
#     novel_wiki_<branch_slug>
#
# The script writes a .env.branch file (gitignored) that pipeline/config.py
# loads with override=true on top of .env. Switching branches just re-runs
# `branch_db.sh use` to repoint at the right DB without ever touching .env
# or main's database.
#
# Subcommands:
#   name              Print the DB name for the current branch.
#   url               Print the DATABASE_URL for the current branch.
#   show              Show current branch + DB + connection status.
#   create            Create the branch DB and apply schema (idempotent).
#   drop              Drop the branch DB (asks for confirmation).
#   reset             Drop + recreate + reapply schema.
#   clone-from-main   Copy main's `novel_wiki` data into the branch DB.
#   use               Write .env.branch pointing config at the branch DB.
#   clear             Remove .env.branch (revert to plain .env / main DB).
#   list              List all novel_wiki_* databases on the local server.
#
# Hard rules:
#   - This script NEVER writes to the `novel_wiki` database (main's DB).
#   - drop/reset refuse to operate on `novel_wiki`.
#   - No subcommand modifies .env.

set -euo pipefail

# Resolve paths relative to the script so it works from any cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_BRANCH_FILE="$BACKEND_DIR/.env.branch"

MAIN_DB_NAME="novel_wiki"
DB_HOST="${PGHOST:-localhost}"
DB_USER_FLAG=""
if [[ -n "${PGUSER:-}" ]]; then
  DB_USER_FLAG="-U $PGUSER"
fi

branch_name() {
  git -C "$BACKEND_DIR" symbolic-ref --short HEAD 2>/dev/null || {
    echo "ERROR: not on a branch (detached HEAD?)" >&2
    exit 1
  }
}

slugify() {
  # lowercase, replace any non-alphanumeric with underscores, collapse repeats
  echo "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/_/g; s/^_+|_+$//g'
}

db_name_for_branch() {
  local b
  b="$(branch_name)"
  if [[ "$b" == "main" || "$b" == "master" ]]; then
    echo "$MAIN_DB_NAME"
  else
    echo "novel_wiki_$(slugify "$b")"
  fi
}

db_url_for() {
  local dbname="$1"
  if [[ -n "${PGUSER:-}" ]]; then
    echo "postgresql://${PGUSER}@${DB_HOST}/${dbname}"
  else
    echo "postgresql://${DB_HOST}/${dbname}"
  fi
}

guard_not_main_db() {
  local dbname="$1"
  if [[ "$dbname" == "$MAIN_DB_NAME" ]]; then
    echo "ERROR: refusing to touch the main database '$MAIN_DB_NAME'." >&2
    echo "       The current branch resolves to the main DB. Switch to a feature branch." >&2
    exit 2
  fi
}

db_exists() {
  local dbname="$1"
  psql $DB_USER_FLAG -h "$DB_HOST" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname='$dbname'" | grep -q 1
}

apply_schema() {
  local dburl="$1"
  echo "Applying schema via 'novel-pipeline init-db'..."
  # Prefer the project's local venv; fall back to PATH; fall back to `python -m`.
  local runner=""
  if [[ -x "$BACKEND_DIR/.venv/bin/novel-pipeline" ]]; then
    runner="$BACKEND_DIR/.venv/bin/novel-pipeline"
  elif [[ -x "$BACKEND_DIR/venv/bin/novel-pipeline" ]]; then
    runner="$BACKEND_DIR/venv/bin/novel-pipeline"
  elif command -v novel-pipeline >/dev/null 2>&1; then
    runner="novel-pipeline"
  fi
  if [[ -n "$runner" ]]; then
    ( cd "$BACKEND_DIR" && DATABASE_URL="$dburl" "$runner" init-db )
  else
    # Last resort: run the module directly via the project python.
    local py="$BACKEND_DIR/.venv/bin/python"
    [[ -x "$py" ]] || py="$BACKEND_DIR/venv/bin/python"
    [[ -x "$py" ]] || py="python3"
    ( cd "$BACKEND_DIR" && DATABASE_URL="$dburl" "$py" -m pipeline.pipeline init-db )
  fi
}

cmd_name() {
  db_name_for_branch
}

cmd_url() {
  db_url_for "$(db_name_for_branch)"
}

cmd_show() {
  local b dbname dburl
  b="$(branch_name)"
  dbname="$(db_name_for_branch)"
  dburl="$(db_url_for "$dbname")"
  echo "Branch:         $b"
  echo "DB name:        $dbname"
  echo "DATABASE_URL:   $dburl"
  if db_exists "$dbname"; then
    echo "Status:         EXISTS"
  else
    echo "Status:         (not created)"
  fi
  if [[ -f "$ENV_BRANCH_FILE" ]]; then
    echo ".env.branch:    present"
    grep -E '^DATABASE_URL=' "$ENV_BRANCH_FILE" | sed 's/^/                /'
  else
    echo ".env.branch:    (not present — config will use .env)"
  fi
}

cmd_create() {
  local dbname dburl
  dbname="$(db_name_for_branch)"
  guard_not_main_db "$dbname"
  dburl="$(db_url_for "$dbname")"
  if db_exists "$dbname"; then
    echo "DB '$dbname' already exists. Skipping CREATE; reapplying schema."
  else
    echo "Creating database '$dbname'..."
    psql $DB_USER_FLAG -h "$DB_HOST" -d postgres -c "CREATE DATABASE \"$dbname\""
  fi
  apply_schema "$dburl"
  echo "Done. Run '$0 use' to point config at this DB."
}

cmd_drop() {
  local dbname
  dbname="$(db_name_for_branch)"
  guard_not_main_db "$dbname"
  if ! db_exists "$dbname"; then
    echo "DB '$dbname' does not exist; nothing to drop."
    return 0
  fi
  read -r -p "DROP database '$dbname'? Type the name to confirm: " confirm
  if [[ "$confirm" != "$dbname" ]]; then
    echo "Aborted." >&2
    exit 1
  fi
  psql $DB_USER_FLAG -h "$DB_HOST" -d postgres -c \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$dbname' AND pid <> pg_backend_pid()" \
    >/dev/null
  psql $DB_USER_FLAG -h "$DB_HOST" -d postgres -c "DROP DATABASE \"$dbname\""
  echo "Dropped '$dbname'."
}

cmd_reset() {
  cmd_drop
  cmd_create
}

cmd_clone_from_main() {
  local dbname dburl
  dbname="$(db_name_for_branch)"
  guard_not_main_db "$dbname"
  dburl="$(db_url_for "$dbname")"
  if db_exists "$dbname"; then
    echo "ERROR: '$dbname' already exists. Drop it first if you really want to clone." >&2
    exit 1
  fi
  echo "Cloning '$MAIN_DB_NAME' -> '$dbname' via pg_dump | psql ..."
  pg_dump $DB_USER_FLAG -h "$DB_HOST" "$MAIN_DB_NAME" \
    | psql $DB_USER_FLAG -h "$DB_HOST" -d postgres -c "CREATE DATABASE \"$dbname\""
  pg_dump $DB_USER_FLAG -h "$DB_HOST" "$MAIN_DB_NAME" \
    | psql $DB_USER_FLAG -h "$DB_HOST" -d "$dbname" >/dev/null
  echo "Cloned. Run '$0 use' to point config at it."
}

cmd_use() {
  local dbname dburl
  dbname="$(db_name_for_branch)"
  guard_not_main_db "$dbname"
  dburl="$(db_url_for "$dbname")"
  cat > "$ENV_BRANCH_FILE" <<EOF
# Auto-generated by scripts/branch_db.sh. Gitignored.
# pipeline/config.py loads this AFTER .env with override=true.
DATABASE_URL=$dburl
CONTINUUM_BRANCH_DB=$dbname
EOF
  echo "Wrote $ENV_BRANCH_FILE"
  echo "DATABASE_URL = $dburl"
}

cmd_clear() {
  if [[ -f "$ENV_BRANCH_FILE" ]]; then
    rm "$ENV_BRANCH_FILE"
    echo "Removed $ENV_BRANCH_FILE. Config will fall back to .env."
  else
    echo "No $ENV_BRANCH_FILE present; nothing to clear."
  fi
}

cmd_list() {
  psql $DB_USER_FLAG -h "$DB_HOST" -d postgres -tAc \
    "SELECT datname FROM pg_database WHERE datname LIKE 'novel_wiki%' ORDER BY datname"
}

usage() {
  sed -n '2,30p' "$0"
}

case "${1:-show}" in
  name)            cmd_name ;;
  url)             cmd_url ;;
  show)            cmd_show ;;
  create)          cmd_create ;;
  drop)            cmd_drop ;;
  reset)           cmd_reset ;;
  clone-from-main) cmd_clone_from_main ;;
  use)             cmd_use ;;
  clear)           cmd_clear ;;
  list)            cmd_list ;;
  -h|--help|help)  usage ;;
  *)
    echo "Unknown subcommand: $1" >&2
    usage >&2
    exit 1
    ;;
esac
