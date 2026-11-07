"""
tests/test_reject_close_behavior.py

Behavioural regression for the "close button does nothing" bug.

The rest of the suite runs against Qt stubs whose close()/reject()/closeEvent
are no-ops, so they cannot exercise the real QDialog reentrancy that caused the
bug. This test drives a real PySide6 QDialog in an isolated subprocess (offscreen)
so it cannot disturb the shared in-process stubs, and skips when no Qt binding
is installed (as in CI).

It reproduces the exact fixed pattern used by every analyzer dialog:
    reject()  -> self.close()          (Esc must run closeEvent cleanup)
    closeEvent-> <cleanup>; event.accept()

and asserts that BOTH the window-close path (close()) and the Esc path
(reject()) actually hide the dialog while running cleanup exactly once. The
pre-fix pattern (closeEvent ending in super().closeEvent()) left the dialog
visible; that variant is also checked so the regression is unambiguous.
"""

import subprocess
import sys
import textwrap
import unittest


_CHILD = textwrap.dedent(
    r"""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication, QDialog, QWidget
    except Exception:
        print("SKIP: no PySide6 binding")
        raise SystemExit(0)

    app = QApplication.instance() or QApplication([])
    _keep = []  # keep parents alive so children are not GC-deleted

    def make(cls):
        parent = QWidget()
        _keep.append(parent)
        d = cls(parent)
        d.show(); app.processEvents()
        return d

    # --- fixed pattern: closeEvent accepts the event directly ---
    class Fixed(QDialog):
        def __init__(self, p):
            super().__init__(p); self.cleanups = 0
        def reject(self):
            self.close()
        def closeEvent(self, event):
            self.cleanups += 1
            event.accept()

    # --- pre-fix pattern: closeEvent -> super().closeEvent() (recurses) ---
    class Broken(QDialog):
        def __init__(self, p):
            super().__init__(p); self.cleanups = 0
        def reject(self):
            self.close()
        def closeEvent(self, event):
            self.cleanups += 1
            super().closeEvent(event)

    results = {}

    d = make(Fixed); d.close(); app.processEvents()
    results["fixed_close_visible"] = d.isVisible()
    results["fixed_close_cleanups"] = d.cleanups

    d = make(Fixed); d.reject(); app.processEvents()
    results["fixed_reject_visible"] = d.isVisible()
    results["fixed_reject_cleanups"] = d.cleanups

    d = make(Broken); d.close(); app.processEvents()
    results["broken_close_visible"] = d.isVisible()

    print("RESULTS", results)
    """
)


class TestRejectCloseBehaviour(unittest.TestCase):
    def test_close_and_reject_actually_hide_the_dialog(self):
        proc = subprocess.run(
            [sys.executable, "-c", _CHILD],
            capture_output=True,
            text=True,
            timeout=120,
        )
        out = proc.stdout
        if "SKIP:" in out:
            self.skipTest("PySide6 binding not available")
        self.assertIn("RESULTS", out, f"child failed:\n{proc.stdout}\n{proc.stderr}")
        line = next(ln for ln in out.splitlines() if ln.startswith("RESULTS"))
        results = eval(line[len("RESULTS ") :])  # trusted child output

        # Fixed pattern: both paths hide the window, cleanup runs exactly once.
        self.assertFalse(results["fixed_close_visible"], "close() left window open")
        self.assertEqual(results["fixed_close_cleanups"], 1)
        self.assertFalse(results["fixed_reject_visible"], "reject() left window open")
        self.assertEqual(results["fixed_reject_cleanups"], 1)

        # The pre-fix pattern demonstrably fails to close — this documents the bug.
        self.assertTrue(
            results["broken_close_visible"],
            "expected the super().closeEvent() pattern to leave the window open",
        )


if __name__ == "__main__":
    unittest.main()
