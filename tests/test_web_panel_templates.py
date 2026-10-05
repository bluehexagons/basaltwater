"""Shared web-panel document and navigation rendering tests."""

from __future__ import annotations

import unittest
from io import BytesIO
from unittest.mock import Mock
import xml.etree.ElementTree as ET

from common.service_tools import web_panel_service as panel
from common.service_tools.web_panel_templates import (
    BRAND_PALETTES,
    panel_navigation,
    render_document,
    render_favicon,
    render_sidebar,
)


class WebPanelTemplateTest(unittest.TestCase):
    def test_palette_keeps_text_controls_and_status_readable_in_both_themes(self) -> None:
        def luminance(color: str) -> float:
            values = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
            linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
            return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))

        for theme, palette in BRAND_PALETTES.items():
            for background in ("bg", "panel", "accent-soft", "workspace-soft", "stone-soft", "sea-soft", "bad-soft"):
                for foreground in ("text", "muted", "accent", "ok", "bad", "warning", "workspace", "stone", "sea"):
                    with self.subTest(theme=theme, foreground=foreground, background=background):
                        a, b = sorted((luminance(palette[foreground]), luminance(palette[background])))
                        self.assertGreaterEqual((b + .05) / (a + .05), 4.5)
            for background in ("bg", "panel"):
                a, b = sorted((luminance(palette["line"]), luminance(palette[background])))
                self.assertGreaterEqual((b + .05) / (a + .05), 3)

    def test_navigation_keeps_core_links_and_marks_only_current_page(self) -> None:
        sidebar = render_sidebar(
            panel_navigation(current="jobs")
        )

        self.assertIn('href="/"', sidebar)
        self.assertIn('href="/#services-heading"', sidebar)
        self.assertIn('href="/#audit-heading"', sidebar)
        self.assertIn('href="/#notifications-heading"', sidebar)
        self.assertIn('href="/#access-heading"', sidebar)
        self.assertIn('href="/#trust"', sidebar)
        self.assertIn('href="/#maintenance-heading"', sidebar)
        self.assertEqual(sidebar.count('aria-current="page"'), 1)
        self.assertIn('href="/jobs" aria-current="page"', sidebar)

    def test_every_view_has_identical_navigation_destinations(self) -> None:
        expected = [(href, label) for href, label, _ in panel_navigation(current="dashboard")]
        for current in ("agents", "agent-tools", "admin", "services", "jobs", "logs"):
            with self.subTest(current=current):
                self.assertEqual([(href, label) for href, label, _ in panel_navigation(current=current)], expected)

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
        self.assertIn("<header>Header<svg", document)
        self.assertIn('class="brand-texture brand-texture-basalt"', document)
        self.assertIn("<footer>Footer</footer>", document)
        self.assertIn('rel="icon" href="/favicon.svg" type="image/svg+xml"', document)

    def test_favicon_route_serves_only_vector_artwork_without_host_state(self) -> None:
        handler = object.__new__(panel.WebPanelHandler)
        handler.path = "/favicon.svg"
        handler._send = Mock()
        handler.do_GET()
        handler._send.assert_called_once_with(panel.HTTPStatus.OK, render_favicon(), "image/svg+xml")
        root = ET.fromstring(handler._send.call_args.args[1])
        self.assertEqual(root.attrib["viewBox"], "0 0 32 32")
        self.assertIn("prefers-color-scheme:dark", root.find("{http://www.w3.org/2000/svg}style").text)
        self.assertTrue(all(node.tag.rsplit("}", 1)[-1] in {"svg", "style", "path"} for node in root.iter()))

    def test_favicon_csp_allows_same_origin_images_and_keeps_scripts_blocked(self) -> None:
        handler = object.__new__(panel.WebPanelHandler)
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.wfile = BytesIO()
        handler._send(panel.HTTPStatus.OK, render_favicon(), "image/svg+xml")
        headers = dict(call.args for call in handler.send_header.call_args_list)
        policy = headers["Content-Security-Policy"]
        self.assertIn("img-src 'self'", policy)
        self.assertIn("default-src 'none'", policy)
        self.assertNotIn("data:", policy)
        self.assertNotIn("script-src", policy)
        self.assertEqual(headers["Content-Type"], "image/svg+xml; charset=utf-8")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")


if __name__ == "__main__":
    unittest.main()
