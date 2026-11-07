"""
tests/test_log_messages.py
Ratchets the quality of log messages in caught-exception handlers.

135 handlers across the package used to share one placeholder message
("silenced: %s") that named neither the operation nor the object involved,
making the log unactionable. This is a structural check over the AST, like
test_exception_policy.py, so a future placeholder cannot creep back in.
"""

import ast
import os
import unittest

_PKG = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "gamess_result_analyzer")
)

_LOG_METHODS = {"debug", "info", "warning", "error", "critical", "exception", "log"}

# Below this, a message cannot possibly name an operation and an object; it is
# indistinguishable from a placeholder like "silenced" or "error".
_MIN_MESSAGE_LENGTH = 12


def _log_calls():
    """Yield (filename, lineno, call_node) for every logging.<level>(...) call."""
    for name in sorted(os.listdir(_PKG)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(_PKG, name)
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in _LOG_METHODS
                and isinstance(func.value, ast.Name)
                and func.value.id == "logging"
            ):
                yield name, node.lineno, node


class TestLogMessages(unittest.TestCase):
    def test_no_placeholder_silenced_message(self):
        offenders = []
        for name, ln, node in _log_calls():
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if "silenced" in arg.value.lower():
                        offenders.append(f"{name}:{ln}")
        self.assertEqual(
            offenders,
            [],
            f"placeholder 'silenced' message found at {offenders}; name the "
            "operation and object being attempted instead",
        )

    def test_no_fstring_log_message(self):
        """f-strings eagerly format even when the level is disabled."""
        offenders = []
        for name, ln, node in _log_calls():
            if node.args and isinstance(node.args[0], ast.JoinedStr):
                offenders.append(f"{name}:{ln}")
        self.assertEqual(
            offenders,
            [],
            f"logging call passes an f-string instead of a lazy %-style "
            f"message at {offenders}",
        )

    def test_log_messages_are_not_trivially_short(self):
        """A future placeholder ("failed", "error occurred") should not fit.

        The lone exception is the bare passthrough "%s", used where the
        message itself is a caller-supplied string being relayed verbatim
        (e.g. utils.notify's status-bar fallback) rather than describing a
        failed operation.
        """
        offenders = []
        for name, ln, node in _log_calls():
            if not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                if first.value == "%s":
                    continue
                if len(first.value) < _MIN_MESSAGE_LENGTH:
                    offenders.append(f"{name}:{ln} ({first.value!r})")
        self.assertEqual(
            offenders,
            [],
            f"log message too short to name an operation: {offenders}",
        )


if __name__ == "__main__":
    unittest.main()
