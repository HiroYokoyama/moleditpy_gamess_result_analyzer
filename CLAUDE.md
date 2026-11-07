# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A MoleditPy plugin that parses GAMESS quantum-chemistry output (`.log`, `.out`) and
presents it as analysis dialogs — MO visualisation, vibrational modes, NMR,
TD-DFT, charges, forces, trajectories. PySide6 for the UI, pyvista for the 3D
viewer, matplotlib for plots, rdkit for molecule handling.

`gamess_result_analyzer/` is the whole plugin; there is no `src/` layout. It ships
as a zip of that directory.

## Layout

- `__init__.py` — plugin entry points (`initialize`, `run`) and the
  `PLUGIN_VERSION` constant. Registers windows through the host's context
  registry rather than holding references itself.
- `parser.py` — `GAMESSParser` itself: `__init__`, `load_from_memory`, `parse_all`.
  The ~30 `parse_*` methods live in four mixins it inherits —
  `parser_structure.py` (geometry, trajectory, gradients, scans),
  `parser_electronic.py` (MO coefficients, orbital energies, basis, SCF trace),
  `parser_properties.py` (dipole, charges, Mayer, energy components) and
  `parser_spectra.py` (NMR, TD-DFT, thermochemistry, frequencies). They all
  fill one `self.data` dict; every dialog reads from that dict. A new GAMESS
  block means a new `parse_*` method in the matching mixin, called from
  `parse_all()`. Ordering there matters — gradients are parsed before the
  trajectory so they can be linked to it. All five modules are listed in
  `tests/test_exception_policy.py`'s `_FULLY_NARROWED`: no broad `except` is
  allowed in any of them.
- `gui.py` — `GAMESSResultAnalyzerDialog`, the main window. Owns the file loading,
  the 3D structure, atom picking, and one `show_*` launcher per analysis dialog.
- One module per analysis type (`freq_analysis.py`, `mo_analysis.py`, …), each a
  self-contained `QDialog`. `NMRDialog` is split the same way `GAMESSParser` is:
  `nmr_analysis.py` holds the dialog and its UI, with `nmr_merge.py` (peak
  merging and its persistence), `nmr_plot.py` (stick spectra,
  highlighting, 3D labels) and `nmr_export.py` as mixins.
- `mo_engine.py` — basis-set evaluation for MO cubes. Read
  `docs/MO_CALCULATION.md` before changing it: a mis-normalized basis function
  still renders as a plausible orbital, so changes there must be verified
  numerically (spherical-harmonic similarity, unit norm, nodal angles) rather
  than by looking at the picture. The same caution applies to the index/spin
  bookkeeping in `mo_analysis.py` — resolving the wrong orbital also produces a
  plausible picture. Keep the explanatory comments in the MO modules; the usual
  minimum-comment rule does not apply there.
- `settings.py` — the shared read-merge-atomic-write for the single
  `settings.json` beside the package. Every dialog owns one top-level key, so a
  save must merge rather than replace. Call `load_section(path, key)` /
  `save_section(path, key, values)` and pass the dialog's own `settings_file`;
  the path is deliberately a parameter, because the dialog tests redirect each
  module's `__file__` at a temp dir to keep the suite from writing into the
  package source tree.
- `utils.py` — shared helpers: `save_json_atomic`, `get_default_export_path`,
  and `notify(owner, message, timeout)`. `notify` is the only supported way to
  reach the host's status bar; it walks `self.context` / `parent_dlg` /
  `freq_dialog` / `parent()` itself, logs when there is no host, and survives an
  already-deleted Qt window. Do not call `context.show_status_message` directly.

## Version bumping

`PLUGIN_VERSION` in `gamess_result_analyzer/__init__.py` is the single source of
truth. The release workflow verifies it matches the pushed tag and fails
otherwise, so bump it in the same commit as the fix it describes.

## Testing

```bash
python run_tests.py                 # whole suite, exactly as CI runs it
python run_tests.py -k parser       # pytest args are forwarded
```

