"""
tests/test_exception_policy.py
Enforces CONTRIBUTING.md section 4 B/C on the package source.

Section 4B requires UI slots and callbacks to catch broadly so a slot can
never crash the app, and to log or report what they caught. Section 4C
requires internal helper code to name specific exception types instead.

These are structural checks over the AST, so they cost nothing at runtime and
fail the moment a new blind handler is added. The broad-handler count is a
ratchet: it may fall freely, but raising it is a deliberate act that has to
change this file too.
"""

import ast
import re
import os
import unittest

_PKG = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "gamess_result_analyzer")
)

# Ratchet. Lower it whenever handlers are narrowed; never raise it without a
# reason in the commit message. Was 212 before the 3.12.2 narrowing pass, 50
# before the 4.0.0 narrow-or-justify pass, 46 before the cancellable NMR
# coupling simulation.
_MAX_BROAD_HANDLERS = 39

# A surviving broad handler must name one of these on the `except` line or
# the line above it (see test_broad_handlers_carry_a_reason_category).
_REASON_CATEGORIES = (
    "Qt slot",
    "plugin boundary",
    "C++ library boundary",
    "worker-thread top level",
)

# parser.py is pure text parsing with no UI slots, so every handler in it can
# name its types. Verified bit-identical over the 19 sample outputs.
_FULLY_NARROWED = {
    "parser.py",
    "parser_structure.py",
    "parser_electronic.py",
    "parser_properties.py",
    "parser_spectra.py",
}


def _handlers():
    """Yield (filename, lineno, caught_names, handler_node, try_node)."""
    for name in sorted(os.listdir(_PKG)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(_PKG, name)
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.Try):
                continue
            for h in node.handlers:
                t = h.type
                if t is None:
                    caught = None  # bare except:
                elif isinstance(t, ast.Name):
                    caught = {t.id}
                elif isinstance(t, ast.Tuple):
                    caught = {e.id for e in t.elts if isinstance(e, ast.Name)}
                else:
                    caught = {ast.unparse(t)}
                yield name, h.lineno, caught, h, node


def _is_broad(caught):
    return caught is None or bool(caught & {"Exception", "BaseException"})


class TestExceptionPolicy(unittest.TestCase):
    def test_no_bare_except_anywhere(self):
        """`except:` also swallows KeyboardInterrupt and SystemExit."""
        bare = [
            f"{name}:{ln}" for name, ln, caught, _h, _t in _handlers() if caught is None
        ]
        self.assertEqual(bare, [], f"bare `except:` found at {bare}")

    def test_parser_names_every_exception_it_catches(self):
        offenders = [
            f"{name}:{ln}"
            for name, ln, caught, _h, _t in _handlers()
            if name in _FULLY_NARROWED and _is_broad(caught)
        ]
        self.assertEqual(
            offenders,
            [],
            "parser.py is internal helper code with no UI slots; every handler "
            f"must name its types. Broad handlers at {offenders}",
        )

    def test_broad_handler_count_does_not_grow(self):
        broad = [
            f"{name}:{ln}"
            for name, ln, caught, _h, _t in _handlers()
            if _is_broad(caught)
        ]
        self.assertLessEqual(
            len(broad),
            _MAX_BROAD_HANDLERS,
            f"{len(broad)} broad handlers, ceiling is {_MAX_BROAD_HANDLERS}. "
            "Name the exception types, or raise the ratchet deliberately.",
        )

    def test_broad_handlers_carry_a_reason_category(self):
        """Every surviving broad handler must be NARROWED or JUSTIFIED."""
        unjustified = []
        for fname, ln, caught, _h, _t in _handlers():
            if not _is_broad(caught):
                continue
            path = os.path.join(_PKG, fname)
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
            candidates = lines[max(0, ln - 2) : ln]
            if not any(
                cat in text for text in candidates for cat in _REASON_CATEGORIES
            ):
                unjustified.append(f"{fname}:{ln}")
        self.assertEqual(
            unjustified,
            [],
            "broad handlers with no reason comment naming a category "
            f"(Qt slot / plugin boundary / C++ library boundary / "
            f"worker-thread top level): {unjustified}",
        )

    def test_the_ratchet_is_tight(self):
        """A stale ceiling stops catching regressions, so keep it exact."""
        broad = [1 for _n, _l, caught, _h, _t in _handlers() if _is_broad(caught)]
        self.assertEqual(
            len(broad),
            _MAX_BROAD_HANDLERS,
            "handlers were narrowed without lowering _MAX_BROAD_HANDLERS",
        )

    def test_no_handler_silently_discards_its_exception(self):
        """CONTRIBUTING 4B: empty except blocks are not acceptable."""
        silent = []
        for name, ln, _caught, h, _t in _handlers():
            body = [n for n in h.body if not isinstance(n, ast.Pass)]
            if not body:
                silent.append(f"{name}:{ln}")
        self.assertEqual(
            silent, [], f"handlers that only `pass` (no log, no report): {silent}"
        )

    def test_broad_handlers_log_or_report(self):
        """What survives as broad must at least say so."""
        mute = []
        for name, ln, caught, h, _t in _handlers():
            if not _is_broad(caught):
                continue
            text = " ".join(ast.unparse(n) for n in h.body)
            reports = any(
                k in text
                for k in (
                    "logging.",
                    "logger.",
                    "QMessageBox",
                    "show_status_message",
                    "finished_sig",
                    "raise",
                    "self.fail",
                )
            )
            # A handler that only substitutes a fallback value is fine.
            if not reports and not any(
                isinstance(n, (ast.Assign, ast.Return, ast.FunctionDef)) for n in h.body
            ):
                mute.append(f"{name}:{ln}")
        self.assertEqual(
            mute, [], f"broad handlers that neither log nor report: {mute}"
        )


