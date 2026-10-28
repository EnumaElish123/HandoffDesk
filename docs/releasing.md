# Preparing a release

Work in a source checkout, separate from any existing installation and user
state directory. Never import a private installation's Git history or copy its
data into a public repository.

1. Update `handoffdesk/__init__.py` and `CHANGELOG.md`.
2. Run `python scripts/check_public_tree.py`. It checks tracked files as well
   as unignored source candidates; it is a guard, not a substitute for review.
3. Review `git status --short`, the staged diff, examples, screenshots, and all
   commit metadata for personal information. Local configuration and SQLite
   databases must remain outside the release, even if someone force-added them.
4. Run `python tests/run_tests.py --headless`, then the full runner on a working
   Windows desktop and an Ubuntu X11/XWayland display. Check the CI result for
   the exact commit you intend to publish. Record any skips and environment
   limitations honestly.
5. In a build environment, install `build` and run `python -m build`. Inspect
   the wheel and source archive: only application source, generic documentation,
   examples, tests, and licenses should be present. No local database,
   configuration, backup, log, virtual environment, or runtime belongs there.
6. Install the wheel into a new environment. From outside the source checkout,
   run `python /path/to/checkout/scripts/smoke_install.py`. This checks
   initialization, agent saves, idempotency, conflict handling, file manifests,
   package assets, and preservation of a fictional project's file.
7. Publish the reviewed source commit to the repository. A version tag and
   GitHub Release can be created for that commit after its checks pass.

The wheel and source archive require a separately installed Python 3.10+.
Windows desktop additionally needs the `desktop` extra and WebView2 Runtime;
Linux terminal reading uses the `tui` extra. Tk is only for `legacy-desktop`.
The wheel and source archive are not standalone executable bundles.

Build portable binaries with `scripts/build_portable.py` on each target OS.
The Windows user installer is `scripts/windows-installer.iss`; its online mode
prepares WebView2, and an explicitly supplied standalone installer supports
offline distribution. The manually triggered portable workflow prepares
unsigned artifacts, not a public release. See [installation acceptance](agent-first.md).
Bundles must include runtime/dependency licenses and pass clean-machine,
relocated-path, real Windows DPI, SSH/terminal, and actual Codex trust checks.

Keep release hashes and validation logs with release artifacts. Keep logs
containing machine paths private; a public validation note should report only
the platform, version, commands, aggregate outcomes, and limitations.
