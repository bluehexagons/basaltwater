"""Offline destination page contracts without a browser or network."""

from __future__ import annotations

import base64
import hashlib
from html.parser import HTMLParser
import unittest

from desktop.kiosk_home import render_home


class Page(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.elements = []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class KioskHomeTests(unittest.TestCase):
    def test_literal_links_preserve_host_and_destination_order_without_markup_injection(self):
        url = 'https://pve.example:8006/?q="quoted"&x=1#node'
        destinations = [{"name": '<script>alert("name")</script>', "url": "http://guest.example/?a=1&b=2"},
                        {"name": "Coding VM", "url": "https://coding.example/"}]
        content = render_home(url, destinations)
        page = Page(content)
        links = [attrs for tag, attrs in page.elements if tag == "a"]
        self.assertEqual([link["href"] for link in links], [url, *(item["url"] for item in destinations)])
        self.assertTrue(all(link["target"] == "_blank" and link["rel"] == "noopener noreferrer" for link in links))
        self.assertIn('&lt;script&gt;alert(&quot;name&quot;)&lt;/script&gt;', content)
        self.assertNotIn("script", [tag for tag, _ in page.elements])
        self.assertNotIn("?q=", content.partition('class="address"')[2].partition("</span>")[0])

    def test_page_has_no_external_assets_and_only_allows_its_exact_styles(self):
        content = render_home("http://localhost/", [{"name": "VM", "url": "https://vm.example/"}])
        page = Page(content)
        self.assertFalse(any(tag in {"script", "link", "iframe", "form", "img"} for tag, _ in page.elements))
        policy = next(attrs["content"] for tag, attrs in page.elements
                      if tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy")
        styles = content.partition("<style>")[2].partition("</style>")[0]
        digest = base64.b64encode(hashlib.sha256(styles.encode()).digest()).decode("ascii")
        self.assertIn(f"style-src 'sha256-{digest}'", policy)
        self.assertIn("default-src 'none'", policy)
        self.assertIn("base-uri 'none'", policy)
        self.assertNotIn("unsafe-inline", policy)
        self.assertIn(":focus-visible", styles)

    def test_invalid_destination_or_host_never_renders_a_link(self):
        for url, destinations in (("file:///etc/passwd", []),
                                  ("http://localhost/", [{"name": "Bad", "url": "javascript:alert(1)"}])):
            with self.subTest(url=url, destinations=destinations), self.assertRaises(ValueError):
                render_home(url, destinations)
