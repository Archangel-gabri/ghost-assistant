#!/usr/bin/env python3
"""CLI interface for Ghost — voice + screen assistant."""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from ghost.main import run_cli, load_config


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Ghost — voice + screen assistant — Terminal Mode",
        prog="ghost-cli",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config.yaml",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 2.0.0",
    )

    args = parser.parse_args()
    config_path = Path(args.config) if args.config else None
    cfg = load_config(config_path)
    run_cli(cfg)


if __name__ == "__main__":
    main()
