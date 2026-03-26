"""Run the event indexer as a standalone script."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.event_indexer import run_event_indexer

if __name__ == "__main__":
    run_event_indexer()
