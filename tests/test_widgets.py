from __future__ import annotations

from chatalyst.widgets.bookmark_panel import BookmarkPanel
from chatalyst.widgets.command_palette import CommandPalette
from chatalyst.widgets.notes_panel import NotesPanel
from chatalyst.widgets.search_dialog import SearchDialog


def test_modal_cancel_actions_dismiss_with_none(monkeypatch):
    screens = [SearchDialog(), CommandPalette(), BookmarkPanel([]), NotesPanel([])]

    for screen in screens:
        dismissed: list[object] = []
        monkeypatch.setattr(screen, "dismiss", dismissed.append)

        screen.action_cancel()

        assert dismissed == [None]
