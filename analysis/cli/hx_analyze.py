#!/usr/bin/env python3
"""Backwards-compatible entry-point that delegates to `hfe_ana`'s CLI module."""

from hfe_ana.cli.hx_analyze import main

if __name__ == "__main__":
    main()
