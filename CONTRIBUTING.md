# Contributing Guidelines

Thank you for your interest in contributing! We welcome contributions from the community to make this project more robust and feature-rich.

To maintain the high quality and stability of the application, please review the following guidelines.

## 1. How to Report Bugs

When opening an Issue, please include:
1.  **Steps to Reproduce:** A detailed list of actions taken.
2.  **Expected Behavior vs. Actual Behavior.**
3.  **Logs / Traceback:** If applicable, please attach the contents of the console output or log files.
4.  **Environment:** OS, Python version, library/package version, etc.

## 2. Development Setup

1.  Clone the repository.
2.  Copy or symlink the plugin package folder into your **user plugin directory** of the main **MoleditPy** application so it is discovered automatically at startup:
    - **Windows**: `C:\Users\<YourName>\.moleditpy\plugins\`
    - **Linux / macOS**: `~/.moleditpy/plugins/`
3.  Install development dependencies as required by the project.

## 3. Coding Standards

We maintain high code quality standards. Before submitting a Pull Request (PR), ensure your code meets the following:

* **Style:** Follow standard style guidelines (e.g. PEP 8 for Python).
* **Type Hinting:** Use type hints for function arguments and return values where applicable.

## 4. Testing & Stability Policy

### A. Core Logic & Testing
* Core logic and utility modules should have automated unit tests.
* Maintain high test coverage for any new features.

**Running the suite**

```bash
python run_tests.py                  # whole suite, exactly as CI runs it
python run_tests.py -k parser        # any pytest arguments are forwarded
python run_tests.py tests/test_parser.py -v
```

Use `run_tests.py` rather than calling `pytest` directly. It disables
third-party plugin autoload so local runs match CI: CI installs no Qt binding,
and an installed `pytest-qt` would otherwise import a real PySide6 during
collection, defeating the stubs the tests rely on.

**Coverage**

The target is to keep coverage **above 80%**. CI reports it on every run but
does not fail the build on it — use the number as a guide, not a gate. To check
locally:

```bash
python run_tests.py -p pytest_cov --cov=gamess_result_analyzer --cov-report=term-missing
```

`-p pytest_cov` is required because plugin autoload is off.

Treat the figure with some care: much of this codebase is Qt dialogs, and a
constructor running without raising counts as covered. The parsing, the
chemical-shift and peak arithmetic, the exporters and the settings round-trips
are where the tests actually pin behaviour.

**Writing tests for dialogs**

The plugin needs Qt and a 3D viewer at import time, neither of which exists in
CI. `tests/gui_harness.py` solves this: `load_isolated("module_name")` loads one
source module against subclassable Qt stand-ins that keep real state (spin
boxes, combos, trees, tables remember what you set), then restores the shared
`sys.modules` so other test modules are unaffected. See `tests/test_nmr_dialog.py`
for a representative example, and [tests/README.md](tests/README.md) for how the
suite is organised and which sample GAMESS files are available.

Two things to watch out for:

* **Dialogs that persist settings** derive the path from their own module
  directory, sometimes recomputing it inside each method. Point the loaded
  module's `__file__` at a temp dir, or the suite will write a `settings.json`
  into the package source tree.
* **The widget stubs are permissive** — an unknown attribute returns a
  MagicMock rather than raising. A typo in a method name will therefore pass
  silently instead of failing. Assert on observable state, not just that a call
  happened.

### B. Error Handling — No Hiding, No Crashing
* Stability policy: **never hide errors, never crash.**
* Wrap UI slots, event handlers, or callbacks in `try/except` block, log the traceback, and show a status message or dialog to the user.
* Internal helpers should propagate exceptions rather than swallowing them.
* Write logs/tracebacks on caught exceptions; empty `except` blocks are not acceptable.

### C. GUI & Interaction
* **Avoid Broad Exceptions:** Minimize the use of broad `try-except Exception` blocks in internal helper code. Prefer granular, specific exception types.
* **Manual Verification:** Verify GUI changes manually and document your verification in the PR.

## 5. Pull Request Process

1.  **Branching:** Create a new branch for your feature or fix.
2.  **Verification:**
    * Run tests and check linting before submitting.
3.  **Description:** Describe your changes clearly. If it's a UI change, screenshots are highly appreciated.
4.  **Review:** Wait for a maintainer to review your code.

## 6. License

By contributing, you agree that your contributions will be licensed under the project's license.
