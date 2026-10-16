"""Chunk 6 offline check: no internet or API key needed.

Uses fake mart websites (HTML written below) so every extraction rule is tested,
including the tricky cases: junk emails, share buttons, agency footers, look-alike names.
"""
import tempfile

import config

config.WEBSITE_CACHE_DIR = tempfile.mkdtemp()           # keep test runs out of data/
import website  # noqa: E402  (imported after the cache dir is redirected)

website.WEBSITE_CACHE_DIR = config.WEBSITE_CACHE_DIR
from bs4 import BeautifulSoup  # noqa: E402

from models import Lead  # noqa: E402
from website import (  # noqa: E402
    FetchResult, apply_result, classify_website, enrich_leads, enrich_site, enrichment_report,
    extract_emails, extract_owner, extract_socials, find_contact_links, merge_social,
    social_from_url, validate_enrichment,
)


def soup(html):
    return BeautifulSoup(html, "html.parser")


# ---------------------------------------------------------------- URL cleaning
print("URL cleaning")
assert classify_website("https://asianmart.co.uk/?utm_source=google&utm_medium=organic") == \
    ("https://asianmart.co.uk/", [], "pending")
assert classify_website("asianmart.co.uk")[0] == "https://asianmart.co.uk"
url, socials, status = classify_website("https://www.facebook.com/CityGrocersLeeds/?ref=page")
assert (url, status) == ("", "social_only") and "facebook.com/CityGrocersLeeds" in socials[0]
assert classify_website("https://www.ubereats.com/store/city-grocers")[2] == "none"
assert classify_website("")[2] == "none" and classify_website("   ")[2] == "none"
assert classify_website("https://linktr.ee/citygrocers")[2] == "social_only"

# ---------------------------------------------------------------- emails
print("Emails")
html = """
<html><body>
<a href="mailto:Info@AsianMart.co.uk?subject=Hello">Email us</a>
<p>Orders: orders [at] asianmart [dot] co [dot] uk</p>
<p>Sales: sales@asianmart.co.uk</p>
<img src="logo@2x.png"><p>noreply@asianmart.co.uk</p>
<p>your@email.com</p>
<p>Website by hello@webagency.io</p>
<span class="__cf_email__" data-cfemail="%s">[protected]</span>
</body></html>"""
# cloudflare-encode shop@asianmart.co.uk with key 0x2a
key = 0x2A
cf = f"{key:02x}" + "".join(f"{ord(c) ^ key:02x}" for c in "shop@asianmart.co.uk")
emails = dict(extract_emails(soup(html % cf), html % cf, "asianmart.co.uk", is_contact_page=False))
assert "info@asianmart.co.uk" in emails, emails
assert "orders@asianmart.co.uk" in emails, emails                 # [at]/[dot] obfuscation
assert "shop@asianmart.co.uk" in emails, emails                   # cloudflare hidden
assert "sales@asianmart.co.uk" in emails
for bad in ("noreply@asianmart.co.uk", "your@email.com", "hello@webagency.io"):
    assert bad not in emails, (bad, emails)
assert not any(e.endswith(".png") for e in emails)
assert max(emails, key=emails.get) in ("info@asianmart.co.uk",), emails   # mailto + preferred prefix wins

# gmail on a small shop site is fine; unknown other-domain only counts as a mailto on a contact page
h2 = '<p>citygrocers.leeds@gmail.com</p><p>agency@designers.com</p><a href="mailto:group@othergroup.com">x</a>'
e_home = dict(extract_emails(soup(h2), h2, "citygrocers.co.uk", False))
e_contact = dict(extract_emails(soup(h2), h2, "citygrocers.co.uk", True))
assert "citygrocers.leeds@gmail.com" in e_home and "agency@designers.com" not in e_home
assert "group@othergroup.com" not in e_home and "group@othergroup.com" in e_contact
assert "agency@designers.com" not in e_contact          # plain text on contact page still not trusted

# ---------------------------------------------------------------- socials
print("Social links")
assert social_from_url("https://www.instagram.com/asianmart_leeds/?hl=en") == \
    ("instagram", "https://www.instagram.com/asianmart_leeds/")
assert social_from_url("https://instagram.com/p/Cxyz123/") is None
assert social_from_url("https://www.facebook.com/sharer/sharer.php?u=x") is None
assert social_from_url("https://www.facebook.com/tr?id=123") is None
assert social_from_url("https://www.facebook.com/AsianMartLeeds/") == \
    ("facebook", "https://www.facebook.com/AsianMartLeeds")
assert social_from_url("https://facebook.com/profile.php?id=100012345") == \
    ("facebook", "https://www.facebook.com/profile.php?id=100012345")
assert social_from_url("https://www.facebook.com/pages/City-Grocers/12345?ref=x") == \
    ("facebook", "https://www.facebook.com/pages/City-Grocers/12345")
assert social_from_url("https://www.facebook.com/wix") is None

