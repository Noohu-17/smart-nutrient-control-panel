#!/usr/bin/env python3
"""Validate the client configuration packs in config/clients/.

Parses every config/clients/*/*.json and checks each pack against docs/contract.md
section 2. The checks live in backend/app/packcheck.py, so the back end runs the very
same ones before it stores a pack in the database (T1-39). Standard library only
(Python 3): nothing from the back end's requirements is needed.

Usage:
    python scripts/validate_config.py [CLIENTS_DIR]

CLIENTS_DIR defaults to config/clients/ in this repo. Exits 0 when every
check passes and 1 on any failure, printing one line per problem.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.packcheck import main  # after the path above

if __name__ == "__main__":
    sys.exit(main(sys.argv))
