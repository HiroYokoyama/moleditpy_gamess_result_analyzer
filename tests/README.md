# GAMESS Result Analyzer — Test Suite

~1780 tests across 53 files, covering the GAMESS parser, the pure analysis
logic, and every analysis dialog driven headlessly. No Qt binding, display, or
GAMESS installation is required to run them.

## Running the tests

```bash
python run_tests.py                        # full suite, exactly as CI runs it
python run_tests.py -k parser              # any pytest args are forwarded
python run_tests.py tests/test_parser.py -v
python run_tests.py tests/test_nmr_dialog.py::TestRecalc
```

**Use `run_tests.py` rather than calling `pytest` directly.** It sets
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` so local runs match CI, which installs no Qt
binding. An installed `pytest-qt` would otherwise import a real PySide6 during
collection and defeat the stubs the tests rely on — producing failures and
segfaults that do not reproduce in CI.

Coverage (autoload is off, so the plugin must be named explicitly):

```bash
python run_tests.py -p pytest_cov --cov=gamess_result_analyzer --cov-report=term-missing
```

The target is to keep coverage above 80%. CI reports it on every run but does
not fail the build on it.

Treat the number with some care: this is largely a Qt codebase, and a
constructor that runs without raising counts as covered. The parsing, the
chemical-shift and peak arithmetic, the exporters and the settings round-trips
are where the tests actually pin behaviour.

### Python version

CI runs 3.11–3.13. The source uses 3.10+ syntax (`str | None`, `list[str]`), so
on an older interpreter `tests/test_api.py` fails at collection. On 3.9,
`--ignore=tests/test_api.py` lets the rest of the suite run, but treat CI as
authoritative.

## How the suite is organised

Tests are grouped by what they exercise rather than one-to-one with source
files. Per-file test counts are deliberately not listed here — they go stale
within a commit; run `--collect-only` for the current picture.

| Area | Files | What they cover |
|---|---|---|
| **Parser** | `test_gamess_parser.py`, `test_parser_xyz_regressions.py` | Every parser against real output, and MO orthonormality on a real-space grid |
| **Fixtures** | `test_fixture_privacy.py` | No identifying text in a committed output file |
| **Cube / MO engine** | `test_mo_engine.py`, `test_mo_overlap.py` | Cube parsing (including MO cubes with a negative atom count), basis-set evaluation |
| **Analysis dialogs** | `test_*_dialog.py`, `test_*_analysis.py`, `test_small_dialogs.py`, `test_dialog_extras.py` | Each `QDialog` driven through `gui_harness` — construction, controls, exports, settings |
| **Main window** | `test_gui_dialog.py`, `test_gui_interaction.py`, `test_gui_launchers.py`, `test_gui_load_file.py`, `test_about_menu.py`, `test_gui_reset_on_load.py` | Button enablement, file loading, 3D picking, drag-and-drop, analysis-window lifecycle |
| **Plugin contract** | `test_init.py`, `test_plugin_integration.py`, `test_api.py` | Entry points, the host `PluginContext` API, and drift against the real main app |
| **Shared helpers** | `test_utils.py` | Pure utility functions |

## The Qt harness

The dialogs need Qt and a live 3D viewer at import time, neither of which
exists in CI. `tests/gui_harness.py` bridges that gap.

`load_isolated("module_name")` loads one source module against subclassable Qt
stand-ins that keep **real state** — spin boxes, combos, buttons, check boxes,
sliders, line edits, trees, tables and lists all remember what you set — then
restores the shared `sys.modules` so other test modules are unaffected. It also
swaps in the genuine numpy and matplotlib for the duration, since other test
modules leave stubs of those behind.

`tests/test_nmr_dialog.py` is a representative example.

Traps worth knowing before you add a test are documented in
[`CLAUDE.md`](../CLAUDE.md) — chiefly that the widget stubs are permissive (a
mistyped method name passes silently), that settings paths are derived from the
module directory and will otherwise write into the package source tree, and
that some Qt loop idioms never terminate against a MagicMock.

### Older stub style

Test modules predating the harness install their own partial PySide6 stubs at
**module level**, to avoid collection-order dependencies. Those still work and
have not been converted. If you touch one, note that any `sys.modules` stub for
a third-party package (`numpy`, `matplotlib`, `PIL`) must be a *fallback used
only when the real package is absent* — overwriting a working package breaks
every later test that does real numerics, because matplotlib resolves numpy and
its Agg backend from `sys.modules` at call time.

## Sample output files

Real GAMESS 2024 R2 output lives in `tests/sample_outputs/`, cleaned by
`tools/sanitize_gamess_log.py`. `test_fixture_privacy.py` fails if a path, name,
address or timing gets into one, so run new files through the tool first.

| File | Job | Key data |
|---|---|---|
| `benzene_opt_hess.out` | Optimize, then Hessian | Trajectory, 30 modes, 15 Mayer bonds |
| `h2co_cct_mo.out` | cc-pVTZ, `ISPHER=1` | f shells, 88 MOs over 100 AOs |
| `h2co_tddft.out` | TD-DFT | 5 states with amplitudes |
| `methanol_nmr.out` | `RUNTYP=NMR` | 6 GIAO shieldings |
| `methanol_nmr_abnormal.out` | NMR on two cores | Error termination |
| `o2_rohf.out`, `o2_uhf_opt.out` | Open shell | ROHF; UHF alpha and beta MOs, S**2 |
| `water_c2v_mo.out` | C2v optimization | Symmetry labels and copies, final-geometry MOs |
| `water_ccq_mo.out` | cc-pVQZ, `ISPHER=1` | g shells, 115 MOs over 140 AOs |
| `water_ccsdt.out`, `water_mp2.out` | Post-HF | Correlation energies |
| `water_d3.out` | DFT with D3 | Dispersion energy |
| `water_failed_opt.out` | Optimization out of steps | Not converged |
| `water_gradient.out` | Gradient | Forces |
| `water_opt_freq.out` | Optimize, then Hessian | Thermochemistry; checked against Gaussian 16 |
| `water_pdc.out` | `$PDC PTSEL=CHELPG` | Fitted ESP charges |
| `water_raman.out` | `RUNTYP=RAMAN` | Raman activities; checked against Gaussian 16 |
| `water_rhf_mo.out` | `NPRINT=7` | 19 MOs, Cartesian d |

See `tests/sample_outputs/NOTICE` for how these files are licensed.

## Integration test strategy

The plugin is loaded by a host application, so the host's `PluginContext` API
is a contract that can drift. Two tiers guard it:

```
Stub mode (always runs)
  _StubContext mirrors the real PluginContext API.
  No main app required. Catches interface mismatches immediately.

Real-context mode (runs when the main app is available)
  Uses the actual PluginContext from python_molecular_editor.
  Catches drift invisible to the stub.
```

Real-context mode activates when the repos are siblings:

```
<parent>/
    moleditpy_gamess_result_analyzer/   ← this plugin
    python_molecular_editor/                 ← main app
```

### CI jobs

| Job | Python | Main app | Tests |
|---|---|---|---|
| `test` | 3.11, 3.12, 3.13 | Cloned with `\|\| true` | Full suite with coverage; real-context tests run if the clone succeeded |
| `test-integration` | 3.11 | Cloned (hard failure if the clone fails) | `test_plugin_integration.py` + `test_api.py` including the real-context tier |

## Adding tests

- Match the fixture shapes `parser.py` actually produces, or the test passes
  against fiction. The shapes that have caught people out are listed in
  [`CLAUDE.md`](../CLAUDE.md#fixture-shapes).
- Parser behaviour that needs a new GAMESS feature belongs in a new sample file
  under `sample_outputs/`, exercised from `test_parser_samples.py`.
- New dialog behaviour goes through `gui_harness.load_isolated`, not a
  hand-rolled PySide6 stub.
