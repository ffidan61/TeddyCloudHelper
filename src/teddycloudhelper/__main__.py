"""Allow running as `python -m teddycloudhelper`."""

from teddycloudhelper.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
