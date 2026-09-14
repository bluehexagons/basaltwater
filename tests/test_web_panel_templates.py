"""Shared web-panel document and navigation rendering tests."""

from __future__ import annotations

import unittest

from common.service_tools.web_panel_templates import (
    panel_navigation,
    render_document,
    render_sidebar,
)


class WebPanelTemplateTest(unittest.TestCase):
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