page = """<a href="https://www.facebook.com/sharer/sharer.php?u=x">Share</a>
<a href="https://www.instagram.com/asianmart_leeds/">IG</a>
<a href="https://www.facebook.com/AsianMartLeeds/">FB</a>
<a href="https://www.instagram.com/p/abc/">post</a>"""
assert extract_socials(soup(page), page) == {
    "instagram": "https://www.instagram.com/asianmart_leeds/",
    "facebook": "https://www.facebook.com/AsianMartLeeds"}
ld = '<script type="application/ld+json">{"@type":"GroceryStore","sameAs":["https://www.facebook.com/FreshFoodsDubai","https://instagram.com/freshfoodsdxb"]}</script>'
assert extract_socials(soup(ld), ld) == {"facebook": "https://www.facebook.com/FreshFoodsDubai",
                                         "instagram": "https://www.instagram.com/freshfoodsdxb/"}

assert merge_social("", "https://www.instagram.com/a/", "") == "Instagram: https://www.instagram.com/a/"
assert merge_social("Facebook: https://www.facebook.com/x", "https://www.instagram.com/a/", "https://www.facebook.com/other") == \
    "Instagram: https://www.instagram.com/a/ | Facebook: https://www.facebook.com/x"      # never overwrites

# ---------------------------------------------------------------- owner / manager
print("Owner / manager")
cases = [
    ("<p>Owner: Ahmed Khan</p>", "Ahmed Khan (Owner)"),
    ("<p>Founded by Imran Malik in 1998 in Leeds.</p>", "Imran Malik (Founder)"),
    ("<p>Family run by Mr Tariq Hussain since 2004</p>", "Tariq Hussain (Owner)"),
    ("<h3>Sana Rafiq, Store Manager</h3>", "Sana Rafiq (Manager)"),
    ("<p>Hi, I'm Bilal Ahmed, the owner of this shop.</p>", "Bilal Ahmed (Owner)"),
    ('<script type="application/ld+json">{"@type":"Store","founder":{"@type":"Person","name":"Zainab Ali"}}</script>', "Zainab Ali (Founder)"),
]
for html_, expected in cases:
    got = extract_owner(soup(html_), "Fresh Foods")
    assert got == expected, (html_, got, expected)

false_positives = [
    "<p>Managed by Fresh Foods Ltd</p>",              # company, not a person
    "<p>Founded by Fresh Foods</p>",                  # business name again
    "<p>Opening Hours Monday to Friday</p>",
    "<p>Contact Us Today for our Store Manager</p>",
    "<p>Powered by Shopify Inc</p>",
    "<p>Owner: our team of experts</p>",
    "<p>Family owned and run by the best people</p>",
]
for html_ in false_positives:
    got = extract_owner(soup(html_), "Fresh Foods")
    assert got == "", (html_, got)

# ---------------------------------------------------------------- contact page discovery
print("Contact pages")
nav = """<a href="/contact-us">Contact</a><a href="/about">About Us</a><a href="/products">Shop</a>
<a href="https://other.com/contact">elsewhere</a><a href="/brochure.pdf">pdf</a><a href="mailto:a@b.com">m</a>
<a href="/our-story">Story</a><a href="/blog/post-1">blog</a>"""
links = find_contact_links(soup(nav), "https://asianmart.co.uk/", 3)
assert links[0].endswith("/contact-us") and len(links) == 3 and all("other.com" not in l for l in links), links


# ---------------------------------------------------------------- crawling with fake sites
class FakeFetcher:
    """Serves pages from a dict so the crawl logic runs without internet."""

    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url):
        self.calls.append(url)
        page = self.pages.get(url)
        if page is None:
            return FetchResult(error="HTTP 404", status_code=404, final_url=url)
        if page == "BLOCKED":
            return FetchResult(blocked=True, error="robots.txt disallows", final_url=url)
        if isinstance(page, FetchResult):
            return page
        return FetchResult(ok=True, html=page, final_url=url, status_code=200)


SITES = {
    "https://asianmart.co.uk/": """<a href="/contact">Contact</a><a href="/about">About</a><a href="/a">a</a><a href="/b-contact">b</a>
        <a href="https://www.instagram.com/asianmart_leeds/">IG</a>""",
    "https://asianmart.co.uk/contact": '<a href="mailto:info@asianmart.co.uk">Email</a><p>Tel 0113 496 0001</p>',
    "https://asianmart.co.uk/about": "<p>Founded by Imran Malik in 1998.</p><a href='https://facebook.com/AsianMartLeeds'>FB</a>",
    "https://emptyshop.com/": "<html><body><h1>Welcome</h1></body></html>",
    "https://blocked.com/": "BLOCKED",
    "https://tofacebook.com/": FetchResult(ok=True, html="x", final_url="https://www.facebook.com/RedirectedMart", status_code=200),
}

