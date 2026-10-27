"""Generate a static page using only the Python standard library."""

from __future__ import annotations

from pathlib import Path


def main() -> None:
    output = Path(__file__).resolve().parent / "dist"
    output.mkdir(exist_ok=True)
    (output / "index.html").write_text(
        '<!doctype html>\n<html lang="en">\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<title>Python static example</title>\n<main>\n'
        '<h1>Python static example</h1>\n'
        '<p>Generated on the builder; served as static files on the target.</p>\n'
        '</main>\n</html>\n',
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