class TestOptionalDependencyHandlers(unittest.TestCase):
    """A missing optional dependency leaves its name bound to None.

    Calling None raises TypeError, so any handler guarding such a call must
    name TypeError. This is invisible in a dev environment with rdkit and
    pyvista installed, and only shows up in CI, which installs neither --
    exactly how freq_analysis.reset_geometry broke on the 3.12 job.
    """

    def _optional_names(self, tree):
        """Names assigned None inside an except: block (import fallbacks)."""
        names = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            for h in node.handlers:
                for n in ast.walk(ast.Module(body=h.body, type_ignores=[])):
                    if (
                        isinstance(n, ast.Assign)
                        and isinstance(n.value, ast.Constant)
                        and n.value.value is None
                    ):
                        names.update(t.id for t in n.targets if isinstance(t, ast.Name))
        return names

    def test_handlers_guarding_optional_calls_catch_typeerror(self):
        offenders = []
        for name in sorted(os.listdir(_PKG)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(_PKG, name), encoding="utf-8") as fh:
                src = fh.read()
            tree = ast.parse(src)
            optional = self._optional_names(tree)
            if not optional:
                continue
            lines = src.splitlines()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Try):
                    continue
                # A broad sibling handler already covers everything.
                caught_all = any(
                    h.type is None
                    or (isinstance(h.type, ast.Name) and h.type.id == "Exception")
                    for h in node.handlers
                )
                if caught_all:
                    continue
                b0 = node.body[0].lineno
                b1 = max(getattr(n, "end_lineno", n.lineno) for n in node.body)
                body = "\n".join(lines[b0 - 1 : b1])
                called = {o for o in optional if re.search(rf"\b{o}\s*\(", body)}
                if not called:
                    continue
                for h in node.handlers:
                    caught = (
                        {h.type.id}
                        if isinstance(h.type, ast.Name)
                        else {e.id for e in h.type.elts if isinstance(e, ast.Name)}
                        if isinstance(h.type, ast.Tuple)
                        else set()
                    )
                    if caught and "TypeError" not in caught:
                        offenders.append(
                            f"{name}:{h.lineno} calls {sorted(called)} "
                            f"(may be None) but catches {sorted(caught)}"
                        )
        self.assertEqual(
            offenders,
            [],
            "handlers around optional-dependency calls must catch TypeError:\n  "
            + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
