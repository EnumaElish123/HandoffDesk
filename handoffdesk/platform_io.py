"""Small desktop boundary; importing this module never imports Tk or opens UI."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def is_windows():
    return sys.platform == 'win32'


def ensure_desktop(*, tk_required=False):
    if tk_required and importlib.util.find_spec('tkinter') is None:
        raise ValueError('桌面窗口需要 python3-tk。请先安装 python3-tk；服务器的命令记录功能不需要它。')
    if is_windows():
        return
    if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        raise ValueError('当前是无桌面的服务器会话；记录和路径复制仍可使用。请在 Ubuntu 桌面会话中打开文件。')
    if tk_required and not os.environ.get('DISPLAY'):
        raise ValueError('此窗口需要 X11 或 Wayland 的 XWayland 显示连接（DISPLAY）；服务器可继续使用命令记录。')


def _desktop_open(path):
    ensure_desktop()
    target = str(Path(path).absolute())
    opener = shutil.which('xdg-open')
    command = [opener, target] if opener else None
    if command is None:
        opener = shutil.which('gio')
        command = [opener, 'open', target] if opener else None
    if command is None:
        raise ValueError('未找到桌面打开工具。请安装 xdg-utils，或使用“复制路径”。')
    # Absolute paths and argument arrays prevent filenames from becoming options
    # or shell instructions. xdg-open may stay attached to a viewer, so do not
    # wait for the viewer to exit.
    subprocess.Popen(command, shell=False)


def open_document(path):
    path = Path(path)
    if not path.exists():
        raise ValueError('文件或文件夹不存在，请刷新后重试。')
    if is_windows():
        os.startfile(str(path))
    else:
        _desktop_open(path)


def reveal_path(path):
    path = Path(path)
    if path.is_file():
        if is_windows():
            explorer = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'explorer.exe'
            command = subprocess.list2cmdline([str(explorer)]) + ' /select,"' + str(path) + '"'
            subprocess.Popen(command, shell=False)
            return
        ensure_desktop()
        gdbus = shutil.which('gdbus')
        if gdbus:
            # JSON string syntax is also a valid quoted GVariant string. URI
            # percent encoding handles quotes, commas and Unicode filenames.
            uris = json.dumps([path.absolute().as_uri()], ensure_ascii=True)
            try:
                result = subprocess.run([gdbus, 'call', '--session',
                    '--dest', 'org.freedesktop.FileManager1',
                    '--object-path', '/org/freedesktop/FileManager1',
                    '--method', 'org.freedesktop.FileManager1.ShowItems', uris, ''],
                    shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=3, check=False)
                if result.returncode == 0:
                    return
            except (OSError, subprocess.TimeoutExpired):
                pass
    folder = path if path.is_dir() else path.parent
    while not folder.is_dir() and folder != folder.parent:
        folder = folder.parent
    if not folder.is_dir():
        raise ValueError('找不到可打开的上级文件夹。')
    open_document(folder)


def open_text(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError('脚本文件不存在，请刷新后重试。')
    if is_windows():
        notepad = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'notepad.exe'
        subprocess.Popen([str(notepad), str(path)], shell=False)
        return
    ensure_desktop(tk_required=True)
    viewer = Path(__file__).with_name('text_viewer.py')
    subprocess.Popen([sys.executable, '-E', '-s', '-B', str(viewer), str(path.absolute())], shell=False)
