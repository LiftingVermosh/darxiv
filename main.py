"""Root Flet entry point for ``flet run``.

Flet CLI resolves imports relative to the script being launched. Keeping a
thin root-level entry point allows ``flet run main.py`` and
``flet run --web main.py`` to work from the project root while the real app
bootstrap remains in :mod:`app.main`.
"""

from app.main import main


if __name__ == "__main__":
    main()
