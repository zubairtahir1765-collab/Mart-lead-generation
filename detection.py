"""Chunk 7 (part 1): order method and delivery detection, rule-based.

Two steps, kept apart on purpose:

  1. collect_evidence(): while the crawler already has a page open, note which
     keywords / links / shop markers it contains. Only this small dict is stored
     (and cached), never the page text.
  2. detect_order_method() / detect_delivery(): turn the evidence into the sheet
     values. Rules can be tuned and re-run without crawling again.

Fallback everywhere is "Unknown": no evidence is not proof.
"""
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Comment

from config import (
    AUTOMATION_UNKNOWN,
    CART_HTML_MARKERS,
    CHATBOT_HTML_MARKERS,
    CRM_HTML_MARKERS,
    DROPDOWNS,
    FORM_HTML_MARKERS,
    WEBSITE_ONLY_IS_SEMI_AUTOMATED,
    CART_LINK_SUFFIXES,
    COURIERS,
    DELIVERY_PLATFORMS,
    DELIVERY_UNKNOWN,
    INFER_WALK_IN_WHEN_NO_SIGNALS,
    ORDER_METHOD_UNKNOWN,
)
from models import Lead

# Bump when collect_evidence() learns to look for new things: old cache entries are re-crawled.
EVIDENCE_VERSION = 2   # v2 added forms, chatbot, CRM and notification evidence

_STRENGTH = {"": 0, "text": 1, "link": 2, "marker": 2}


# ======================================================================
# 1. Evidence
# ======================================================================
def empty_evidence() -> dict:
    return {
        "whatsapp": "",          # "link" (wa.me with a number) | "text"
        "call_to_order": False,  # "call to order", "phone orders" ...
        "cart": "",              # "marker" (shop platform / cart link) | "text" (add to cart ...)
        "order_online": False,   # "order online", "online store" ...
        "app": "",               # "link" (App Store / Google Play) | "text"
        "platforms": {},         # {"Deliveroo": "link" | "text"}
        "couriers": [],          # ["DPD"]
        "own_delivery": [],      # matched phrases, e.g. ["we deliver"]
        "own_strong": False,     # "our drivers", "delivery team" ...
        "no_delivery": [],       # "collection only" ...
        # --- added in v2 (automation) ---
        "form": "",              # "order" (order/enquiry/quote form) | "contact" (any other multi-field form)
        "chatbot": "",           # "marker" (chat widget script) | "text" ("chatbot", "virtual assistant")
        "crm": "",               # "marker" (HubSpot, Zoho, Salesforce ... script)
        "notifications": False,  # order confirmation / tracking / SMS update wording
    }


def merge_evidence(a: dict, b: dict) -> dict:
    """Combine evidence from two pages (strongest signal wins). Returns a."""
    for key in ("whatsapp", "cart", "app", "chatbot", "crm"):
        if _STRENGTH[b[key]] > _STRENGTH[a[key]]:
            a[key] = b[key]
    if b["form"] == "order" or (b["form"] and not a["form"]):      # an order form outranks a contact form
        a["form"] = b["form"]
    for key in ("call_to_order", "order_online", "own_strong", "notifications"):
        a[key] = a[key] or b[key]
    for name, how in b["platforms"].items():
        if _STRENGTH[how] > _STRENGTH.get(a["platforms"].get(name, ""), 0):
            a["platforms"][name] = how
    for key in ("couriers", "own_delivery", "no_delivery"):
        for item in b[key]:
            if item not in a[key] and len(a[key]) < 4:
                a[key].append(item)
    return a


def _host(url: str) -> str:
    try:
        h = (urlparse(url.strip()).hostname or "").lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def _host_in(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def platform_from_url(url: str) -> str:
    """Name of the delivery platform if this URL is a page on one, else ''."""
    host = _host(url if re.match(r"^https?://", url or "", re.I) else "https://" + (url or ""))
    for name, spec in DELIVERY_PLATFORMS.items():
        if host and _host_in(host, spec["hosts"]):
            return name
    return ""


_PLATFORM_TOKENS = tuple({d.split(".")[0].replace("-", "") for s in DELIVERY_PLATFORMS.values() for d in s["hosts"]}
                         | {"ubereats"})


def _visible_text(soup: BeautifulSoup) -> str:
    """Lower-cased text a visitor can see (no scripts, styles, comments)."""
    parts = []
    for s in soup.find_all(string=True):
        if isinstance(s, Comment) or (s.parent and s.parent.name in
                                      ("script", "style", "noscript", "template", "head", "title")):
            continue
        parts.append(str(s))
    text = re.sub(r"\s+", " ", " ".join(parts)).lower()
    return text.replace("\u2019", "'")


def _rx(*patterns: str) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in patterns))


