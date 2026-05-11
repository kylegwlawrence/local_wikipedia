#!/usr/bin/env bash
# Starts (or restarts) the app inside a persistent tmux session.
# Usage:
#   ./start.sh            # start with default wiki DB
#   ./start.sh stop       # kill the session
#   ./start.sh attach     # attach to the running session
#   WIKI_DB=dumps/enwiki.db ./start.sh

set -euo pipefail

SESSION="wikipedia"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "${1:-start}" in
  stop)
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      tmux kill-session -t "$SESSION"
      echo "Stopped."
    else
      echo "No session named '$SESSION' is running."
    fi
    ;;

  attach)
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      tmux attach-session -t "$SESSION"
    else
      echo "No session named '$SESSION' is running. Run ./start.sh to start it."
      exit 1
    fi
    ;;

  start)
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      echo "Session '$SESSION' is already running."
      echo "  attach:  ./start.sh attach"
      echo "  restart: ./start.sh stop && ./start.sh"
      exit 0
    fi

    cd "$SCRIPT_DIR"
    source .venv/bin/activate

    CMD="uvicorn app:app --host 0.0.0.0 --port 8000"
    if [[ -n "${WIKI_DB:-}" ]]; then
      CMD="WIKI_DB=$WIKI_DB $CMD"
    fi

    tmux new-session -d -s "$SESSION" -x 220 -y 50 "bash -c '$CMD; exec bash'"
    echo "Started. App is running at http://localhost:8000"
    echo "  attach:  ./start.sh attach"
    echo "  stop:    ./start.sh stop"
    ;;

  *)
    echo "Usage: $0 [start|stop|attach]"
    exit 1
    ;;
esac
