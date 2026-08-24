"""Package entry point that forwards command-line execution to the ASCEND CLI."""

from .cli import main

raise SystemExit(main())

