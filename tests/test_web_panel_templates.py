"""Shared web-panel document and navigation rendering tests."""

from __future__ import annotations

import unittest

from common.service_tools.web_panel_templates import (
    BRAND_PALETTES,
    panel_navigation,
    render_document,
    render_sidebar,
)


class WebPanelTemplateTest(unittest.TestCase):
    def test_palette_keeps_text_controls_and_status_readable_in_both_themes(self) -> None:
        def luminance(color: str) -> float:
            values = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
            linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
            return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))

        for theme, palette in BRAND_PALETTES.items():
            for background in ("bg", "panel", "accent-soft"):
                for foreground in ("text", "muted", "accent", "ok", "bad", "warning"):
                    with self.subTest(theme=theme, foreground=foreground, background=background):
                        a, b = sorted((luminance(palette[foreground]), luminance(palette[background])))
                        self.assertGreaterEqual((b + .05) / (a + .05), 4.5)
            for background in ("bg", "panel"):
                a, b = sorted((luminance(palette["line"]), luminance(palette[background])))
                self.assertGreaterEqual((b + .05) / (a + .05), 3)

    def test_navigation_keeps_core_links_and_marks_only_current_page(self) -> None:
        sidebar = render_sidebar(
            panel_navigation(current="jobs", include_notifications=True)
        )

        self.assertIn('href="/"', sidebar)
        self.assertIn('href="/#services-heading"', sidebar)
        self.assertIn('href="/#audit-heading"', sidebar)
        self.assertIn('href="/#notifications-heading"', sidebar)
        self.assertIn('href="/#access-heading"', sidebar)
        self.assertEqual(sidebar.count('aria-current="page"'), 1)
        self.assertIn('href="/jobs" aria-current="page"', sidebar)

    def test_document_has_one_shared_shell_and_escapes_title(self) -> None:
        document = render_document(
            title="View <test>",
            style="body { color: red; }",
            header="<header>Header</header>",
            content="<p>Content</p>",
            navigation=panel_navigation(current="dashboard"),
            footer="<footer>Footer</footer>",
        )

        self.assertEqual(document.count('<nav class="sidebar"'), 1)
        self.assertIn("View &lt;test&gt;", document)
        self.assertIn('href="#main"', document)
        self.assertIn("<header>Header</header>", document)
        self.assertIn("<footer>Footer</footer>", document)


if __name__ == "__main__":
    unittest.main()