**Always use `run_tests.py`, not `pytest` directly.** It sets
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` so local runs match CI, which installs no Qt
binding. An installed `pytest-qt` would otherwise import a real PySide6 during
collection and defeat the stubs the tests rely on — producing failures and
segfaults that do not reproduce in CI.

Coverage (plugin autoload is off, so `-p pytest_cov` is required):

```bash
python run_tests.py -p pytest_cov --cov=gamess_result_analyzer --cov-report=term-missing
```

Target is above 80%. CI reports it but does not gate on it.

### Python version

CI runs 3.11–3.13. The source uses 3.10+ syntax (`str | None`, `list[str]`), so
on an older interpreter `tests/test_api.py` fails at collection. If you are on
3.9, `--ignore=tests/test_api.py` lets the rest of the suite run, but treat CI
as authoritative.

## Writing dialog tests

The dialogs need Qt and a live 3D viewer at import time. `tests/gui_harness.py`
provides `load_isolated("module_name")`, which loads one source module against
subclassable Qt stand-ins that keep real state (spin boxes, combos, buttons,
trees, tables, lists remember what you set), then restores the shared
`sys.modules`. `tests/test_nmr_dialog.py` is a representative example.

Traps worth knowing, each of which has cost real debugging time here:

**The widget stubs are permissive.** An unknown attribute returns a MagicMock
instead of raising, so a mistyped method name in a test *passes silently*.
Assert on observable state, not merely that a call happened.

**Settings paths come from the module directory**, sometimes recomputed inside
each method rather than stored on the dialog. Redirect the loaded module's
`__file__` at a temp dir or the suite writes a `settings.json` into the package
source tree (it is gitignored, and was deliberately untracked, so it is easy to
miss):

```python
saved = M.__file__
M.__file__ = os.path.join(self.tmp, "mod.py")
self.addCleanup(lambda: setattr(M, "__file__", saved))
```

**Never install a crippled third-party module into `sys.modules` permanently.**
Several test modules stub `numpy`, `matplotlib` and `PIL`; those stubs must be
*fallbacks used only when the real package is absent*. A stub that overwrites a
working package breaks every later test doing real numerics — matplotlib
resolves `numpy` and its Agg backend from `sys.modules` at call time, long after
import.

**Qt idioms that hang against a MagicMock.** `while layout.count(): takeAt(0)`
and `while it.value(): ...; it += 1` never terminate when the counter is a
MagicMock. The harness provides stateful layouts and a terminating tree iterator
for this reason; if you add a stub, make sure loop conditions can go falsy.

**Patch where the name is bound.** Some methods import Qt lazily inside the
function body (`from PySide6.QtWidgets import QInputDialog`) — wrap those calls in
`gui_harness.qt_available(QInputDialog=fake)`. Others import at module level —
patch the loaded module's attribute (`patch.object(M, "QInputDialog", fake)`).
Sibling dialogs imported lazily (`from .bond_analysis import ...`) are patched
through `sys.modules[f"{G.__package__}.bond_analysis"]`.

**Avoid patching `builtins.open`** — it breaks pytest's own I/O and hangs the
run. Point at a genuinely unwritable path instead (e.g. a path under a file).

**Keep exporter tests off the rasteriser.** Assert that `savefig` was driven
rather than writing real bytes; a real render pulls in Agg and Pillow, which
other test modules stub out.

## Fixture shapes

Match what `parser.py` actually produces, or tests pass against fiction:

- MO dicts are keyed `"0_alpha"` but carry a numeric `"id"`.
- Convergence entries store the raw GAMESS verdict string (`"YES"`/`"NO"`), not a
  bool.
- `spin_s2` is a dict (`actual`/`ideal`/`contamination`), not a float.
- FMO rows label atoms with a single `"0-C"` token.

## Features follow the program

A control exists only for what GAMESS prints: no NBO, J coupling, CD, velocity gauge,
NEB or scan controls. `tests/claimed_features.py` is the list of claims, each with a
real job; add to it with any new feature, and run `tests/test_real_windows.py`, which
presses every button under real Qt.

GAMESS MO vectors are always over Cartesian AOs, even with `ISPHER=1`: there are then
fewer MOs than coefficients. An optimization's last table sits under `MOLECULAR
ORBITALS`, with fewer orbitals than the first.

## Sample outputs

Files in `tests/sample_outputs/` must go through `tools/sanitize_gamess_log.py`
and contain only textbook molecules. Never commit a raw log: it records the path,
user and machine.

## Conventions

Error handling policy is in `CONTRIBUTING.md` and is enforced in review: never
hide errors, never crash. UI slots and callbacks catch, log, and tell the user;
internal helpers propagate. Empty `except` blocks are not acceptable.

Two tests enforce that mechanically rather than leaving it to review:

- `tests/test_exception_policy.py` — a broad `except Exception` is allowed only
  where it is justified, and the five parser modules permit none at all. The
  count is a ratchet: it may fall freely, and raising it has to change that file.
- `tests/test_log_messages.py` — a caught exception must be logged with the
  operation that failed. **Never write `logging.warning("silenced: %s", e)`** or
  any placeholder like it; there were 135 of these and they made every log
  unactionable, since a user reporting "nothing happened" produced a line that
  named neither the operation nor the object. Say what was being attempted and
  interpolate the identifying detail: `logging.warning("NMR: reading merged
  peaks from %s failed: %s", path, e)`. No f-strings in logging calls.

Comments are minimal — one short line only where the logic is genuinely
non-obvious, with the failure scenario in the commit message rather than the
code. Docstrings are the exception to that and are expected: one line, saying
what the function does, never restating the signature. The MO modules
(`mo_analysis.py`, `mo_engine.py`, `mo_compare.py`) are exempt from the
minimum-comment rule entirely — see the note in Layout.

`pylint` runs in CI (`.github/workflows/tests.yml`, the `lint` job) as a score
ratchet via `--fail-under`, so the score may rise but not fall. Treat its
advice as advice: its `unnecessary-lambda` reports are wrong here, because
`connect(lambda: self.method())` deliberately stops Qt feeding a signal's
argument into a method's first parameter.

## Copied maintenance scripts

See [scripts/COMMON_TOOLS.md](scripts/COMMON_TOOLS.md) for canonical ownership, pinned versions and update instructions. Common script unit tests stay in the canonical repository; retain this repository's own integration checks.