print("Crawling")
fk = FakeFetcher(SITES)
r = enrich_site("https://asianmart.co.uk/", "Asian Mart", fetcher=fk)
assert r["status"] == "ok" and r["emails"] == ["info@asianmart.co.uk"], r
assert r["instagram"] == "https://www.instagram.com/asianmart_leeds/"
assert r["facebook"] == "https://www.facebook.com/AsianMartLeeds"
assert r["owner"] == "Imran Malik (Founder)"
assert len(fk.calls) <= 4, fk.calls                                   # page cap respected

r = enrich_site("https://emptyshop.com/", "Empty Shop", fetcher=FakeFetcher(SITES))
assert r["status"] == "ok" and not r["emails"] and not r["owner"]
assert enrich_site("https://blocked.com/", fetcher=FakeFetcher(SITES))["status"] == "blocked"
assert enrich_site("https://dead.com/", fetcher=FakeFetcher(SITES))["status"] == "unreachable"
r = enrich_site("https://tofacebook.com/", fetcher=FakeFetcher(SITES))
assert r["status"] == "social_only" and r["facebook"] == "https://www.facebook.com/RedirectedMart"

# ---------------------------------------------------------------- full lead flow
print("Lead flow")


def L(name, site="", **kw):
    return Lead(business_name=name, website_url=site, country="United Kingdom", city="Leeds",
                business_type="Grocery Store", phone_number="+44 113 496 0001", **kw)


leads = [
    L("Asian Mart", "https://asianmart.co.uk/?utm_source=google"),            # rich site
    L("Empty Shop", "https://emptyshop.com/"),                                 # reachable, nothing to find
    L("Blocked Shop", "https://blocked.com/"),
    L("Dead Shop", "https://dead.com/"),
    L("FB Only Grocers", "https://www.facebook.com/FBOnlyGrocers/"),          # Google gave a Facebook page
    L("No Site Mart", ""),
    L("Chain A", "https://asianmart.co.uk/"),                                  # same site as Asian Mart
    L("Pre-filled", "https://asianmart.co.uk/", email="keep@me.com", owner_name="Existing Person"),
]
for i, l in enumerate(leads, 1):
    l.lead_id = f"LD-{i:04d}"

factories = []


def factory():
    f = FakeFetcher(SITES)
    factories.append(f)
    return f


enrich_leads(leads, workers=2, use_cache=False, fetcher_factory=factory, progress=False)
by = {l.business_name: l for l in leads}

a = by["Asian Mart"]
assert a.website_available == "Yes" and a.website_url == "https://asianmart.co.uk/"       # utm stripped
assert a.email == "info@asianmart.co.uk" and a.owner_name == "Imran Malik (Founder)"
assert a.social_links == "Instagram: https://www.instagram.com/asianmart_leeds/ | Facebook: https://www.facebook.com/AsianMartLeeds"
fb = by["FB Only Grocers"]
assert fb.website_available == "No" and fb.website_url == ""
assert fb.social_links == "Facebook: https://www.facebook.com/FBOnlyGrocers"
assert by["No Site Mart"].website_available == "No" and by["No Site Mart"].website_status == "none"
assert by["Dead Shop"].website_status == "unreachable" and by["Blocked Shop"].website_status == "blocked"
pre = by["Pre-filled"]
assert pre.email == "keep@me.com" and pre.owner_name == "Existing Person"               # never overwritten
assert by["Chain A"].email == "info@asianmart.co.uk"                                     # shared site, one crawl
assert len(factories) == 4, len(factories)       # asianmart (x3 leads), emptyshop, blocked, dead -> 4 crawls

row = by["Dead Shop"].to_sheet_dict()
assert row["Website Available"] == "Yes" and "Website unreachable" in row["Notes"]
assert list(row.keys()) == config.SHEET_COLUMNS                                          # still the exact 21 columns

report = enrichment_report(leads)
print("  report:", report)
assert report["website_yes"] == 6 and report["enriched"] == 3, report                  # Asian Mart, Chain A, Pre-filled
assert report["enriched_pct"] == 50.0 and report["meets_target"]
assert report["social_only_no_site"] == 1
assert not validate_enrichment(leads), validate_enrichment(leads)

# cache: second run does zero new fetches
website.crawl_cached("https://asianmart.co.uk/", "Asian Mart", use_cache=False, fetcher=FakeFetcher(SITES))
f2 = FakeFetcher({})
cached = website.crawl_cached("https://asianmart.co.uk/", "Asian Mart", use_cache=True, fetcher=f2)
assert cached["emails"] == ["info@asianmart.co.uk"] and not f2.calls
# failures are not cached (a site that is down today may be up tomorrow)
website.crawl_cached("https://dead.com/", use_cache=False, fetcher=FakeFetcher(SITES))
f3 = FakeFetcher({"https://dead.com/": "<a href='mailto:hi@dead.com'>x</a>"})
assert website.crawl_cached("https://dead.com/", use_cache=True, fetcher=f3)["emails"] == ["hi@dead.com"]

print("\nChunk 6 offline check passed.")
