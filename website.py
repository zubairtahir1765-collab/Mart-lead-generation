"""Chunk 6: website and social enrichment.

For every lead:
  1. clean the website URL Google gave us and set Website Available (Yes/No)
  2. visit the homepage plus a few contact/about pages
  3. pick out an email address, Instagram/Facebook links and, where it is
     stated plainly, an owner/manager name

Rules the crawler follows: it only reads public pages, honours robots.txt, visits
at most WEBSITE_MAX_PAGES pages per site, and never logs in to or scrapes
Facebook/Instagram themselves (we only record the links found on the mart's own site).
"""
import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from urllib import robotparser
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config import (
    CONTACT_PAGE_HINTS,
    EMAIL_IGNORE_DOMAINS,
    EMAIL_IGNORE_PREFIXES,
    EMAIL_PREFERRED_PREFIXES,
    ENRICHED_DATA_DIR,
    ENRICHMENT_TARGET_PCT,
    NON_WEBSITE_HOSTS,
    RESPECT_ROBOTS_TXT,
    SOCIAL_HOSTS,
    WEBSITE_CACHE_DIR,
    WEBSITE_MAX_BYTES,
    WEBSITE_MAX_PAGES,
    WEBSITE_PAGE_DELAY_SECONDS,
    WEBSITE_TIMEOUT_SECONDS,
    WEBSITE_USER_AGENT,
    WEBSITE_WORKERS,
)
from detection import (EVIDENCE_VERSION, collect_evidence, empty_evidence, merge_evidence,
                       platform_from_url)
from models import Lead

FREE_MAIL = {"gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "hotmail.com",
             "hotmail.co.uk", "outlook.com", "live.com", "live.co.uk", "icloud.com", "me.com",
             "aol.com", "proton.me", "protonmail.com", "msn.com", "mail.com"}
FILE_TLDS = {"png", "jpg", "jpeg", "gif", "svg", "webp", "css", "js", "ico", "pdf", "woff", "woff2"}
SKIP_LINK_EXT = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".pdf", ".zip", ".mp4", ".css", ".js")


# ======================================================================
# 1. URL handling
# ======================================================================
def _host(url: str) -> str:
    h = (urlparse(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def _host_in(host: str, domains: set[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def _strip_tracking(url: str) -> str:
    parts = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parts.query)
             if not k.lower().startswith(("utm_", "fbclid", "gclid", "mc_", "ref"))]
    return parts._replace(query=urlencode(query), fragment="").geturl()


def classify_website(url: str) -> tuple[str, list[str], str]:
    """
    Clean the URL Google returned.
    Returns (website_url, social_urls, status):
      - real site          -> (clean_url, [], "pending")      (crawl it)
      - Facebook/Instagram -> ("", [url], "social_only")      (not a real website)
      - delivery/maps etc. -> ("", [], "none")
      - empty              -> ("", [], "none")
    """
    url = (url or "").strip()
    if not url:
        return "", [], "none"
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url.lstrip("/")
    host = _host(url)
    if not host or "." not in host:
        return "", [], "none"
    if _host_in(host, SOCIAL_HOSTS):
        return "", [_strip_tracking(url)], "social_only"
    if _host_in(host, NON_WEBSITE_HOSTS):
        return "", [], "none"
    return _strip_tracking(url), [], "pending"


# ======================================================================
# 2. Fetching (polite: robots.txt, size limit, timeout, few pages)
# ======================================================================
@dataclass
class FetchResult:
    ok: bool = False
    html: str = ""
    final_url: str = ""
    status_code: int = 0
    error: str = ""
    blocked: bool = False      # robots.txt, 401/403/429


class SiteFetcher:
    """One per site. Keeps a session and robots.txt rules for that site."""

    def __init__(self, respect_robots: bool = RESPECT_ROBOTS_TXT):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": WEBSITE_USER_AGENT,
                                     "Accept": "text/html,application/xhtml+xml",
                                     "Accept-Language": "en"})
        self.respect_robots = respect_robots
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}
        self._last_request = 0.0

    def _allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            rp = robotparser.RobotFileParser()
            try:
                r = self.session.get(origin + "/robots.txt", timeout=WEBSITE_TIMEOUT_SECONDS)
                if r.status_code == 200:
                    rp.parse(r.text.splitlines())
                    self._robots[origin] = rp
                else:
                    self._robots[origin] = None   # no robots.txt = allowed
            except requests.RequestException:
                self._robots[origin] = None
        rp = self._robots[origin]
        return True if rp is None else rp.can_fetch(WEBSITE_USER_AGENT, url)

    def get(self, url: str) -> FetchResult:
        if not self._allowed(url):
            return FetchResult(blocked=True, error="robots.txt disallows", final_url=url)
        wait = WEBSITE_PAGE_DELAY_SECONDS - (time.time() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.time()
        try:
            try:
                r = self.session.get(url, timeout=WEBSITE_TIMEOUT_SECONDS, stream=True)
            except requests.exceptions.SSLError:
                if not url.startswith("https://"):
                    raise
                r = self.session.get("http://" + url[len("https://"):],
                                     timeout=WEBSITE_TIMEOUT_SECONDS, stream=True)
        except requests.RequestException as e:
            return FetchResult(error=type(e).__name__, final_url=url)

        try:
            code = r.status_code
            if code in (401, 403, 429):
                return FetchResult(status_code=code, blocked=True, error=f"HTTP {code}", final_url=r.url)
            if code != 200:
                return FetchResult(status_code=code, error=f"HTTP {code}", final_url=r.url)
            if "html" not in r.headers.get("Content-Type", "").lower():
                return FetchResult(status_code=code, error="not html", final_url=r.url)
            body = b""
            for chunk in r.iter_content(65536):
                body += chunk
                if len(body) > WEBSITE_MAX_BYTES:
                    break
            encoding = r.encoding or "utf-8"
            return FetchResult(ok=True, html=body.decode(encoding, errors="replace"),
                               final_url=r.url, status_code=code)
        finally:
            r.close()


# ======================================================================
# 3. Extraction
# ======================================================================
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")


def _decode_cfemail(hexstr: str) -> str:
    """Cloudflare hides emails as hex; first byte is the XOR key."""
    try:
        key = int(hexstr[:2], 16)
        return "".join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))
    except ValueError:
        return ""


