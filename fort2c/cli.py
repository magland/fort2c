"""Command-line interface for fort2c."""

import argparse
import sys

from . import __version__, transpile_file
from .transpiler import (
    generate_c,
    generate_h,
    Unsupported,
    DEFAULT_RUNTIME_HEADER,
    DEFAULT_GUARD_PREFIX,
    _default_basename,
)


def build_parser():
    p = argparse.ArgumentParser(
        prog="fort2c",
        description="A deterministic Fortran-to-C transpiler "
                    "(fparser2 front end), bit-exact at -O0.",
    )
    p.add_argument("source", help="Fortran source file")
    p.add_argument("--header", action="store_true",
                   help="emit the .h header instead of the .c source")
    p.add_argument("-o", "--out-dir", default=None,
                   help="write <basename>.c and <basename>.h into this "
                        "directory (instead of printing to stdout)")
    p.add_argument("--only", default=None,
                   help="comma-separated subset of routines to emit")
    p.add_argument("--basename", default=None,
                   help="output basename (default: source file stem)")
    p.add_argument("--runtime-header", default=DEFAULT_RUNTIME_HEADER,
                   help="support header the generated code includes "
                        f"(default: {DEFAULT_RUNTIME_HEADER})")
    p.add_argument("--guard-prefix", default=DEFAULT_GUARD_PREFIX,
                   help="include-guard prefix for the generated header "
                        f"(default: {DEFAULT_GUARD_PREFIX})")
    p.add_argument("--version", action="version",
                   version=f"fort2c {__version__}")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    only = set(args.only.split(",")) if args.only else None
    base = args.basename or _default_basename(args.source)
    try:
        if args.out_dir:
            c_path, h_path = transpile_file(
                args.source, args.out_dir, base, only,
                args.runtime_header, args.guard_prefix)
            print(c_path)
            print(h_path)
        elif args.header:
            print(generate_h(args.source, base, only,
                             args.runtime_header, args.guard_prefix))
        else:
            print(generate_c(args.source, base, only, args.runtime_header))
    except Unsupported as e:
        print(f"fort2c: unsupported Fortran construct: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
