#!/usr/bin/env python3
"""Ghost — voice + screen assistant — Main entry point."""

import sys
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Add src to path for imports
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)-12s] %(levelname)s: %(message)s",
)

from ghost.main import run_gui, run_cli, run_once_cli
from ghost.main import load_config


def main():
    parser = argparse.ArgumentParser(
        description="Ghost — voice + screen assistant — Super-fast AI helper",
        prog="ghost",
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Terminal-only mode",
    )
    parser.add_argument(
        "--once",
        type=str,
        metavar="QUESTION",
        help="Single-shot CLI mode",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 2.0.0",
    )

    args = parser.parse_args()

    # Load config
    config_path = Path(args.config) if args.config else None
    cfg = load_config(config_path)

    # Run
    if args.once:
        run_once_cli(cfg, args.once)
    elif args.cli:
        run_cli(cfg)
    else:
        sys.exit(run_gui(cfg))


if __name__ == "__main__":
    main()