def _deobfuscate(text: str) -> str:
    text = re.sub(r"\s*[\[\(\{]\s*at\s*[\]\)\}]\s*", "@", text, flags=re.I)
    text = re.sub(r"\s*[\[\(\{]\s*dot\s*[\]\)\}]\s*", ".", text, flags=re.I)
    return text.replace("&#64;", "@").replace("&commat;", "@")


def _valid_email(email: str) -> bool:
    email = email.strip().strip(".").lower()
    if len(email) > 80 or email.count("@") != 1:
        return False
    local, domain = email.split("@")
    if not local or domain.split(".")[-1] in FILE_TLDS:
        return False
    if _host_in(domain, EMAIL_IGNORE_DOMAINS):
        return False
    if local.startswith(EMAIL_IGNORE_PREFIXES):
        return False
    if re.fullmatch(r"[0-9a-f]{16,}", local):      # hash-like (tracking / sentry ids)
        return False
    return True


def _same_site(email_domain: str, site_host: str) -> bool:
    e, s = email_domain.lower(), site_host.lower()
    return e == s or s.endswith("." + e) or e.endswith("." + s)


def extract_emails(soup: BeautifulSoup, html: str, site_host: str, is_contact_page: bool) -> list[tuple[str, int]]:
    """Return [(email, score)] for emails that look like they belong to this business."""
    found: dict[str, int] = {}

    def add(raw: str, via_mailto: bool):
        raw = unquote(raw).split("?")[0].strip()
        for m in EMAIL_RE.findall(_deobfuscate(raw)):
            email = m.strip(".").lower()
            if not _valid_email(email):
                continue
            domain = email.split("@")[1]
            same = _same_site(domain, site_host)
            free = domain in FREE_MAIL
            # other-domain addresses are usually web agencies/platforms; only trust a mailto on a contact page
            if not (same or free or (via_mailto and is_contact_page)):
                continue
            score = (3 if same else 0) + (1 if free else 0) + (1 if via_mailto else 0)
            if email.split("@")[0].startswith(EMAIL_PREFERRED_PREFIXES):
                score += 2
            found[email] = max(found.get(email, 0), score)

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith("mailto:"):
            add(href[7:], True)
        elif "/cdn-cgi/l/email-protection#" in href:
            add(_decode_cfemail(href.split("#", 1)[1]), True)
    for el in soup.select("[data-cfemail]"):
        add(_decode_cfemail(el.get("data-cfemail", "")), True)
    add(soup.get_text(" "), False)
    for script in soup.find_all("script", type="application/ld+json"):
        add(script.string or "", False)
    return list(found.items())


