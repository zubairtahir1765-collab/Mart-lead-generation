"""Chunk 7 offline check: order method + delivery detection. No internet needed.

Uses fake mart websites so every rule is tested, including the tricky cases:
share buttons, platform apps, "no delivery charge", words like "noon" and "carriage".
"""
import tempfile

import config

config.WEBSITE_CACHE_DIR = tempfile.mkdtemp()
import website  # noqa: E402

website.WEBSITE_CACHE_DIR = config.WEBSITE_CACHE_DIR
from bs4 import BeautifulSoup  # noqa: E402

from detection import (  # noqa: E402
    build_reason, collect_evidence, detect_automation, detect_delivery, detect_lead, detect_order_method,
    detection_report, merge_evidence, platform_from_url, validate_detection, EVIDENCE_VERSION,
)
from models import Lead  # noqa: E402
from website import FetchResult  # noqa: E402


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
        return FetchResult(ok=True, html=page, final_url=url, status_code=200)


def ev_of(html):
    return collect_evidence(BeautifulSoup(html, "html.parser"), html)


def order(html, hint=""):
    return detect_order_method(ev_of(html), hint)


def delivery(html, hint=""):
    return detect_delivery(ev_of(html), hint)


# ---------------------------------------------------------------- WhatsApp
print("WhatsApp")
assert ev_of('<a href="https://wa.me/447700900123">Chat</a>')["whatsapp"] == "link"
assert ev_of('<a href="https://api.whatsapp.com/send?phone=447700900123&text=Hi">x</a>')["whatsapp"] == "link"
assert ev_of('<a href="https://wa.me/?text=check%20this">Share</a>')["whatsapp"] == ""          # share button
assert ev_of('<a href="https://api.whatsapp.com/send?text=hello">Share on WhatsApp</a>')["whatsapp"] == ""
assert ev_of("<p>Order via WhatsApp on 07700 900123</p>")["whatsapp"] == "text"
assert order('<a href="https://wa.me/447700900123">Chat</a>') == "WhatsApp Orders"

# ---------------------------------------------------------------- phone orders
print("Phone orders")
for phrase in ("Call to order: 0113 496 0001", "Call us to place your order", "Phone orders welcome",
               "Orders can be placed by phone", "For orders please call 0113 496 0001", "Order by phone today"):
    assert ev_of(f"<p>{phrase}</p>")["call_to_order"], phrase
for phrase in ("Call us any time with questions", "Our phone number is 0113 496 0001"):
    assert not ev_of(f"<p>{phrase}</p>")["call_to_order"], phrase

# ---------------------------------------------------------------- website orders
print("Website orders")
assert ev_of('<button>Add to Cart</button>')["cart"] == "text"
assert ev_of('<img src="https://cdn.shopify.com/s/files/1/0001/logo.png">')["cart"] == "marker"
assert ev_of('<a href="/cart">Basket</a>')["cart"] == "marker"
assert ev_of('<script>var s="add to cart";</script><p>Welcome</p>')["cart"] == ""               # JS strings do not count
assert order("<p>Order online for collection</p>") == "Website Orders"
assert order("<p>We sell rice, flour and spices.</p>") == "Unknown"

# ---------------------------------------------------------------- mobile app
print("Mobile app")
assert ev_of('<a href="https://apps.apple.com/gb/app/asian-mart/id123">iOS</a>')["app"] == "link"
assert ev_of('<a href="https://play.google.com/store/apps/details?id=uk.co.asianmart">Android</a>')["app"] == "link"
assert ev_of('<a href="https://apps.apple.com/gb/app/deliveroo-takeaway/id1">Get Deliveroo</a>')["app"] == ""   # platform's app
assert ev_of("<p>Download our app today</p>")["app"] == "text"
assert ev_of("<p>Available on the App Store</p>")["app"] == "text"
assert order('<a href="https://apps.apple.com/gb/app/asian-mart/id123">iOS</a>') == "Mobile App"

