# Third-party notices

HandoffDesk's application code, documentation, and geometric application icon
(the SVG source and its PNG/ICO renditions) are distributed under the MIT
license in `LICENSE`.

This source distribution does not bundle a Python interpreter, Tcl/Tk runtime,
operating system fonts, or other third-party binary libraries. HandoffDesk uses
Python's standard library, including SQLite, and optionally Tkinter for the
legacy desktop. Those components remain under their respective licenses provided by
their distributors. The core command line has no third-party runtime dependencies.

Optional readers use Textual 6.12.0 (MIT) and pywebview 6.2.1 (BSD-3-Clause).
Textual depends on Rich, markdown-it-py, Pygments and related packages; pywebview
uses the platform Web engine and, on Windows, pythonnet and WebView2. Their
respective installed distribution licenses must accompany binary packages.
Microsoft WebView2 Runtime is distributed under Microsoft's terms. The local
HTML/CSS/JavaScript frontend contains no CDN assets or third-party UI framework.

Development and packaging tools such as setuptools and GitHub Actions are
external tools, not part of the application source. Their own licenses apply.
If a future release bundles a runtime, its distributor must also include the
licenses and notices required by that runtime and all bundled libraries.

## Time zone data

`tzdata` supplies IANA time zone data when the operating system has none. It is distributed under Apache-2.0; the underlying IANA database is public domain. Source packages resolve the declared dependency; portable builds include its data and license files. See [Python zoneinfo](https://docs.python.org/3/library/zoneinfo.html#data-sources) and [tzdata](https://pypi.org/project/tzdata/).
