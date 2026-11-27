"""Disposable GTK application exercised by the managed desktop smoke check."""

from __future__ import annotations

from pathlib import Path
import sys


def main(document: Path) -> None:
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    window = Gtk.Window(title=f"Desktop check — {document.parent}")
    window.set_default_size(420, 220)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    editor = Gtk.TextView()
    buffer = editor.get_buffer()
    buffer.set_text(document.read_text(encoding="utf-8"))

    def save(_button):
        text = buffer.get_text(*buffer.get_bounds(), True)
        document.write_text(text, encoding="utf-8")

    def find(_button):
        dialog = Gtk.Dialog(title="Find", transient_for=window, modal=True)
        entry = Gtk.Entry()
        dialog.get_content_area().add(entry)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        dialog.connect("response", lambda widget, _response: widget.destroy())
        dialog.show_all()
        entry.grab_focus()

    for label, action in (("Save", save), ("Find", find)):
        button = Gtk.Button(label=label)
        button.connect("clicked", action)
        box.pack_start(button, False, False, 0)
    box.pack_start(editor, True, True, 0)
    window.add(box)
    window.connect("destroy", Gtk.main_quit)
    window.show_all()
    Gtk.main()


if __name__ == "__main__":
    main(Path(sys.argv[1]))
