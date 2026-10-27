"""Small presentation-only controls; the native notebook owns page state."""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont

from .theme import BG, WHITE, GREEN, INK, MUTED, BORDER, FONT


def _rounded_points(left, top, right, bottom, radius):
    radius = min(radius, (right-left)/2, (bottom-top)/2)
    return (left+radius,top,right-radius,top,right,top,right,top+radius,
            right,bottom-radius,right,bottom,right-radius,bottom,left+radius,bottom,
            left,bottom,left,bottom-radius,left,top+radius,left,top)


class SegmentedTabs(tk.Canvas):
    """A keyboard-operable segmented bar synchronized with a ttk.Notebook.

    The moving selection is decorative: pages change immediately and no input
    waits for animation. Rapid navigation replaces the previous transition.
    """
    def __init__(self, parent, notebook, motion, labels):
        super().__init__(parent, height=42, background=BG, highlightthickness=0,
                         borderwidth=0, takefocus=True, cursor='hand2')
        self.notebook, self.motion, self.labels = notebook, motion, tuple(labels)
        self.selected_index = 0
        self._pill_x = 4.0
        self._cell = 90.0
        self._track_width = 360.0
        self._focus = False
        self._font = tkfont.Font(root=self, family=FONT, size=10)
        self._key = ('tabs', str(self))
        self._notebook_binding = notebook.bind('<<NotebookTabChanged>>', self._sync, add='+')
        self.bind('<Configure>', self._layout)
        self.bind('<Button-1>', self._click)
        self.bind('<Left>', lambda e:self._key_select(self.selected_index-1))
        self.bind('<Right>', lambda e:self._key_select(self.selected_index+1))
        self.bind('<Home>', lambda e:self._key_select(0))
        self.bind('<End>', lambda e:self._key_select(len(self.labels)-1))
        self.bind('<Return>', lambda e:self._key_select(self.selected_index))
        self.bind('<space>', lambda e:self._key_select(self.selected_index))
        self.bind('<FocusIn>', lambda e:self._focus_changed(True))
        self.bind('<FocusOut>', lambda e:self._focus_changed(False))
        self.bind('<Destroy>', self._destroyed, add='+')
        self._sync()

    def select(self, index):
        index = max(0, min(len(self.labels)-1, int(index)))
        self.notebook.select(index)
        self._sync()

    def _key_select(self, index):
        self.select(index)
        return 'break'

    def _click(self, event):
        if 0 <= event.x <= self._track_width:
            self.focus_set()
            self.select(min(len(self.labels)-1, int(event.x/self._cell)))

    def _layout(self, _event=None):
        available = max(1, self.winfo_width())
        desired = (max(self._font.measure(label) for label in self.labels)+32)*len(self.labels)
        self._track_width = min(available, desired)
        self._cell = self._track_width/len(self.labels)
        self.motion.cancel(self._key, finish=False)
        self._pill_x = 4+self._cell*self.selected_index
        self._draw()

    def _sync(self, _event=None):
        try:index = self.notebook.index('current')
        except tk.TclError:return
        changed = index != self.selected_index
        self.selected_index = index
        if not changed:
            self._draw()
            return
        start, end = self._pill_x, 4+self._cell*index
        def step(progress):
            if self.winfo_exists():
                self._pill_x = start+(end-start)*progress
                self._draw()
        if not self.motion.run(self._key, step, duration=180):step(1)

    def _focus_changed(self, focused):
        self._focus = focused
        self._draw()

    def _draw(self):
        if not self.winfo_exists():return
        self.delete('all')
        width = self._track_width
        self.create_polygon(_rounded_points(0,2,width,40,12), smooth=True,
                            fill='#E8E8ED', outline=GREEN if self._focus else '', width=1)
        left = self._pill_x
        right = left+self._cell-8
        self.create_polygon(_rounded_points(left,6,right,38,9), smooth=True,
                            fill='#D7D7DE', outline='')
        self.create_polygon(_rounded_points(left,5,right,36,9), smooth=True,
                            fill=WHITE, outline='')
        for index,label in enumerate(self.labels):
            self.create_text(self._cell*(index+.5),21,text=label,font=self._font,
                             fill=INK if index==self.selected_index else MUTED)

    def _destroyed(self, event):
        if event.widget is self:
            self.motion.cancel(self._key, finish=False)
            try:self.notebook.unbind('<<NotebookTabChanged>>',self._notebook_binding)
            except tk.TclError:pass