WHATSAPP_LINK_RE = re.compile(
    r"wa\.me/(?:\+?\d{6,}|message/|c/)|wa\.link/|whatsapp\.com/catalog|"
    r"(?:whatsapp\.com/send/?|whatsapp://send/?)\?[^#]*phone=\+?\d{6,}")
WHATSAPP_SHARE_RE = re.compile(r"share\s+(?:\w+\s+){0,3}?whatsapp")      # share buttons are not the mart's number

CALL_TO_ORDER_RE = _rx(
    r"call (?:us )?(?:now )?(?:to|and) (?:place (?:an |your )?)?order",
    r"ring (?:us )?(?:to|and) order",
    r"(?:phone|telephone|call) orders?\b",
    r"orders? (?:can be )?(?:placed )?(?:by|via|over) (?:the )?(?:phone|telephone|call)",
    r"call in (?:your |an )?orders?",
    r"phone (?:us )?to order",
    r"to (?:place an )?order,? (?:please )?(?:call|phone|ring)",
    r"for orders?,? (?:please )?(?:call|phone|ring)",
)
CART_TEXT_RE = _rx(
    r"add to (?:cart|basket|bag|trolley)", r"view (?:cart|basket)", r"\bcheckout\b",
    r"shopping (?:cart|basket)", r"your (?:cart|basket)\b",
)
ORDER_ONLINE_RE = _rx(
    r"order online", r"shop online", r"buy online", r"online (?:shop|store|shopping|ordering|orders?)\b",
    r"order (?:your )?(?:groceries|shopping) online",
)
APP_TEXT_RE = _rx(
    r"\bapp store\b", r"\bgoogle play\b", r"download (?:our|the|my) (?:\w+ )?app\b",
    r"get (?:our|the) app\b", r"our (?:mobile )?app\b",
)
OWN_STRONG_RE = _rx(
    r"\bour own (?:delivery|drivers?|riders?|fleet)\b", r"\bour (?:delivery )?(?:drivers?|riders?|fleet)\b",
    r"\bdelivery (?:team|drivers?|riders?|vans?|fleet)\b", r"\bin-?house delivery\b",
)
OWN_GENERIC_RE = _rx(
    r"\bwe deliver\b", r"\bhome delivery\b", r"\bfree delivery\b", r"\bdelivery service\b",
    r"\bour delivery\b", r"\bdelivery (?:is )?available\b", r"\bsame[- ]day delivery\b",
    r"\bnext[- ]day delivery\b", r"\bwe (?:also )?(?:offer|provide) (?:a )?(?:free )?(?:home )?delivery\b",
    r"\bdelivery (?:charges?|fees?|areas?|radius|zones?)\b", r"\bdeliver(?:s|ing)? (?:within|across|to your)\b",
)
NO_DELIVERY_RE = _rx(
    r"\b(?:collection|click\s*(?:and|&|n)\s*collect|pick-?\s?up|in[- ]store)\s+only\b",
    r"\bno (?:home )?delivery\b(?!\s+(?:charges?|fees?|costs?))",
    r"\bwe (?:do not|don't|cannot|can't) (?:currently )?(?:offer |provide )?deliver(?:y|ies)?\b",
    r"\bdelivery (?:is )?(?:currently )?(?:not|un)available\b",
    r"\bnot (?:currently )?delivering\b",
)


ORDER_FORM_RE = _rx(r"order form", r"place (?:an|your) order", r"request (?:a )?(?:quote|callback|call back)",
                    r"bulk (?:order|enquir)", r"catering (?:order|enquir)", r"enquiry form")
CHATBOT_TEXT_RE = _rx(r"\bchat-?bot\b", r"\bvirtual assistant\b", r"\bai assistant\b")
NOTIFICATIONS_RE = _rx(
    r"order (?:confirmations?|updates|tracking|notifications?|status)", r"track (?:your|my) (?:order|delivery|parcel)",
    r"(?:sms|text message) (?:alerts|notifications|updates)", r"delivery (?:updates|notifications|tracking)",
    r"(?:live|real-time) (?:order |delivery )?tracking", r"push notifications?",
)
_FIELD_INPUT_TYPES = {"text", "email", "tel", "number", "date", "time", ""}


