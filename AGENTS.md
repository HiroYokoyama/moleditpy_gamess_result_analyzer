# Release protocol

When the user requests a release of the GAMESS Result Analyzer plugin, complete
the following steps in order:

1. Add regression tests for the fix and bump the patch version in
   `gamess_result_analyzer/__init__.py` (`PLUGIN_VERSION`).
2. Run the full test suite with coverage using
   `python run_tests.py -p pytest_cov --cov=gamess_result_analyzer --cov-report=term-missing --cov-report=xml`.
   Codecov is enabled for this repository; retain the full coverage upload in CI.
3. Push a feature branch and open a pull request to `main`.
4. Wait for all PR CI and Codecov checks to pass. Resolve failures before merging.
5. Merge the PR to `main`, then wait for main CI on the merged commit to pass.
6. Only after main CI is green, push the matching `vX.Y.Z` tag on that merged
   commit to trigger the automatic release workflow.
7. Verify the release workflow and release artifact, and report the PR,
   coverage result, tag, and release URL to the user.

Do not bypass the PR or tag a commit before its main CI passes.
