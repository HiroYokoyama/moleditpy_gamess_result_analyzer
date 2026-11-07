"""
tests/_parser_loader.py
Shared loader for tests that import gamess_result_analyzer/parser.py standalone
(no Qt, no stubs). parser.py now uses package-relative imports for its mixin
modules (parser_structure.py, parser_electronic.py, ...), so it can no longer
be exec'd with no parent package. This mounts a throwaway package whose
``__path__`` points at the real ``gamess_result_analyzer`` source directory,
mirroring the technique in ``tests/gui_harness.py``.
"""

import importlib.util
import os
import sys
import types

_PKG_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "gamess_result_analyzer")
)

_counter = [0]


def load_standalone_parser(name_prefix="gamess_parser_standalone"):
    """Load parser.py as ``<throwaway pkg>.parser`` and return the module."""
    _counter[0] += 1
    pkg_name = f"{name_prefix}_{_counter[0]}"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [_PKG_DIR]
    sys.modules[pkg_name] = pkg
    spec = importlib.util.spec_from_file_location(
        f"{pkg_name}.parser", os.path.join(_PKG_DIR, "parser.py")
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = pkg_name
    sys.modules[f"{pkg_name}.parser"] = mod
    spec.loader.exec_module(mod)
    return mod
