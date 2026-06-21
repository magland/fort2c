"""fortc - a deterministic Fortran-to-C transpiler.

fortc parses Fortran with fparser2 and emits C that follows the gfortran ABI
(lowercase symbol + trailing underscore, all arguments by pointer, column-major
arrays). Because it preserves the exact structure of every expression, the
generated C agrees with gfortran **bit-for-bit at -O0** - the property it was
built to guarantee.

Public API
----------
    generate_c(path, basename=None, only=None, runtime_header=...) -> str
    generate_h(path, basename=None, only=None, runtime_header=..., guard_prefix=...) -> str
    transpile_file(src, out_dir, basename=None, only=None, ...) -> (c_path, h_path)

`only` is an optional iterable/set of routine names: emit just that subset
(the rest of the file is still parsed so same-file calls resolve). Any Fortran
construct fortc cannot translate raises `Unsupported`.
"""

import os

from .transpiler import (
    generate_c,
    generate_h,
    Unsupported,
    DEFAULT_RUNTIME_HEADER,
    DEFAULT_GUARD_PREFIX,
    _default_basename,
)

__version__ = "0.1.0"

__all__ = [
    "generate_c",
    "generate_h",
    "transpile_file",
    "Unsupported",
    "DEFAULT_RUNTIME_HEADER",
    "DEFAULT_GUARD_PREFIX",
    "__version__",
]


def transpile_file(src, out_dir, basename=None, only=None,
                   runtime_header=DEFAULT_RUNTIME_HEADER,
                   guard_prefix=DEFAULT_GUARD_PREFIX):
    """Transpile `src` and write `<basename>.c` and `<basename>.h` into
    `out_dir`. Returns the (c_path, h_path) pair."""
    base = basename or _default_basename(src)
    os.makedirs(out_dir, exist_ok=True)
    c_path = os.path.join(out_dir, base + ".c")
    h_path = os.path.join(out_dir, base + ".h")
    with open(c_path, "w") as f:
        f.write(generate_c(src, base, only, runtime_header) + "\n")
    with open(h_path, "w") as f:
        f.write(generate_h(src, base, only, runtime_header, guard_prefix) + "\n")
    return c_path, h_path