def _has_basic_form(soup: BeautifulSoup) -> bool:
    """A form a customer could fill in to contact/order: a textarea, or 2+ text-like fields.
    Search boxes, logins (password) and one-field newsletter sign-ups do not count."""
    for form in soup.find_all("form"):
        if form.get("role") == "search" or form.find("input", {"type": "password"}):
            continue
        fields = [i for i in form.find_all("input") if (i.get("type") or "").lower() in _FIELD_INPUT_TYPES]
        if form.find("textarea") or len(fields) + len(form.find_all("select")) >= 2:
            return True
    return False


def _first(rx: re.Pattern, text: str) -> str:
    m = rx.search(text)
    return m.group(0).strip() if m else ""


def collect_evidence(soup: BeautifulSoup, html: str, base_url: str = "") -> dict:
    """Look through one page and note the keywords/links we care about."""
    ev = empty_evidence()
    hrefs = [a["href"].strip() for a in soup.find_all("a", href=True)]
    low_hrefs = [h.lower() for h in hrefs]
    text = _visible_text(soup)
    html_low = html.lower()

    # --- WhatsApp: a link carrying a real number beats a text mention ---
    if any(WHATSAPP_LINK_RE.search(h) for h in low_hrefs):
        ev["whatsapp"] = "link"
    elif "whatsapp" in WHATSAPP_SHARE_RE.sub("", text):
        ev["whatsapp"] = "text"

    ev["call_to_order"] = bool(CALL_TO_ORDER_RE.search(text))

    # --- website ordering ---
    if (any(m in html_low for m in CART_HTML_MARKERS)
            or any(urlparse(h).path.rstrip("/").endswith(CART_LINK_SUFFIXES) for h in low_hrefs)):
        ev["cart"] = "marker"
    elif CART_TEXT_RE.search(text):
        ev["cart"] = "text"
    ev["order_online"] = bool(ORDER_ONLINE_RE.search(text))

    # --- delivery platforms (links to a platform are never counted as the mart's own app) ---
    for name, spec in DELIVERY_PLATFORMS.items():
        if any(_host_in(_host(h), spec["hosts"]) for h in low_hrefs):
            ev["platforms"][name] = "link"
        elif any(re.search(rf"\b{re.escape(t)}\b", text) for t in spec["text"]):
            ev["platforms"][name] = "text"

    # --- mobile app ---
    for h in low_hrefs:
        host, path = _host(h), urlparse(h).path
        store = (host == "apps.apple.com" or (host == "itunes.apple.com" and "/app/" in path)
                 or (host == "play.google.com" and "/store/apps" in path))
        if store and not any(tok in h for tok in _PLATFORM_TOKENS):
            ev["app"] = "link"
            break
    if not ev["app"] and APP_TEXT_RE.search(text):
        ev["app"] = "text"

    # --- delivery wording ---
    ev["couriers"] = [c.upper() if len(c) <= 4 else c.title() for c in COURIERS
                      if re.search(rf"\b{re.escape(c)}\b", text)][:4]
    ev["own_strong"] = bool(OWN_STRONG_RE.search(text))
    own = [p for p in (_first(OWN_STRONG_RE, text), _first(OWN_GENERIC_RE, text)) if p]
    ev["own_delivery"] = own
    nod = _first(NO_DELIVERY_RE, text)
    ev["no_delivery"] = [nod] if nod else []

    # --- automation (v2) ---
    if _has_basic_form(soup) or any(m in html_low for m in FORM_HTML_MARKERS):
        ev["form"] = "order" if ORDER_FORM_RE.search(text) else "contact"
    if any(m in html_low for m in CHATBOT_HTML_MARKERS):
        ev["chatbot"] = "marker"
    elif CHATBOT_TEXT_RE.search(text):
        ev["chatbot"] = "text"
    if any(m in html_low for m in CRM_HTML_MARKERS):
        ev["crm"] = "marker"
    ev["notifications"] = bool(NOTIFICATIONS_RE.search(text))
    return ev


