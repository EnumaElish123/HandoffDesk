"""Friendly reporting also works under pythonw, where stderr is absent."""
from pathlib import Path
import argparse
import sys
import traceback


def run():
    # pythonw has no console streams. Keep library errors usable and visible.
    log = None
    streams = sys.stdout, sys.stderr
    windowed = sys.stderr is None or sys.stdout is None
    try:
        if windowed:
            from .registry import Registry
            from .paths import app_directory, configuration_path
            options = argparse.ArgumentParser(add_help=False)
            options.add_argument('--app-dir', type=Path)
            options.add_argument('--data-dir', type=Path)
            paths, _ = options.parse_known_args()
            app_dir = app_directory(paths.app_dir)
            config = configuration_path(app_dir)
            projects = Registry(config).projects if config.exists() else []
            folder = Path(paths.data_dir or app_dir / 'data').resolve()
            log_path = (folder / 'startup.log').resolve()
            if not log_path.is_relative_to(folder) or any(folder.is_relative_to(Path(root).resolve()) or log_path.is_relative_to(Path(root).resolve())
                    for project in projects for root in project['roots'].values()):
                raise ValueError('工具日志路径指向业务项目或数据目录之外，已停止写入。')
            folder.mkdir(parents=True, exist_ok=True)
            log = log_path.open('a', encoding='utf-8')
            sys.stdout = sys.stderr = log
        from .cli import main
        result = main()
        if result and log and sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, "启动未完成。详情见：" + str(log.name), "HandoffDesk", 0x10)
        return result
    except Exception as exc:
        if isinstance(exc, ModuleNotFoundError) and exc.name in {'tkinter', '_tkinter'}:
            if sys.stderr is not None:
                print('桌面窗口需要 python3-tk。Ubuntu 可安装 python3-tk；服务器的命令记录功能不需要桌面。', file=sys.stderr)
            return 2
        if type(exc).__module__ == '_tkinter':
            if sys.stderr is not None:
                print('无法连接桌面显示服务：' + str(exc) + '。请在 Ubuntu 桌面会话启动；服务器请使用记录命令。', file=sys.stderr)
            return 2
        detail = traceback.format_exc()
        if sys.stderr is not None:
            print(detail, file=sys.stderr)
        if windowed and sys.platform == 'win32':
            import ctypes
            message = '启动遇到错误。详情见：' + str(log.name) if log else str(exc)
            ctypes.windll.user32.MessageBoxW(None, message + ('\n' + detail[-1000:] if log else ''), "HandoffDesk", 0x10)
        return 1
    finally:
        if log:
            log.flush()
            sys.stdout, sys.stderr = streams
            log.close()


if __name__ == "__main__":
    raise SystemExit(run())
