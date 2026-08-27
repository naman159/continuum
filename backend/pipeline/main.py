"""``python -m pipeline.main`` shim for the CLI.

The installed console script (``novel-pipeline``) points at ``pipeline.cli:main``
directly; this exists so the CLI is also reachable without installing the package.
"""

from pipeline.cli import main

if __name__ == "__main__":
    main()