# --- social links ---------------------------------------------------------
IG_SKIP = {"p", "reel", "reels", "explore", "accounts", "tv", "stories", "share", "about",
           "direct", "web", "developer", "legal", "privacy", "instagram", "meta"}
FB_SKIP = {"sharer", "sharer.php", "share", "share.php", "dialog", "plugins", "tr", "login", "login.php",
           "policies", "privacy", "help", "events", "groups", "watch", "marketplace", "hashtag",
           "permalink.php", "photo", "photo.php", "story.php", "facebook", "fb", "wix", "squarespace",
           "shopify", "wordpress", "meta", "business", "ads", "pages", "people", "profile.php", "l.php"}
SOCIAL_NAME_SKIP = {"wix", "wixcom", "squarespace", "shopify", "wordpress", "godaddy", "elementor"}


def social_from_url(url: str) -> tuple[str, str] | None:
    """('instagram'|'facebook', canonical_url) or None if it is not a profile/page link."""
    try:
        parts = urlparse(url.strip())
    except ValueError:
        return None
    host = _host(url)
    segs = [s for s in parts.path.split("/") if s]
    if host.endswith("instagram.com") and segs:
        handle = segs[0]
        if handle.lower() in IG_SKIP or not re.fullmatch(r"[A-Za-z0-9._]{1,30}", handle):
            return None
        if handle.lower() in SOCIAL_NAME_SKIP:
            return None
        return "instagram", f"https://www.instagram.com/{handle}/"
    if host in ("facebook.com", "fb.com", "m.facebook.com", "web.facebook.com") or host.endswith(".facebook.com"):
        if not segs:
            return None
        first = segs[0]
        if first.lower() == "profile.php":
            ids = dict(parse_qsl(parts.query)).get("id")
            return ("facebook", f"https://www.facebook.com/profile.php?id={ids}") if ids and ids.isdigit() else None
        if first.lower() in ("pages", "people") and len(segs) >= 3:
            return "facebook", "https://www.facebook.com/" + "/".join(segs[:3])
        if first.lower() in FB_SKIP or first.lower() in SOCIAL_NAME_SKIP:
            return None
        if not re.fullmatch(r"[A-Za-z0-9.\-_]{3,80}", first):
            return None
        return "facebook", f"https://www.facebook.com/{first}"
    return None


def extract_socials(soup: BeautifulSoup, html: str) -> dict[str, str]:
    """First valid Instagram and Facebook link on the page (anchors, JSON-LD sameAs, embedded URLs)."""
    candidates: list[str] = [a["href"] for a in soup.find_all("a", href=True)]
    for script in soup.find_all("script", type="application/ld+json"):
        candidates += re.findall(r"https?:\\?/\\?/[^\"'\s,\]]+", (script.string or "").replace("\\/", "/"))
    candidates += re.findall(r"https?://(?:www\.)?(?:instagram|facebook)\.com/[A-Za-z0-9._/\-?=]+", html)
    out: dict[str, str] = {}
    for url in candidates:
        res = social_from_url(url)
        if res and res[0] not in out:
            out[res[0]] = res[1]
    return out


# --- owner / manager name ---------------------------------------------------
NAME = r"((?!(?:I['’]m|Hi|Hello|Meet|My|Our|Welcome)\b)[A-Z][a-zA-Z'’\-]{1,20}(?:\s+[A-Z][a-zA-Z'’\-]{1,20}){1,2})"
ROLE = r"(?i:owner|co-owner|proprietor|founder|co-founder|store manager|general manager|manager|managing director|director)"
OWNER_PATTERNS = [
    # "Owner: Ahmed Khan"
    (re.compile(rf"\b({ROLE})\s*[:\-–—]\s*{NAME}"), "role_first"),
    # "Founded by Ahmed Khan", "Family run by ...", "Owned and managed by ..."
    (re.compile(rf"(?i:\b(owned|run|managed|founded|established|started|opened)(?:\s+and\s+(?:owned|run|managed|operated))?\s+by)\s+(?:Mr\.?\s+|Mrs\.?\s+|Ms\.?\s+)?{NAME}"), "by"),
    # "Ahmed Khan, Owner" / "Ahmed Khan - Store Manager" / "Ahmed Khan | Founder"
    (re.compile(rf"{NAME}\s*[,\-–—|(]\s*(?:the\s+)?({ROLE})\b"), "name_first"),
    # "Hi, I'm Ahmed Khan, the owner"
    (re.compile(rf"(?i:\b(?:hi,?\s+)?(?:i'?m|i am|my name is|meet))\s+{NAME}[,.\s]+(?i:(?:the|your|our)\s+)?({ROLE})\b"), "intro"),
]
NAME_STOPWORDS = {
    "ltd", "limited", "llc", "plc", "inc", "co", "company", "group", "store", "stores", "mart", "market",
    "supermarket", "grocery", "groceries", "foods", "food", "fresh", "halal", "asian", "organic",
    "family", "team", "our", "the", "and", "customer", "customers", "service", "services", "retail",
    "business", "shop", "shopping", "online", "contact", "about", "us", "home", "page", "privacy",
    "policy", "terms", "delivery", "order", "orders", "opening", "hours", "monday", "tuesday",
    "wednesday", "thursday", "friday", "saturday", "sunday", "january", "february", "march", "april",
    "may", "june", "july", "august", "september", "october", "november", "december", "welcome",
    "street", "road", "lane", "avenue", "city", "town", "wholesale", "cash", "carry", "supplies",
    "express", "local", "premier", "best", "quality", "new", "all", "rights", "reserved", "copyright",
}


