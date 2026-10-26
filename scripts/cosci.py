#!/usr/bin/env python3
"""Entry point: python scripts/cosci.py <command> ..."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from cosci.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
