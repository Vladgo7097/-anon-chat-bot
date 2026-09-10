"""The informational site: redirects, robots, sitemap and the page markup.

Everything here is about how search engines and browsers see the site, which
is exactly the part no other test covers.
"""
import io
import re
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from web.app import PAGE_DIRECTORIES, STATIC_DIR, app

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
PAGES = sorted(STATIC_DIR.rglob("index.html"))


def read(path):
    return io.open(path, encoding="utf-8").read()


class TrailingSlashTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, follow_redirects=False)

    def test_every_page_directory_was_discovered(self):
        self.assertIn("/vozmozhnosti", PAGE_DIRECTORIES)
        self.assertIn("/en/faq", PAGE_DIRECTORIES)
        self.assertNotIn("/", PAGE_DIRECTORIES)

    def test_a_missing_slash_is_a_permanent_redirect(self):
        for directory in sorted(PAGE_DIRECTORIES):
            with self.subTest(directory):
                response = self.client.get(directory)
                self.assertEqual(response.status_code, 301)
                self.assertTrue(response.headers["location"].endswith(directory+"/"))

    def test_the_redirect_keeps_the_scheme_the_proxy_reported(self):
        # nginx terminates TLS and forwards X-Forwarded-Proto; sending the
        # visitor to http:// would downgrade and add a second redirect.
        response = self.client.get("/vozmozhnosti", headers={
            "x-forwarded-proto": "https", "host": "anonimchat.pp.ua"})
        self.assertEqual(response.headers["location"],
                         "https://anonimchat.pp.ua/vozmozhnosti/")

    def test_the_page_itself_is_untouched(self):
        response = self.client.get("/vozmozhnosti/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Возможности", response.text)

    def test_an_unknown_path_still_gets_the_themed_404(self):
        for path, marker in (("/net-takoy", "Такой страницы нет"),
                             ("/en/no-such-page", "Page not found")):
            with self.subTest(path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertIn(marker, response.text)


class CacheHeaderTests(unittest.TestCase):
    """Every asset used to come back with no Cache-Control at all."""

    def setUp(self):
        self.client = TestClient(app)

    def test_html_always_revalidates(self):
        for path in ("/", "/vozmozhnosti/", "/en/faq/"):
            with self.subTest(path):
                self.assertEqual(self.client.get(path).headers["cache-control"], "no-cache")

    def test_images_are_cached_for_a_month_and_css_for_a_day(self):
        self.assertEqual(self.client.get("/img/logo.webp").headers["cache-control"],
                         "public, max-age=2592000")
        self.assertEqual(self.client.get("/css/site.css").headers["cache-control"],
                         "public, max-age=86400")

    def test_robots_and_sitemap_are_cached_briefly(self):
        for path in ("/robots.txt", "/sitemap.xml"):
            with self.subTest(path):
                self.assertEqual(self.client.get(path).headers["cache-control"],
                                 "public, max-age=3600")

    def test_the_404_page_is_never_cached(self):
        response = self.client.get("/net-takoy")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["cache-control"], "no-cache")

    def test_an_unknown_extension_is_not_cached_by_accident(self):
        from web.app import cache_control
        self.assertEqual(cache_control("/some/file.weird"), "no-cache")


class LargestImageTests(unittest.TestCase):
    def test_the_hero_avatar_stays_small_enough_to_be_the_lcp(self):
        # It was an 868 KB PNG loaded eagerly on both home pages.
        avatar = STATIC_DIR/"img"/"avatar-owner-lg.webp"
        self.assertTrue(avatar.exists(), "the avatar should be webp, not png")
        self.assertLess(avatar.stat().st_size, 300*1024)

    def test_no_page_still_points_at_the_old_png(self):
        for page in PAGES:
            with self.subTest(page.name):
                self.assertNotIn("avatar-owner-lg.png", read(page))


class RobotsTests(unittest.TestCase):
    def setUp(self):
        self.text = read(STATIC_DIR/"robots.txt")

    def test_the_sitemap_is_announced(self):
        self.assertIn("Sitemap: https://anonimchat.pp.ua/sitemap.xml", self.text)

    def test_pages_stay_crawlable(self):
        self.assertIn("Allow: /", self.text)

    def test_the_json_endpoints_are_kept_out_of_the_index(self):
        # /api/online and /api/visits answer plain JSON to anyone, including
        # a crawler, and would be indexed as thin pages.
        self.assertIn("Disallow: /api/", self.text)


class SitemapTests(unittest.TestCase):
    def setUp(self):
        self.root = ET.fromstring(read(STATIC_DIR/"sitemap.xml"))
        self.urls = self.root.findall("sm:url", SITEMAP_NS)

    def test_every_page_on_disk_is_listed(self):
        listed = {u.find("sm:loc", SITEMAP_NS).text for u in self.urls}
        for page in PAGES:
            directory = page.parent.relative_to(STATIC_DIR).as_posix()
            url = "https://anonimchat.pp.ua/" + ("" if directory == "." else directory+"/")
            with self.subTest(url):
                self.assertIn(url, listed)

    def test_every_entry_carries_a_last_modified_date(self):
        # Google reads lastmod to decide what to re-crawl; it ignores
        # changefreq and priority entirely.
        for url in self.urls:
            loc = url.find("sm:loc", SITEMAP_NS).text
            with self.subTest(loc):
                lastmod = url.find("sm:lastmod", SITEMAP_NS)
                self.assertIsNotNone(lastmod, f"{loc} has no lastmod")
                self.assertRegex(lastmod.text, r"^\d{4}-\d{2}-\d{2}$")

    def test_no_url_is_listed_twice(self):
        locs = [u.find("sm:loc", SITEMAP_NS).text for u in self.urls]
        self.assertEqual(len(locs), len(set(locs)))


class PageMarkupTests(unittest.TestCase):
    """Guards the head tags that are easy to lose in a copy-paste edit."""

    def pages(self):
        for page in PAGES:
            yield page.relative_to(STATIC_DIR).as_posix(), read(page)

    def test_each_page_states_its_own_canonical_address(self):
        for name, text in self.pages():
            with self.subTest(name):
                canonical = re.search(r'<link rel="canonical" href="([^"]+)"', text)
                self.assertIsNotNone(canonical, f"{name}: no canonical")
                self.assertTrue(canonical.group(1).startswith("https://anonimchat.pp.ua/"))

    def test_each_page_has_one_title_one_description_one_h1(self):
        for name, text in self.pages():
            with self.subTest(name):
                self.assertEqual(len(re.findall(r"<title[^>]*>", text)), 1)
                self.assertEqual(len(re.findall(r'<meta name="description"', text)), 1)
                self.assertEqual(len(re.findall(r"<h1[^>]*>", text)), 1)

    def test_both_languages_point_at_each_other(self):
        for name, text in self.pages():
            with self.subTest(name):
                langs = set(re.findall(r'hreflang="([^"]+)"', text))
                self.assertEqual(langs, {"ru", "en", "x-default"})

    def test_no_image_goes_without_a_description(self):
        for name, text in self.pages():
            for image in re.findall(r"<img\b[^>]*>", text):
                with self.subTest(name, image=image[:60]):
                    self.assertIn("alt=", image)

    def test_the_trail_back_to_the_homepage_is_marked_up(self):
        # BreadcrumbList is one of the few rich results still shown for a
        # site like this one; FAQPage snippets were retired in 2023.
        for name, text in self.pages():
            if name in {"index.html", "en/index.html"}:
                continue
            with self.subTest(name):
                self.assertIn('"@type": "BreadcrumbList"', text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
