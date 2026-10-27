"""A read-only script viewer; the selected file is never imported or executed."""
from pathlib import Path
import sys


def main(argv=None):
    import tkinter as tk
    from tkinter import font, ttk
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        raise ValueError('需要一个本机文件路径。')
    path = Path(args[0])
    with path.open('rb') as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    truncated = len(raw) > 2 * 1024 * 1024
    content = raw[:2 * 1024 * 1024].decode('utf-8-sig', errors='replace')
    root = tk.Tk()
    root.title(path.name + ' · 只读文本')
    root.geometry('900x650')
    available = set(font.families(root))
    family = next((name for name in ('Noto Sans CJK SC', 'WenQuanYi Micro Hei', 'DejaVu Sans') if name in available), 'TkDefaultFont')
    ttk.Label(root, text=str(path), wraplength=850, padding=12).pack(fill='x')
    frame = ttk.Frame(root); frame.pack(fill='both', expand=True)
    text = tk.Text(frame, wrap='none', font=(family, 11), padx=12, pady=10)
    vertical = ttk.Scrollbar(frame, command=text.yview)
    horizontal = ttk.Scrollbar(frame, orient='horizontal', command=text.xview)
    text.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    frame.rowconfigure(0, weight=1); frame.columnconfigure(0, weight=1)
    text.grid(row=0, column=0, sticky='nsew')
    vertical.grid(row=0, column=1, sticky='ns'); horizontal.grid(row=1, column=0, sticky='ew')
    text.insert('1.0', content); text.configure(state='disabled')
    ttk.Label(root, text='只读查看，不执行脚本。' + (' 文件较大，仅显示前 2 MiB。' if truncated else ''), padding=10).pack(fill='x')
    root.bind('<Escape>', lambda _event: root.destroy())
    root.mainloop()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('无法查看文本：' + str(exc), file=sys.stderr)
        raise SystemExit(2)