def _valid_person(name: str, business_name: str) -> bool:
    words = re.split(r"\s+", name.strip())
    if not 2 <= len(words) <= 3:
        return False
    low = [re.sub(r"[^a-z]", "", w.lower()) for w in words]
    if any(w in NAME_STOPWORDS or len(w) < 2 for w in low):
        return False
    biz = {re.sub(r"[^a-z]", "", w.lower()) for w in business_name.split()}
    if all(w in biz for w in low):          # it's just the business name again
        return False
    return True


def _role_label(role: str) -> str:
    role = role.lower().replace("co-", "").strip()
    return {"owned": "Owner", "run": "Owner", "managed": "Manager", "founded": "Founder",
            "established": "Founder", "started": "Founder", "opened": "Owner",
            "proprietor": "Owner", "general manager": "Manager", "store manager": "Manager",
            "managing director": "Director"}.get(role, role.title())


def extract_owner(soup: BeautifulSoup, business_name: str = "") -> str:
    """Only stated, unambiguous names: 'Owner: X', 'Founded by X', 'X, Owner'. Returns 'Name (Role)' or ''."""
    # structured data first
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                for key, role in (("founder", "Founder"), ("founders", "Founder"), ("owner", "Owner")):
                    val = node.get(key)
                    for item in (val if isinstance(val, list) else [val]):
                        nm = item.get("name") if isinstance(item, dict) else item
                        if isinstance(nm, str) and nm.strip() and _valid_person(nm.title() if nm.islower() else nm, business_name):
                            return f"{nm.strip()} ({role})"
                stack.extend(v for v in node.values() if isinstance(v, (list, dict)))

    text = re.sub(r"\s+", " ", soup.get_text(" "))
    for pattern, kind in OWNER_PATTERNS:
        for m in pattern.finditer(text):
            if kind == "role_first" or kind == "by":
                role, name = m.group(1), m.group(2)
            else:
                name, role = m.group(1), m.group(2)
            name = re.sub(r"^(?:Mr|Mrs|Ms|Dr)\.?\s+", "", name).strip()
            if _valid_person(name, business_name):
                return f"{name} ({_role_label(role)})"
    return ""


# --- which internal pages to visit ----------------------------------------
def find_contact_links(soup: BeautifulSoup, base_url: str, limit: int) -> list[str]:
    base_host = _host(base_url)
    scored: dict[str, int] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = _strip_tracking(urljoin(base_url, href))
        p = urlparse(url)
        if p.scheme not in ("http", "https") or _host(url) != base_host:
            continue
        if p.path.lower().endswith(SKIP_LINK_EXT) or p.path in ("", "/"):
            continue
        hay = (p.path + " " + a.get_text(" ")).lower()
        for rank, hint in enumerate(CONTACT_PAGE_HINTS):
            if hint in hay:
                scored[url] = min(scored.get(url, 99), rank)
                break
    return [u for u, _ in sorted(scored.items(), key=lambda kv: kv[1])][:limit]


