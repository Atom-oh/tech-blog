"""Regression coverage for opening generated pages directly and under a URL prefix."""
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import urljoin, urlsplit
from unittest.mock import patch

import build


class References(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.values = []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in {"href", "src", "poster"}:
                self.values.append(value)


class GeneratedLinksTest(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def output(self, rel, content):
        with patch.object(build, "ROOT", self.root):
            build.write(rel, content)
        return self.root / rel

    def test_nested_page_media_resolves_to_real_site_assets(self):
        assets = self.root / "assets"
        assets.mkdir()
        for name in ["demo.webm", "poster.jpg", "captions.vtt", "style.css"]:
            (assets / name).write_bytes(b"fixture")
        page = self.output("aurora/index.html", '<video poster="/assets/poster.jpg">'
                           '<source src="/assets/demo.webm"><track src="/assets/captions.vtt">'
                           '</video><link href="/assets/style.css">')
        for reference in References(page.read_text()).values:
            resolved = urlsplit(urljoin(page.as_uri(), reference))
            self.assertTrue(Path(resolved.path).is_file(), reference)
            self.assertEqual(Path(resolved.path).parent, assets)

    def test_home_asset_resolves_under_hosted_subdirectory(self):
        page = self.output("index.html", '<img src="/assets/cover.svg">')
        reference = References(page.read_text()).values[0]
        self.assertEqual(urljoin("https://example.com/preview/index.html", reference),
                         "https://example.com/preview/assets/cover.svg")

    def test_directory_links_open_index_files_and_keep_fragment(self):
        page = self.output("aurora/index.html", '<a href="/">Home</a><a href="/tags/#aurora">Tag</a>')
        refs = References(page.read_text()).values
        self.assertEqual(urljoin(page.as_uri(), refs[0]), (self.root / "index.html").as_uri())
        self.assertEqual(urljoin(page.as_uri(), refs[1]),
                         (self.root / "tags/index.html").as_uri() + "#aurora")

    def test_external_urls_and_local_anchors_are_preserved(self):
        refs = ["https://docs.aws.amazon.com/?a=1&amp;b=2", "//cdn.example.com/x.js", "#manual"]
        page = self.output("aurora/index.html", "".join(f'<a href="{r}">Link</a>' for r in refs))
        self.assertEqual(References(page.read_text()).values,
                         ["https://docs.aws.amazon.com/?a=1&b=2", "//cdn.example.com/x.js", "#manual"])

    def test_feed_links_are_not_rewritten(self):
        content = '<rss><link href="/assets/file"/><link>https://blog.atomai.click/aurora/</link></rss>'
        page = self.output("feed.xml", content)
        self.assertEqual(page.read_text(), content)

    def test_asset_directory_links_open_existing_readme(self):
        directory = self.root / "assets/data/lab"
        directory.mkdir(parents=True)
        (directory / "README.md").write_text("Download instructions")
        page = self.output("aurora/index.html", '<a href="/assets/data/lab/">Download</a>')
        reference = References(page.read_text()).values[0]
        self.assertEqual(urljoin(page.as_uri(), reference), (directory / "README.md").as_uri())

    def test_directory_without_landing_file_remains_a_directory(self):
        (self.root / "assets/data/lab").mkdir(parents=True)
        page = self.output("aurora/index.html", '<a href="/assets/data/lab/">Download</a>')
        reference = References(page.read_text()).values[0]
        self.assertEqual(urljoin("https://example.com/preview/aurora/index.html", reference),
                         "https://example.com/preview/assets/data/lab/")


if __name__ == "__main__":
    unittest.main()
