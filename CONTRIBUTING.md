# Contributing to HandoffDesk

Thank you for helping make project handoffs easier to trust and use. Small, focused changes with clear reproduction steps are welcome.

## Development setup

Use Python 3.10 or newer. The application uses the standard library; Tk is required for desktop development. Git state collection and native opening depend on the host's installed tools.

```sh
git clone https://github.com/EnumaElish123/HandoffDesk.git
cd HandoffDesk
python -m pip install .
python tests/run_tests.py --headless
```

On Ubuntu, install inside a virtual environment. Run the full suite in a desktop session with a matching Tk build:

```sh
python tests/run_tests.py
```

Keep real configuration and databases outside the checkout. Initialize an isolated state directory with `--app-dir` for manual testing. Never use personal project records as public fixtures.

## Changes and tests

- Describe the user-visible problem, the resulting behavior, and the relevant validation.
- Preserve the read-only boundary for registered project files. Tool writes belong to its own state or explicitly selected data directory.
- Preserve atomic record and file saves, operation replay semantics, and revision conflicts. Protocol changes need explicit compatibility handling and documentation.
- Keep headless commands independent of Tk and display services.
- Use temporary fixtures for new behavior. Add tests for meaningful regressions or safety boundaries rather than duplicating implementation details.
- Check Chinese and space paths when changing file handling. Pass native command arguments as separate values; do not build shell commands from file names.
- Keep user-facing text and documentation consistent. The current desktop is primarily Chinese; English UI support should be a deliberate feature.

For native opening changes, test the real desktop interaction in addition to mocked unit tests. Report what was tested and what was unavailable. A test on an isolated X11 session does not establish physical GNOME or Wayland coverage.

## Before a release

Run the applicable headless and desktop suites, verify installation from the built source package, and try a first initialization with an independent state directory. Check agent context, validation, save, replay, conflict handling, node file queries, and desktop receipt of a new record.

Review tracked files and generated archives for databases, local configuration, private paths, credentials, cached source contents, and test evidence. Use generic examples. Include the license and third-party notices, update the changelog, and retain the actual validation results for the release commit. Do not claim hosted CI success until that commit's runs have finished successfully.

## Issues and pull requests

Include the version, OS, Python and Tk versions where relevant, concise steps to reproduce, expected behavior, actual behavior, and sanitized logs. For file problems, reproduce with a small temporary file rather than sharing original research or personal documents.

By contributing, you agree that your contribution is licensed under this repository's [MIT license](LICENSE). Do not submit third-party material without compatible rights and attribution.
