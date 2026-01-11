"""GUI builder entrypoint.

The actual application code lives in the gui_builder_app/ package.
"""

from __future__ import annotations

from gui_builder_app import GuiBuilderApp


def main() -> None:
    GuiBuilderApp().run()


if __name__ == "__main__":
    main()
