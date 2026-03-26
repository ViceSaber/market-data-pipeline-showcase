"""Run the family builder as a standalone script."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.family_builder import run_family_builder

if __name__ == "__main__":
    run_family_builder()
