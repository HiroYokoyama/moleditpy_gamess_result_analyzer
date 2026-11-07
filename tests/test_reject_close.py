"""
tests/test_reject_close.py

Two static rules keep the dialog close/reject contract intact:

1. Every QDialog subclass that does cleanup in closeEvent must also override
   reject() — QDialog.reject (Esc key) only hides the dialog, so without the
   override Esc skips timer stops, 3D actor/label removal, settings saves, and
   the NMR unsaved-merge prompt.

2. A dialog whose reject() routes back through self.close() must NOT end its
   closeEvent with super().closeEvent(): QDialog.closeEvent itself calls
   reject(), so the pair recurses (close -> closeEvent -> reject -> close) and
   the reentrancy guard leaves the window permanently visible — the "close
   button does nothing" bug. Such closeEvents must accept the event directly
   (event.accept()).
"""

import ast
import glob
import os
import unittest

_SRC_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "gamess_result_analyzer")
)


def _dialog_classes(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            base_names = {
                b.id if isinstance(b, ast.Name) else getattr(b, "attr", None)
                for b in node.bases
            }
            if "QDialog" in base_names:
                yield node


def _method(cls, name):
    for n in cls.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


def _calls_self_close(func):
    """True if the function body contains a self.close() call."""
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "close"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
        ):
            return True
    return False


def _calls_super_closeevent(func):
    """True if the function body contains a super().closeEvent(...) call."""
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "closeEvent"
            and isinstance(node.func.value, ast.Call)
            and isinstance(node.func.value.func, ast.Name)
            and node.func.value.func.id == "super"
        ):
            return True
    return False


class TestRejectRoutesThroughClose(unittest.TestCase):
    def test_every_qdialog_with_closeevent_overrides_reject(self):
        offenders = []
        for path in glob.glob(os.path.join(_SRC_DIR, "*.py")):
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=path)
            for cls in _dialog_classes(tree):
                methods = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
                if "closeEvent" in methods and "reject" not in methods:
                    offenders.append(f"{os.path.basename(path)}:{cls.name}")
        self.assertEqual(
            offenders,
            [],
            "QDialog subclasses with closeEvent cleanup but no reject() "
            f"override (Esc would skip the cleanup): {offenders}",
        )

    def test_reject_close_pairs_do_not_recurse(self):
        """A reject()->self.close() dialog must not super().closeEvent() —
        that pairing recurses and the window never closes."""
        offenders = []
        for path in glob.glob(os.path.join(_SRC_DIR, "*.py")):
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=path)
            for cls in _dialog_classes(tree):
                reject = _method(cls, "reject")
                close_evt = _method(cls, "closeEvent")
                if reject is None or close_evt is None:
                    continue
                if _calls_self_close(reject) and _calls_super_closeevent(close_evt):
                    offenders.append(f"{os.path.basename(path)}:{cls.name}")
        self.assertEqual(
            offenders,
            [],
            "reject()->self.close() paired with super().closeEvent() recurses "
            "and leaves the window stuck open; use event.accept() instead: "
            f"{offenders}",
        )


if __name__ == "__main__":
    unittest.main()