# ======================================================================
# 2. Rules: evidence -> sheet values
# ======================================================================
def detect_order_method(ev: dict | None, platform_hint: str = "") -> str:
    """'Phone Orders + WhatsApp Orders + Website Orders + Mobile App + Delivery Platforms' (those found)."""
    scanned = ev is not None                      # a real site was read (vs. no site / unreachable)
    ev = ev or empty_evidence()
    found = []
    if ev["call_to_order"]:
        found.append("Phone Orders")
    if ev["whatsapp"]:
        found.append("WhatsApp Orders")
    if ev["cart"] or ev["order_online"]:
        found.append("Website Orders")
    if ev["app"]:
        found.append("Mobile App")
    if ev["platforms"] or platform_hint:
        found.append("Delivery Platforms")
    if found:
        return " + ".join(found)
    return "Walk-in Only" if (INFER_WALK_IN_WHEN_NO_SIGNALS and scanned) else ORDER_METHOD_UNKNOWN


def detect_delivery(ev: dict | None, platform_hint: str = "") -> str:
    """Own Delivery Team | Third-party Delivery | No Delivery | Unknown.

    Order of trust:
      1. wording that names the mart's own drivers/team        -> Own Delivery Team
      2. a delivery platform or courier                         -> Third-party Delivery
      3. generic "we deliver / free delivery" wording           -> Own Delivery Team
      4. explicit "collection only" / "no delivery" wording     -> No Delivery
      5. nothing stated                                         -> Unknown
    Positive evidence always beats "no delivery" wording ("no delivery on Sundays").
    """
    ev = ev or empty_evidence()
    third_party = bool(ev["platforms"] or ev["couriers"] or platform_hint)
    if ev["own_strong"]:
        return "Own Delivery Team"
    if third_party:
        return "Third-party Delivery"
    if ev["own_delivery"]:
        return "Own Delivery Team"
    if ev["no_delivery"]:
        return "No Delivery"
    return DELIVERY_UNKNOWN


# ======================================================================
# 3. Notes reason
# ======================================================================
def _platform_names(ev: dict, hint: str) -> list[str]:
    names = list(ev["platforms"])
    if hint and hint not in names:
        names.append(hint)
    return names


def build_reason(ev: dict | None, platform_hint: str = "", website_status: str = "",
                 has_site: bool = False) -> str:
    """Short human reason, e.g. 'Has WhatsApp button, no cart; delivery: on Deliveroo'."""
    if ev is None:
        if platform_hint:
            return f"Google lists a {platform_hint} page as the website; delivery: on {platform_hint}"
        if has_site and website_status in ("unreachable", "blocked"):
            return "Site could not be scanned"
        return "No website to scan"

    has = []
    if ev["call_to_order"]:
        has.append("call-to-order text")
    if ev["whatsapp"]:
        has.append("WhatsApp button" if ev["whatsapp"] == "link" else "WhatsApp mention")
    if ev["cart"]:
        has.append("online store/cart")
    elif ev["order_online"]:
        has.append("order-online wording")
    if ev["app"]:
        has.append("app store link" if ev["app"] == "link" else "app mention")
    names = _platform_names(ev, platform_hint)
    if names:
        has.append(", ".join(names) + (" link" if "link" in ev["platforms"].values() else " mention"))

    if not has:
        order_part = "No ordering signals on site"
    else:
        order_part = "Has " + ", ".join(has)
        if not ev["cart"] and not ev["order_online"]:
            order_part += ", no cart"

    if ev["own_strong"]:
        delivery = "own drivers/team mentioned"
    elif names or ev["couriers"]:
        delivery = "on " + ", ".join(names) if names else "courier: " + ", ".join(ev["couriers"])
    elif ev["own_delivery"]:
        delivery = f"site says \"{ev['own_delivery'][0]}\""
    elif ev["no_delivery"]:
        delivery = f"site says \"{ev['no_delivery'][0]}\""
    else:
        delivery = "not stated"
    return f"{order_part}; delivery: {delivery}"