# ======================================================================
# 4. Crawl one site
# ======================================================================
def enrich_site(url: str, business_name: str = "", fetcher=None, max_pages: int = WEBSITE_MAX_PAGES) -> dict:
    """
    Crawl one site. Returns:
      {status, emails:[...ranked], instagram, facebook, owner, pages_visited, error}
    status: ok | unreachable | blocked | social_only
    """
    fetcher = fetcher or SiteFetcher()
    result = {"url": url, "status": "unreachable", "emails": [], "instagram": "", "facebook": "",
              "owner": "", "pages_visited": 0, "error": "",
              "evidence": None, "evidence_version": EVIDENCE_VERSION}   # evidence: Chunk 7 keyword scan

    home = fetcher.get(url)
    if not home.ok:
        result["status"] = "blocked" if home.blocked else "unreachable"
        result["error"] = home.error
        return result

    final = home.final_url or url
    if _host_in(_host(final), SOCIAL_HOSTS):         # site just redirects to a social page
        found = social_from_url(final)
        if found:
            result[found[0]] = found[1]
        result["status"] = "social_only"
        return result

    site_host = _host(final)
    scored_emails: dict[str, int] = {}
    pages = [(final, home.html, False)]
    soup_home = BeautifulSoup(home.html, "html.parser")
    queue = find_contact_links(soup_home, final, max_pages - 1)

    for link in queue:
        page = fetcher.get(link)
        if page.ok:
            pages.append((page.final_url or link, page.html, True))

    result["status"] = "ok"
    result["pages_visited"] = len(pages)
    result["evidence"] = empty_evidence()
    for page_url, html, is_contact in pages:
        soup = soup_home if page_url == final and not is_contact else BeautifulSoup(html, "html.parser")
        is_contact_page = is_contact or any(h in page_url.lower() for h in CONTACT_PAGE_HINTS)
        for email, score in extract_emails(soup, html, site_host, is_contact_page):
            scored_emails[email] = max(scored_emails.get(email, 0), score)
        merge_evidence(result["evidence"], collect_evidence(soup, html, page_url))
        socials = extract_socials(soup, html)
        for kind in ("instagram", "facebook"):
            if not result[kind] and socials.get(kind):
                result[kind] = socials[kind]
        if not result["owner"]:
            result["owner"] = extract_owner(soup, business_name)

    result["emails"] = [e for e, _ in sorted(scored_emails.items(), key=lambda kv: -kv[1])]
    return result


# ======================================================================
# 5. Apply to leads
# ======================================================================
def _cache_path(url: str) -> str:
    key = hashlib.sha1(url.lower().rstrip("/").encode()).hexdigest()[:16]
    return os.path.join(WEBSITE_CACHE_DIR, f"{_host(url) or 'site'}_{key}.json")


def crawl_cached(url: str, business_name: str = "", use_cache: bool = True, fetcher=None) -> dict:
    path = _cache_path(url)
    if use_cache and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)
        # entries saved before the keyword scan existed (or by an older scan) are crawled again
        if cached.get("status") != "ok" or cached.get("evidence_version") == EVIDENCE_VERSION:
            return cached
    result = enrich_site(url, business_name, fetcher=fetcher)
    # do not cache transient failures
    if result["status"] in ("ok", "social_only"):
        os.makedirs(WEBSITE_CACHE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
    return result


def merge_social(existing: str, instagram: str = "", facebook: str = "") -> str:
    """Keep what is already there; add Instagram/Facebook if missing. Format: 'Instagram: url | Facebook: url'."""
    have = {}
    for part in (existing or "").split("|"):
        part = part.strip()
        if part.lower().startswith("instagram:"):
            have["Instagram"] = part.split(":", 1)[1].strip()
        elif part.lower().startswith("facebook:"):
            have["Facebook"] = part.split(":", 1)[1].strip()
    if instagram and "Instagram" not in have:
        have["Instagram"] = instagram
    if facebook and "Facebook" not in have:
        have["Facebook"] = facebook
    return " | ".join(f"{k}: {have[k]}" for k in ("Instagram", "Facebook") if k in have)


def prepare_lead(lead: Lead) -> str:
    """Step 1 (no network): clean the URL and set website_available. Returns the URL to crawl or ''."""
    lead.platform_hint = platform_from_url(lead.website_url) or lead.platform_hint
    clean_url, socials, status = classify_website(lead.website_url)
    lead.website_url = clean_url
    for s in socials:
        found = social_from_url(s)
        if found:
            lead.social_links = merge_social(lead.social_links,
                                             instagram=found[1] if found[0] == "instagram" else "",
                                             facebook=found[1] if found[0] == "facebook" else "")
    lead.website_status = status if status != "pending" else ""
    return clean_url


def apply_result(lead: Lead, result: dict) -> None:
    """Step 3: copy crawl results onto the lead without overwriting anything already filled."""
    lead.website_status = result["status"]
    if result["status"] == "ok":
        lead.site_evidence = result.get("evidence") or {}
        emails = result["emails"]
        if emails and not lead.email:
            lead.email = emails[0]
            lead.extra_emails = ", ".join(emails[1:3])
        lead.social_links = merge_social(lead.social_links, result["instagram"], result["facebook"])
        if result["owner"] and not lead.owner_name:
            lead.owner_name = result["owner"]
    elif result["status"] == "social_only":
        lead.website_url = ""
        lead.social_links = merge_social(lead.social_links, result["instagram"], result["facebook"])


def enrich_leads(leads: list[Lead], workers: int = WEBSITE_WORKERS, use_cache: bool = True,
                 fetcher_factory=None, progress: bool = True) -> list[Lead]:
    """Enrich every lead in place. Leads sharing a website (chains) are crawled once."""
    to_crawl: dict[str, list[Lead]] = {}
    for lead in leads:
        url = prepare_lead(lead)
        if url:
            to_crawl.setdefault(url, []).append(lead)

    def job(url: str):
        names = to_crawl[url]
        fetcher = fetcher_factory() if fetcher_factory else None
        return url, crawl_cached(url, names[0].business_name, use_cache, fetcher)

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(job, u) for u in to_crawl]
        for fut in as_completed(futures):
            try:
                url, result = fut.result()
            except Exception as e:           # one bad site must not stop the batch
                done += 1
                continue
            for lead in to_crawl[url]:
                apply_result(lead, result)
            done += 1
            if progress:
                print(f"  [{done}/{len(to_crawl)}] {url} -> {result['status']}"
                      f"{' | ' + result['emails'][0] if result['emails'] else ''}")
    return leads