# ---------------------------------------------------------------- delivery platforms
print("Delivery platforms")
e = ev_of('<a href="https://deliveroo.co.uk/menu/leeds/x">Order on Deliveroo</a><p>Also on Uber Eats</p>')
assert e["platforms"] == {"Deliveroo": "link", "Uber Eats": "text"}, e["platforms"]
assert ev_of('<a href="https://www.talabat.com/uae/store">T</a>')["platforms"] == {"Talabat": "link"}
assert ev_of('<a href="https://www.noon.com/uae-en/x">N</a>')["platforms"] == {"Noon": "link"}
assert ev_of("<p>Open at 12 noon. Carriage paid on orders.</p>")["platforms"] == {}              # everyday words
assert platform_from_url("https://www.ubereats.com/store/city-grocers") == "Uber Eats"
assert platform_from_url("citygrocers.co.uk") == "" and platform_from_url("") == ""
assert order('<a href="https://www.ubereats.com/store/x">Uber</a>') == "Delivery Platforms"

# ---------------------------------------------------------------- order method combos
print("Combos")
combo = ('<a href="https://wa.me/447700900123">WA</a><p>Call to order</p><button>Add to cart</button>'
         '<a href="https://deliveroo.co.uk/menu/x">D</a>')
assert order(combo) == "Phone Orders + WhatsApp Orders + Website Orders + Delivery Platforms"
assert order("<p>Hello</p>", hint="Deliveroo") == "Delivery Platforms"

# ---------------------------------------------------------------- delivery rules
print("Delivery")
assert delivery("<p>We deliver across Leeds. Free delivery over £30.</p>") == "Own Delivery Team"
assert delivery("<p>Our drivers deliver 7 days a week</p>") == "Own Delivery Team"
assert delivery('<a href="https://deliveroo.co.uk/menu/x">D</a>') == "Third-party Delivery"
assert delivery("<p>We deliver nationwide with DPD</p>") == "Third-party Delivery"                  # courier, no own team
assert delivery('<p>Our drivers deliver.</p><a href="https://deliveroo.co.uk/m">D</a>') == "Own Delivery Team"
assert delivery("<p>We deliver!</p><a href='https://www.ubereats.com/s'>U</a>") == "Third-party Delivery"
assert delivery("<p>Collection only</p>") == "No Delivery"
assert delivery("<p>Sorry, we don't offer delivery at the moment.</p>") == "No Delivery"
assert delivery("<p>No delivery charge on orders over £20</p>") == "Own Delivery Team"             # not a 'no delivery' sign
assert delivery("<p>No delivery on Sundays. We deliver Monday to Saturday.</p>") == "Own Delivery Team"
assert delivery("<p>Fresh halal meat and spices.</p>") == "Unknown"
assert delivery("<p>x</p>", hint="Talabat") == "Third-party Delivery"

# ---------------------------------------------------------------- reasons for Notes
print("Notes reason")
r = build_reason(ev_of('<a href="https://wa.me/447700900123">WhatsApp us</a><p>Welcome</p>'))
assert r == "Has WhatsApp button, no cart; delivery: not stated", r
r = build_reason(ev_of("<p>Welcome to our shop</p>"))
assert r == "No ordering signals on site; delivery: not stated", r
r = build_reason(ev_of('<button>Add to cart</button><a href="https://deliveroo.co.uk/m">D</a>'))
assert r == "Has online store/cart, Deliveroo link; delivery: on Deliveroo", r
assert build_reason(None, website_status="unreachable", has_site=True) == "Site could not be scanned"
assert build_reason(None) == "No website to scan"
assert "Deliveroo" in build_reason(None, platform_hint="Deliveroo")
assert len(build_reason(ev_of(combo))) < 160

# ---------------------------------------------------------------- automation evidence
print("Automation evidence")
CONTACT_FORM = '<form><input type="text" name="name"><input type="email" name="email"><textarea name="msg"></textarea><button>Send</button></form>'
assert ev_of(CONTACT_FORM)["form"] == "contact"
assert ev_of("<h2>Order form</h2>" + CONTACT_FORM)["form"] == "order"
assert ev_of('<form role="search"><input type="text"><input type="text"></form>')["form"] == ""        # search box
assert ev_of('<form><input type="email" name="email"><button>Subscribe</button></form>')["form"] == ""  # newsletter
assert ev_of('<form><input type="text"><input type="password"></form>')["form"] == ""                  # login
assert ev_of('<div class="wpcf7-form"></div>')["form"] == "contact"                                     # form plugin
assert ev_of('<script src="//embed.tawk.to/abc/default"></script>')["chatbot"] == "marker"
assert ev_of("<p>Ask our chatbot anything</p>")["chatbot"] == "text"
assert ev_of("<p>Chat with us on the phone</p>")["chatbot"] == ""
assert ev_of('<script src="https://js.hs-scripts.com/123.js"></script>')["crm"] == "marker"
assert ev_of("<p>We use a notebook, not a CRM</p>")["crm"] == ""
assert ev_of("<p>Get order confirmation by email and track your order live.</p>")["notifications"]
assert ev_of("<p>SMS updates when your driver is close</p>")["notifications"]
assert not ev_of("<p>Sign up for email updates and offers</p>")["notifications"]                      # newsletter wording

