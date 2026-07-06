"""Single-line text field with full cursor/selection editing behaviour.

Implements the editing semantics of a browser <input> / <textarea> row:
arrow keys, Home/End, Ctrl+Arrow word-jump, Shift for selection, Backspace /
Delete, Ctrl+Backspace word-delete, Ctrl+A select-all.

No SDL or GL imports — pure Python logic so it can be unit-tested standalone.
"""
from __future__ import annotations


class TextField:
    """Editable single-line text value with cursor, selection, and view-scroll.

    Attributes
    ----------
    value : str
        Current text content.
    cursor : int
        Byte-index of the insertion point inside *value*.
    sel_start : int | None
        Anchor of an active selection, or None when no selection exists.
        The selection spans ``[min(sel_start, cursor), max(sel_start, cursor))``.
    view_start : int
        Index of the first character currently rendered in the display box.
        Updated by the renderer based on field pixel width and cursor position.
    """

    def __init__(self, value: str = '', max_len: int = 200) -> None:
        self.value: str = str(value)
        self.cursor: int = len(self.value)
        self.sel_start: int | None = None
        self.view_start: int = 0
        self._max_len = max_len

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def has_selection(self) -> bool:
        return self.sel_start is not None and self.sel_start != self.cursor

    def sel_range(self) -> tuple[int, int] | None:
        """Return (lo, hi) of the current selection or None."""
        if self.sel_start is None or self.sel_start == self.cursor:
            return None
        return min(self.sel_start, self.cursor), max(self.sel_start, self.cursor)

    def selected_text(self) -> str:
        r = self.sel_range()
        return self.value[r[0]:r[1]] if r else ''

    # ------------------------------------------------------------------
    # Cursor helpers
    # ------------------------------------------------------------------

    def _set_cursor(self, pos: int, select: bool = False) -> None:
        pos = max(0, min(len(self.value), pos))
        if select:
            if self.sel_start is None:
                self.sel_start = self.cursor
        else:
            self.sel_start = None
        self.cursor = pos

    # ------------------------------------------------------------------
    # Motion
    # ------------------------------------------------------------------

    def move_left(self, select: bool = False) -> None:
        if not select and self.has_selection:
            r = self.sel_range()
            self._set_cursor(r[0])
        else:
            self._set_cursor(self.cursor - 1, select)

    def move_right(self, select: bool = False) -> None:
        if not select and self.has_selection:
            r = self.sel_range()
            self._set_cursor(r[1])
        else:
            self._set_cursor(self.cursor + 1, select)

    def move_home(self, select: bool = False) -> None:
        self._set_cursor(0, select)

    def move_end(self, select: bool = False) -> None:
        self._set_cursor(len(self.value), select)

    def move_word_left(self, select: bool = False) -> None:
        pos = self.cursor
        while pos > 0 and self.value[pos - 1] == ' ':
            pos -= 1
        while pos > 0 and self.value[pos - 1] != ' ':
            pos -= 1
        self._set_cursor(pos, select)

    def move_word_right(self, select: bool = False) -> None:
        pos = self.cursor
        n = len(self.value)
        while pos < n and self.value[pos] != ' ':
            pos += 1
        while pos < n and self.value[pos] == ' ':
            pos += 1
        self._set_cursor(pos, select)

    def select_all(self) -> None:
        self.sel_start = 0
        self.cursor = len(self.value)

    # ------------------------------------------------------------------
    # Editing
    # ------------------------------------------------------------------

    def _delete_selection(self) -> bool:
        r = self.sel_range()
        if r is None:
            return False
        lo, hi = r
        self.value = self.value[:lo] + self.value[hi:]
        self.cursor = lo
        self.sel_start = None
        return True

    def insert(self, text: str) -> bool:
        """Insert *text* at cursor (replacing any selection). Returns True if changed."""
        self._delete_selection()
        clean = ''.join(ch for ch in text if ch >= ' ' or ch == '\t')
        if not clean:
            return False
        allowed = self._max_len - len(self.value)
        clean = clean[:allowed]
        if not clean:
            return False
        p = self.cursor
        self.value = self.value[:p] + clean + self.value[p:]
        self.cursor = p + len(clean)
        return True

    def delete_before(self) -> bool:
        """Backspace — delete char before cursor or selection."""
        if self._delete_selection():
            return True
        if self.cursor == 0:
            return False
        self.value = self.value[:self.cursor - 1] + self.value[self.cursor:]
        self.cursor -= 1
        return True

    def delete_after(self) -> bool:
        """Delete key — delete char after cursor or selection."""
        if self._delete_selection():
            return True
        if self.cursor >= len(self.value):
            return False
        self.value = self.value[:self.cursor] + self.value[self.cursor + 1:]
        return True

    def delete_word_before(self) -> bool:
        """Ctrl+Backspace — delete word before cursor."""
        old = self.cursor
        self.move_word_left()
        if self.cursor < old:
            self.value = self.value[:self.cursor] + self.value[old:]
            return True
        return False
