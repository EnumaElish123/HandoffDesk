"""Frozen entry point shared by the desktop and console executables."""
from handoffdesk.__main__ import run


if __name__ == '__main__':
    raise SystemExit(run())