print("Automation rules")
def auto(html, status="", site=True):
    return detect_automation(ev_of(html), status, site)[0]
assert auto("<button>Add to cart</button>") == "Automated"
assert auto('<a href="https://apps.apple.com/gb/app/asian-mart/id1">iOS</a>') == "Automated"
assert auto('<script src="//embed.tawk.to/x"></script>') == "Automated"
assert auto('<script src="//js.hs-scripts.com/1.js"></script>') == "Automated"
assert auto("<p>Order confirmation and delivery tracking</p>") == "Automated"
assert auto(CONTACT_FORM) == "Semi Automated"
assert auto("<p>Order online</p>") == "Semi Automated"
assert auto("<p>Open 9 to 5. Halal meat.</p>") == "Manual"
assert auto('<a href="https://wa.me/447700900123">WA</a>') == "Manual"                      # WhatsApp chat is staff-handled
assert auto('<a href="https://deliveroo.co.uk/m">D</a>') == "Manual"                        # platform's automation, not theirs
assert auto(CONTACT_FORM + "<button>Add to cart</button>") == "Automated"                   # strongest level wins
assert detect_automation(None, "unreachable", True)[0] == "Unknown"
assert detect_automation(None, "blocked", True)[0] == "Unknown"
assert detect_automation(None, "none", False) == ("Manual", "no website, no online ordering")
assert detect_automation(ev_of(CONTACT_FORM))[1] == "website with basic contact form, no cart"
config_flag = detect_automation.__globals__
config_flag["WEBSITE_ONLY_IS_SEMI_AUTOMATED"] = True
assert auto("<p>Open 9 to 5</p>") == "Semi Automated"                                       # PDF reading: website = semi
config_flag["WEBSITE_ONLY_IS_SEMI_AUTOMATED"] = False
assert "Automation Status" in config.DROPDOWNS and "Unknown" in config.DROPDOWNS["Automation Status"]

# ---------------------------------------------------------------- evidence merge across pages
print("Merging pages")
a = ev_of("<p>Welcome</p>")
merge_evidence(a, ev_of("<p>Order via WhatsApp</p>"))
merge_evidence(a, ev_of('<a href="https://wa.me/447700900123">x</a>'))
assert a["whatsapp"] == "link"                                                                      # strongest wins
merge_evidence(a, ev_of("<p>Call to order</p><p>Collection only</p>"))
assert a["call_to_order"] and a["no_delivery"] == ["collection only"]

# ---------------------------------------------------------------- full flow through the crawler
print("Lead flow")
SITES = {
    "https://waexpress.co.uk/": '<a href="/contact">Contact</a><a href="https://wa.me/447700900123">WhatsApp</a>',
    "https://waexpress.co.uk/contact": "<p>Call to order on 0113 496 0001. We deliver locally.</p>",
    "https://shopmart.co.uk/": '<button>Add to cart</button><a href="https://www.ubereats.com/store/shopmart">Uber Eats</a>',
    "https://plain.co.uk/": "<html><body><h1>Plain Grocers</h1><p>Open 9 to 5</p></body></html>",
    "https://blocked.com/": "BLOCKED",
}


def L(name, site="", **kw):
    return Lead(business_name=name, website_url=site, country="United Kingdom", city="Leeds",
                business_type="Grocery Store", phone_number="+44 113 496 0001", **kw)


leads = [
    L("WA Express", "https://waexpress.co.uk/"),
    L("Shop Mart", "https://shopmart.co.uk/"),
    L("Plain Grocers", "https://plain.co.uk/"),
    L("Blocked", "https://blocked.com/"),
    L("Dead", "https://dead.com/"),
    L("No Site", ""),
    L("Deliveroo Only", "https://deliveroo.co.uk/menu/leeds/deliveroo-only"),
    L("FB Only", "https://www.facebook.com/FBOnly/"),
]
for i, l in enumerate(leads, 1):
    l.lead_id = f"LD-{i:04d}"