# ======================================================================
# 4. Automation status
# ======================================================================
def detect_automation(ev: dict | None, website_status: str = "", has_site: bool = False) -> tuple[str, str]:
    """Returns (Manual | Semi Automated | Automated | Unknown, short reason).

      Automated       online store/cart, own mobile app, chatbot, CRM or order notifications
      Semi Automated  website with a basic contact/order form (or "order online" wording), no store
      Manual          no website, or a website with no forms or online ordering
      Unknown         the website could not be read
    Delivery-platform listings (Deliveroo etc.) are the platform's automation, not the mart's.
    """
    if ev is None:
        if has_site and website_status in ("unreachable", "blocked"):
            return AUTOMATION_UNKNOWN, "site could not be scanned"
        return "Manual", "no website, no online ordering"

    auto = []
    if ev["cart"]:
        auto.append("online store")
    if ev["app"]:
        auto.append("mobile app")
    if ev["chatbot"]:
        auto.append("chatbot")
    if ev["crm"]:
        auto.append("CRM")
    if ev["notifications"]:
        auto.append("order notifications")
    if auto:
        return "Automated", ", ".join(auto)

    if ev["form"]:
        kind = "order form" if ev["form"] == "order" else "contact form"
        return "Semi Automated", f"website with basic {kind}, no cart"
    if ev["order_online"]:
        return "Semi Automated", "website with order-online wording, no cart"
    if WEBSITE_ONLY_IS_SEMI_AUTOMATED:
        return "Semi Automated", "website only, no forms or cart"
    return "Manual", "website has no forms or online ordering"


# ======================================================================
# 5. Apply to leads
# ======================================================================
def detect_lead(lead: Lead) -> Lead:
    """Fill order_method, delivery_system, automation_status and the Notes reason.
    Safe to re-run (deterministic, overwrites its own earlier answer)."""
    ev = lead.site_evidence or None
    hint = lead.platform_hint
    has_site = bool(lead.website_url)
    lead.order_method = detect_order_method(ev, hint)
    lead.delivery_system = detect_delivery(ev, hint)
    lead.automation_status, auto_reason = detect_automation(ev, lead.website_status, has_site)
    lead.detection_note = (build_reason(ev, hint, lead.website_status, has_site)
                           + f"; automation: {lead.automation_status} ({auto_reason})")
    return lead


def detect_leads(leads: list[Lead]) -> list[Lead]:
    """Run detection on every lead. One failing lead gets 'Unknown' instead of stopping the batch."""
    for lead in leads:
        try:
            detect_lead(lead)
        except Exception as e:                      # keep the "all three fields filled" promise
            lead.order_method = lead.delivery_system = lead.automation_status = "Unknown"
            lead.detection_note = f"Detection failed ({type(e).__name__})"
    return leads


# ======================================================================
# 6. Done check
# ======================================================================
ORDER_METHOD_LABELS = {"Phone Orders", "WhatsApp Orders", "Website Orders", "Mobile App",
                       "Delivery Platforms", "Walk-in Only", ORDER_METHOD_UNKNOWN}


def validate_detection(leads: list[Lead]) -> list[str]:
    """Problems found; empty list means every lead has all three fields filled with allowed values."""
    problems = []
    for l in leads:
        who = l.lead_id or l.business_name
        if not l.order_method:
            problems.append(f"{who}: Order Method is empty")
        elif not set(l.order_method.split(" + ")) <= ORDER_METHOD_LABELS:
            problems.append(f"{who}: unexpected Order Method '{l.order_method}'")
        if not l.delivery_system:
            problems.append(f"{who}: Delivery System is empty")
        elif l.delivery_system not in DROPDOWNS["Delivery System"]:
            problems.append(f"{who}: unexpected Delivery System '{l.delivery_system}'")
        if not l.automation_status:
            problems.append(f"{who}: Automation Status is empty")
        elif l.automation_status not in DROPDOWNS["Automation Status"]:
            problems.append(f"{who}: unexpected Automation Status '{l.automation_status}'")
        if not l.detection_note:
            problems.append(f"{who}: no reason written to Notes")
    return problems


def detection_report(leads: list[Lead]) -> dict:
    """Chunk 7 done check: every lead has Order Method, Delivery System and Automation Status filled."""
    def count(attr):
        out: dict[str, int] = {}
        for l in leads:
            v = getattr(l, attr) or "(empty)"
            out[v] = out.get(v, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    problems = validate_detection(leads)
    unknown = lambda attr: sum(1 for l in leads if getattr(l, attr) == "Unknown")
    return {
        "total_leads": len(leads),
        "order_method": count("order_method"),
        "delivery_system": count("delivery_system"),
        "automation_status": count("automation_status"),
        "order_unknown": unknown("order_method"),
        "delivery_unknown": unknown("delivery_system"),
        "automation_unknown": unknown("automation_status"),
        "problems": problems,
        "complete": bool(leads) and not problems,
    }
