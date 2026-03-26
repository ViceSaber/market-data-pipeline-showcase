"""Run the stale rechecker as a standalone script."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.stale_rechecker import run_stale_rechecker

if __name__ == "__main__":
    run_stale_rechecker()