website.enrich_leads(leads, workers=2, use_cache=False, progress=False,
                     fetcher_factory=lambda: FakeFetcher(SITES))
from detection import detect_leads  # noqa: E402

detect_leads(leads)
by = {l.business_name: l for l in leads}

wa = by["WA Express"]
assert wa.order_method == "Phone Orders + WhatsApp Orders", wa.order_method          # found across two pages
assert wa.delivery_system == "Own Delivery Team"
assert wa.automation_status == "Manual"
assert "Has call-to-order text, WhatsApp button, no cart" in wa.to_sheet_dict()["Notes"]

sm = by["Shop Mart"]
assert sm.order_method == "Website Orders + Delivery Platforms" and sm.delivery_system == "Third-party Delivery"
assert len(sm.detection_note) < 260
assert sm.automation_status == "Automated" and "automation: Automated (online store)" in sm.detection_note

pl = by["Plain Grocers"]
assert (pl.order_method, pl.delivery_system, pl.automation_status) == ("Unknown", "Unknown", "Manual")
assert pl.to_sheet_dict()["Notes"].startswith("No ordering signals on site")

for name in ("Blocked", "Dead"):
    assert (by[name].order_method, by[name].delivery_system) == ("Unknown", "Unknown"), name
    assert by[name].detection_note.startswith("Site could not be scanned")
    assert by[name].automation_status == "Unknown", name
assert by["No Site"].detection_note.startswith("No website to scan")
assert by["No Site"].automation_status == "Manual"
assert by["FB Only"].order_method == "Unknown"

do = by["Deliveroo Only"]                                  # Google's "website" was a platform page
assert do.website_url == "" and do.platform_hint == "Deliveroo"
assert (do.order_method, do.delivery_system, do.automation_status) == ("Delivery Platforms", "Third-party Delivery", "Manual")

# every lead has both fields filled, never blank
assert all(l.order_method and l.delivery_system and l.automation_status for l in leads)
# still exactly the 21 sheet columns
assert list(wa.to_sheet_dict().keys()) == config.SHEET_COLUMNS
# re-running changes nothing (deterministic, no duplicated notes)
note_before = wa.to_sheet_dict()["Notes"]
detect_leads(leads)
assert wa.to_sheet_dict()["Notes"] == note_before

report = detection_report(leads)
print("  order:     ", report["order_method"])
print("  delivery:  ", report["delivery_system"])
print("  automation:", report["automation_status"])
assert report["complete"] and not report["problems"], report["problems"]
assert sum(report["automation_status"].values()) == len(leads)
# the done check catches a lead that never went through detection
fresh = L("Never Scanned")
assert not detection_report([fresh])["complete"] and len(validate_detection([fresh])) == 4
assert not detection_report([])["complete"]
bad = L("Bad Values", order_method="Carrier Pigeon", delivery_system="Drone", automation_status="Robots")
bad.detection_note = "x"
assert len(validate_detection([bad])) == 3

# ---------------------------------------------------------------- cache from before Chunk 7 is re-crawled
print("Old cache")
import json, os  # noqa: E402

url = "https://waexpress.co.uk/"
path = website._cache_path(url)
with open(path, encoding="utf-8") as f:
    entry = json.load(f)
assert entry["evidence_version"] == EVIDENCE_VERSION and entry["evidence"]["whatsapp"] == "link"
for k in ("evidence", "evidence_version"):               # make it look like a Chunk 6 cache file
    entry.pop(k)
with open(path, "w", encoding="utf-8") as f:
    json.dump(entry, f)
fk = FakeFetcher(SITES)
fresh = website.crawl_cached(url, "WA Express", use_cache=True, fetcher=fk)
assert fk.calls and fresh["evidence"]["whatsapp"] == "link"      # crawled again, not served stale
fk2 = FakeFetcher({})
website.crawl_cached(url, "WA Express", use_cache=True, fetcher=fk2)
assert not fk2.calls                                              # and now cached again
# a version-1 entry (before the automation scan) is crawled again too
with open(path, encoding="utf-8") as f:
    entry = json.load(f)
entry["evidence_version"] = 1
with open(path, "w", encoding="utf-8") as f:
    json.dump(entry, f)
fk3 = FakeFetcher(SITES)
assert website.crawl_cached(url, "WA Express", use_cache=True, fetcher=fk3)["evidence_version"] == EVIDENCE_VERSION
assert fk3.calls

print("\nChunk 7 offline check passed.")
