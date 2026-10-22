"""Command line entry point for ``ofscan``."""

import argparse

from opticfilm_linux import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ofscan",
        description="Scan and enhance slides from the Plustek OpticFilm 135i.",
    )
    parser.add_argument("--version", action="version", version=f"ofscan {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0
