# Build on the target OS; PyInstaller does not cross-compile WebView2 bindings.
from pathlib import Path
from importlib.metadata import distribution, PackageNotFoundError
import sys
import sysconfig
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH).parent
datas = collect_data_files('handoffdesk')
datas += [(str(root / 'LICENSE'), 'licenses'), (str(root / 'THIRD_PARTY_NOTICES.md'), 'licenses')]
python_license = Path(sysconfig.get_path('stdlib')) / 'LICENSE.txt'
if python_license.is_file():
    datas.append((str(python_license), 'licenses/python'))
for package in ('textual', 'rich', 'markdown-it-py', 'mdit-py-plugins', 'mdurl', 'pygments',
                'platformdirs', 'typing-extensions', 'pywebview', 'proxy-tools', 'pythonnet',
                'clr-loader', 'cffi', 'pycparser', 'bottle', 'PyInstaller'):
    try:
        metadata = distribution(package)
    except PackageNotFoundError:
        continue
    for file in metadata.files or []:
        if any(part.lower().startswith(('license', 'copying', 'notice')) for part in file.parts):
            source = metadata.locate_file(file)
            if source.is_file():
                datas.append((str(source), 'licenses/' + package + '/' + str(file.parent)))
hidden = collect_submodules('handoffdesk')
excludes = []
if sys.platform == 'win32':
    hidden += collect_submodules('webview')
    datas += collect_data_files('webview')
else:
    hidden += collect_submodules('textual')
    datas += collect_data_files('textual')
    excludes = ['webview', 'tkinter', '_tkinter']

a = Analysis([str(root / 'scripts' / 'portable_entry.py')], pathex=[str(root)],
             binaries=[], datas=datas, hiddenimports=hidden, hookspath=[],
             hooksconfig={}, runtime_hooks=[], excludes=excludes, noarchive=False)
pyz = PYZ(a.pure)
if sys.platform == 'win32':
    cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name='HandoffDeskCLI', console=True)
    desktop = EXE(pyz, a.scripts, [], exclude_binaries=True, name='HandoffDesk', console=False,
                  icon=str(root / 'handoffdesk' / 'assets' / 'app.ico'))
    app = COLLECT(cli, desktop, a.binaries, a.datas, strip=False, upx=False, name='HandoffDesk')
else:
    cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name='handoffdesk', console=True)
    app = COLLECT(cli, a.binaries, a.datas, strip=False, upx=False, name='HandoffDesk')
