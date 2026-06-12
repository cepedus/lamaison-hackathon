"""Entry point so ``python -m cleaner ...`` works."""

from __future__ import annotations

import sys

from cleaner.cli import main


if __name__ == "__main__":
    sys.exit(main())
