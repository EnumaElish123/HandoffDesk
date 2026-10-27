"""Small, cancellable Tk transitions; presentation never delays an action.

All callbacks run on Tk's UI thread. There is no timer when no transition is
active, and changing the reduced-motion preference settles current effects.
"""
from __future__ import annotations

from dataclasses import dataclass
import time
import tkinter as tk
from typing import Callable, Hashable


@dataclass
class _Animation:
    update: Callable[[float], None]
    finish: Callable[[], None] | None
    started: float
    duration: float
    after_id: str | None = None
    cleanup: Callable[[], None] | None = None


class MotionController:
    """Schedule short ease-out transitions without blocking Tk or user input."""

    def __init__(self, root, reduced=False):
        self.root = root
        self.reduced = bool(reduced)
        self.closed = False
        self._animations: dict[Hashable, _Animation] = {}
        self._destroy_binding = root.bind('<Destroy>', self._root_destroyed, add='+')

    @property
    def active_count(self):
        return len(self._animations)

    def run(self, key, update, duration=160, finish=None):
        """Animate progress from 0 to 1, replacing an older effect with this key.

        A replacement does not finish the old effect: a new selection must not
        first jump to an obsolete destination. Functional actions should run
        before this call, never wait for its optional presentation callback.
        """
        if self.closed:
            return False
        self.cancel(key, finish=False)
        animation = _Animation(update, finish, time.monotonic(), max(0, float(duration)) / 1000)
        self._animations[key] = animation
        if self.reduced or animation.duration == 0:
            self._finish(key, animation)
        elif self._call(animation.update, 0.0):
            self._schedule(key, animation)
        else:
            self._drop(key, animation)
        return True

    def cancel(self, key, finish=True):
        """Cancel one effect, optionally settling at its final visual state."""
        animation = self._animations.get(key)
        if animation is None:
            return False
        if finish and not self.closed:
            self._finish(key, animation)
        else:
            self._drop(key, animation)
        return True

    def set_reduced(self, reduced):
        self.reduced = bool(reduced)
        if self.reduced and not self.closed:
            for key, animation in tuple(self._animations.items()):
                if self._animations.get(key) is animation:
                    self._finish(key, animation)

    def close(self):
        """Release every pending Tk callback; no later updates can run."""
        if self.closed:
            return
        self.closed = True
        for key, animation in tuple(self._animations.items()):
            self._drop(key, animation)
        if self._destroy_binding:
            try:
                self.root.unbind('<Destroy>', self._destroy_binding)
            except tk.TclError:
                pass
            self._destroy_binding = None

    def fade_in(self, window):
        """Give an already usable dialog a subtle 160 ms opacity transition.

        It begins almost opaque, so a slow frame never hides content or focus.
        Unsupported alpha, cancellation and reduced motion all retain a fully
        opaque dialog. Destroying a dialog cancels its timer immediately.
        """
        if self.closed:
            return False
        key = ('window-fade', str(window))
        self.cancel(key, finish=False)
        try:
            if not window.winfo_exists():
                return False
            window.attributes('-alpha', 1.0 if self.reduced else 0.94)
        except tk.TclError:
            return False
        if self.reduced:
            return True

        binding = None

        def restore():
            try:
                if window.winfo_exists():
                    window.attributes('-alpha', 1.0)
                    if binding:
                        window.unbind('<Destroy>', binding)
            except tk.TclError:
                pass

        def update(progress):
            try:
                if not window.winfo_exists():
                    raise tk.TclError('The animated dialog was closed')
                window.attributes('-alpha', 0.94 + 0.06 * progress)
            except tk.TclError:
                restore()
                raise

        def destroyed(event):
            if event.widget is window:
                self.cancel(key, finish=False)

        try:
            binding = window.bind('<Destroy>', destroyed, add='+')
            self.run(key, update)
            animation = self._animations.get(key)
            if animation is not None:
                animation.cleanup = restore
            else:
                restore()
        except tk.TclError:
            restore()
            self.cancel(key, finish=False)
            return False
        return True

    def _root_destroyed(self, event):
        if event.widget is self.root:
            self.close()

    def _call(self, callback, *arguments):
        try:
            callback(*arguments)
            return True
        except tk.TclError:
            # A widget may disappear between a user action and the next frame.
            return False
        except Exception as exc:
            # Keep the scheduler clean, but do not hide programming failures.
            self.root.report_callback_exception(type(exc), exc, exc.__traceback__)
            return False

    def _schedule(self, key, animation):
        if self.closed or self._animations.get(key) is not animation:
            return
        try:
            animation.after_id = self.root.after(16, lambda: self._tick(key, animation))
        except tk.TclError:
            self._drop(key, animation)

    def _tick(self, key, animation):
        if self.closed or self._animations.get(key) is not animation:
            return
        animation.after_id = None
        elapsed = (time.monotonic() - animation.started) / animation.duration
        if elapsed >= 1:
            self._finish(key, animation)
            return
        progress = 1 - (1 - max(0.0, elapsed)) ** 3
        if self._call(animation.update, progress):
            self._schedule(key, animation)
        else:
            self._drop(key, animation)

    def _finish(self, key, animation):
        # Remove before invoking callbacks, so a callback can start a new effect
        # with the same key without the old completion cancelling the new one.
        if self._animations.get(key) is not animation:
            return
        self._drop(key, animation)
        if not self.closed and self._call(animation.update, 1.0):
            if not self.closed and animation.finish:
                self._call(animation.finish)

    def _drop(self, key, animation):
        if self._animations.get(key) is not animation:
            return
        del self._animations[key]
        if animation.after_id:
            try:
                self.root.after_cancel(animation.after_id)
            except tk.TclError:
                pass
            animation.after_id = None
        if animation.cleanup:
            cleanup, animation.cleanup = animation.cleanup, None
            self._call(cleanup)
