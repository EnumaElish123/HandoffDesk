"""Run the portable suite, or the server subset without importing Tk."""
import argparse
import json
import os
from pathlib import Path
import sys
import unittest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--headless', action='store_true', help='Exclude desktop/Tk tests on servers')
    options = parser.parse_args()
    folder = Path(__file__).resolve().parent
    os.chdir(folder.parent)
    sys.path[:0] = [str(folder.parent), str(folder)]
    suite = unittest.TestSuite()
    excluded = []
    for path in sorted(folder.glob('test_*.py')):
        if options.headless and (path.stem.startswith(('test_desktop', 'test_reader')) or path.stem == 'test_motion'):
            excluded.append(path.name)
            continue
        suite.addTests(unittest.defaultTestLoader.loadTestsFromName(path.stem))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(json.dumps({'platform': sys.platform, 'headless': options.headless, 'total': result.testsRun,
                      'failed': len(result.failures), 'errors': len(result.errors),
                      'skipped': len(result.skipped), 'excluded_gui_modules': excluded,
                      'tkinter_imported': 'tkinter' in sys.modules}, ensure_ascii=False))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