# ======================================================================
# 6. Reporting and saving
# ======================================================================
def has_extra_data(lead: Lead) -> bool:
    return bool(lead.email or lead.social_links or lead.owner_name)


def enrichment_report(leads: list[Lead]) -> dict:
    """
    Chunk 6 'done' check: share of leads WITH a real website that now have extra data
    (email, Instagram/Facebook or owner name).
    """
    with_site = [l for l in leads if l.website_url]
    enriched = [l for l in with_site if has_extra_data(l)]
    pct = round(100 * len(enriched) / len(with_site), 1) if with_site else 0.0
    return {
        "total_leads": len(leads),
        "website_yes": len(with_site),
        "website_no": len(leads) - len(with_site),
        "social_only_no_site": sum(1 for l in leads if not l.website_url and l.social_links),
        "site_unreachable": sum(1 for l in with_site if l.website_status == "unreachable"),
        "site_blocked": sum(1 for l in with_site if l.website_status == "blocked"),
        "with_email": sum(1 for l in with_site if l.email),
        "with_instagram": sum(1 for l in with_site if "Instagram:" in l.social_links),
        "with_facebook": sum(1 for l in with_site if "Facebook:" in l.social_links),
        "with_owner": sum(1 for l in with_site if l.owner_name),
        "enriched": len(enriched),
        "enriched_pct": pct,
        "target_pct": ENRICHMENT_TARGET_PCT,
        "meets_target": bool(with_site) and pct >= ENRICHMENT_TARGET_PCT,
    }


def validate_enrichment(leads: list[Lead]) -> list[str]:
    """Return problems; empty means the data is consistent."""
    problems = []
    for l in leads:
        if l.website_available == "Yes" and not l.website_url:
            problems.append(f"{l.lead_id}: website marked Yes with no URL")
        if l.website_url and not re.match(r"^https?://", l.website_url):
            problems.append(f"{l.lead_id}: website URL has no scheme: {l.website_url}")
        if l.email and not _valid_email(l.email):
            problems.append(f"{l.lead_id}: invalid email {l.email}")
        for part in filter(None, (p.strip() for p in l.social_links.split("|"))):
            if not part.startswith(("Instagram: https://", "Facebook: https://")):
                problems.append(f"{l.lead_id}: bad social link '{part}'")
    return problems


def save_enriched(leads: list[Lead], label: str) -> str:
    os.makedirs(ENRICHED_DATA_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(ENRICHED_DATA_DIR, f"{label.lower().replace(' ', '_')}_{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump([l.to_dict() for l in leads], f, indent=2, ensure_ascii=False)
    return path
