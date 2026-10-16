import os
from dotenv import load_dotenv

load_dotenv()

GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")

WEEKLY_LEAD_TARGET = 100

# country -> list of cities
LOCATIONS = {
    "United Kingdom": ["London", "Manchester", "Birmingham", "Leeds", "Liverpool", "Bristol"],
    "UAE": ["Dubai", "Abu Dhabi", "Sharjah"],
    "Pakistan": ["Islamabad", "Rawalpindi", "Lahore", "Karachi", "Faisalabad"],
    "Saudi Arabia": ["Riyadh", "Jeddah", "Dammam"],  # optional
}

# Start small while testing; add more countries later
ACTIVE_COUNTRIES = ["United Kingdom"]

BUSINESS_TYPES = [
    "Supermarket",
    "Mini Mart",
    "Convenience Store",
    "Grocery Store",
    "Departmental Store",
    "Asian Grocery Store",
    "Local Retail Chain",
    "Organic Food Store",
    "Specialty Food Store",
]

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"


# ---------- Chunk 2: Google Sheets ----------
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "Leads")

# Exact 21 columns from section 9 of the PDF (order matters)
SHEET_COLUMNS = [
    "Lead ID",
    "Business Name",
    "Business Type",
    "Country",
    "City",
    "Address",
    "Phone Number",
    "Email",
    "Website Available",
    "Website URL",
    "Owner/Manager Name",
    "Social Media Links",
    "Order Method",
    "Delivery System",
    "Automation Status",
    "Google Rating",
    "Reviews Count",
    "Lead Priority",
    "Notes",
    "Call Status",
    "Follow-up Date",
]

# Dropdown values used in the sheet
DROPDOWNS = {
    "Website Available": ["Yes", "No"],
    "Delivery System": ["Own Delivery Team", "Third-party Delivery", "No Delivery", "Unknown"],
    "Automation Status": ["Manual", "Semi Automated", "Automated", "Unknown"],
    "Lead Priority": ["High", "Medium", "Low"],
    "Call Status": ["Not Called", "Called", "No Answer", "Interested", "Not Interested", "Follow-up"],
}


# ---------- Chunk 3: Lead search ----------
# Used to build queries like "Asian Grocery Store in Leeds, UK"
COUNTRY_QUERY_NAMES = {
    "United Kingdom": "UK",
    "UAE": "UAE",
    "Pakistan": "Pakistan",
    "Saudi Arabia": "Saudi Arabia",
}

# Region codes bias Google results toward the right country
COUNTRY_REGION_CODES = {
    "United Kingdom": "GB",
    "UAE": "AE",
    "Pakistan": "PK",
    "Saudi Arabia": "SA",
}

SEARCH_PAGE_SIZE = 20          # Places Text Search max per page
SEARCH_MAX_PAGES = 3           # Google returns at most ~60 results per query
SEARCH_PAGE_DELAY_SECONDS = 2  # next page token needs a short moment to activate
RAW_DATA_DIR = "data/raw"


# ---------- Chunk 4: Place details ----------
PLACE_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"
DETAILS_DELAY_SECONDS = 0.2      # small pause between detail calls
DETAILS_CACHE_DIR = "data/details_cache"
LEADS_DATA_DIR = "data/leads"
SKIP_STATUSES = ["CLOSED_PERMANENTLY"]  # leads we do not want to call


# ---------- Chunk 5: Cleaning ----------
CLEANED_DATA_DIR = "data/cleaned"

# Country -> accepted phone regions (a valid number must belong to one of these)
COUNTRY_PHONE_REGIONS = {
    "United Kingdom": {"GB", "GG", "JE", "IM"},
    "UAE": {"AE"},
    "Pakistan": {"PK"},
    "Saudi Arabia": {"SA"},
}

# Words ignored when comparing business names for duplicates
NAME_NOISE_WORDS = {"ltd", "limited", "llc", "pvt", "private", "inc", "co", "the", "and"}


# ---------- Chunk 6: Website and social enrichment ----------
WEBSITE_CACHE_DIR = "data/website_cache"
ENRICHED_DATA_DIR = "data/enriched"
WEBSITE_TIMEOUT_SECONDS = 10
WEBSITE_MAX_PAGES = 4              # homepage + up to 3 contact/about pages per site
WEBSITE_MAX_BYTES = 1_000_000      # ignore anything bigger than ~1 MB per page
WEBSITE_WORKERS = 8                # sites are crawled in parallel; pages within a site are sequential
WEBSITE_PAGE_DELAY_SECONDS = 0.5   # pause between pages on the same site
WEBSITE_USER_AGENT = "Mozilla/5.0 (compatible; MartLeadBot/1.0; business contact lookup)"
RESPECT_ROBOTS_TXT = True
ENRICHMENT_TARGET_PCT = 30         # Chunk 6 is done when >= this % of leads with sites got extra data

# Google often returns a Facebook/Instagram page or a delivery listing as the "website".
# Those are not real websites: social ones move to Social Media Links, the rest are dropped.
SOCIAL_HOSTS = {"facebook.com", "fb.com", "fb.me", "instagram.com", "linktr.ee", "linktree.com"}
NON_WEBSITE_HOSTS = {
    "ubereats.com", "deliveroo.co.uk", "deliveroo.ae", "just-eat.co.uk", "foodpanda.pk",
    "talabat.com", "noon.com", "carriage.com", "google.com", "goo.gl", "maps.app.goo.gl",
    "tiktok.com", "twitter.com", "x.com", "youtube.com", "wa.me", "whatsapp.com", "yelp.com",
    "tripadvisor.com", "tripadvisor.co.uk",
}

# Email handling
EMAIL_IGNORE_DOMAINS = {
    "example.com", "domain.com", "email.com", "yourdomain.com", "yoursite.com", "sentry.io",
    "wixpress.com", "sentry-next.wixpress.com", "godaddy.com", "squarespace.com", "wordpress.com",
    "shopify.com", "schema.org", "w3.org",
}
EMAIL_IGNORE_PREFIXES = ("noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon",
                         "postmaster", "webmaster", "abuse", "privacy", "unsubscribe")
EMAIL_PREFERRED_PREFIXES = ("info", "contact", "hello", "enquiries", "enquiry", "sales", "orders",
                            "order", "shop", "store", "admin", "office", "manager", "support")

# Paths worth visiting for contact details (matched against link text and URL)
CONTACT_PAGE_HINTS = ("contact", "about", "our-story", "ourstory", "who-we-are", "find-us",
                      "store-locator", "team", "meet", "reach-us", "get-in-touch")


# ---------- Chunk 7: Order method, delivery and automation detection ----------
# Delivery platforms: link hosts are strong evidence, plain-text names are weaker evidence.
# (Carriage and Noon are everyday words, so they are only matched by link / explicit names.)
DELIVERY_PLATFORMS = {
    "Deliveroo": {"hosts": ("deliveroo.co.uk", "deliveroo.ae", "deliveroo.com"), "text": ("deliveroo",)},
    "Uber Eats": {"hosts": ("ubereats.com",), "text": ("uber eats", "ubereats")},
    "Just Eat": {"hosts": ("just-eat.co.uk", "just-eat.com", "justeat.com"), "text": ("just eat", "justeat")},
    "Talabat": {"hosts": ("talabat.com",), "text": ("talabat",)},
    "Noon": {"hosts": ("noon.com",), "text": ("noon food", "noon minutes", "noon.com")},
    "Careem": {"hosts": ("careem.com",), "text": ("careem",)},
    "Foodpanda": {"hosts": ("foodpanda.pk", "foodpanda.com"), "text": ("foodpanda",)},
    "InstaShop": {"hosts": ("instashop.com",), "text": ("instashop",)},
    "Carriage": {"hosts": ("carriage.com",), "text": ()},
    "HungerStation": {"hosts": ("hungerstation.com",), "text": ("hungerstation",)},
    "Jahez": {"hosts": ("jahez.net",), "text": ("jahez",)},
    "Mrsool": {"hosts": ("mrsool.co",), "text": ("mrsool",)},
    "Getir": {"hosts": ("getir.com",), "text": ("getir",)},
    "Gopuff": {"hosts": ("gopuff.com",), "text": ("gopuff",)},
}

# Parcel couriers named on a site count as third-party delivery
COURIERS = ("royal mail", "dpd", "evri", "yodel", "parcelforce", "dhl", "aramex", "fedex")

# Raw-HTML fragments that only appear on real online shops (checked in the HTML, not the text)
CART_HTML_MARKERS = ("cdn.shopify.com/s/files", "wc-add-to-cart", "add_to_cart_button",
                     "sqs-add-to-cart-button", "ecwid", "bigcommerce.com")
# Link paths that mean a shopping cart exists
CART_LINK_SUFFIXES = ("/cart", "/basket", "/checkout")

# A site that was scanned but shows no ordering signals at all: say "Unknown" (False) or
# "Walk-in Only" (True). False is the honest default: no evidence is not proof.
INFER_WALK_IN_WHEN_NO_SIGNALS = False

ORDER_METHOD_UNKNOWN = "Unknown"
DELIVERY_UNKNOWN = "Unknown"


# ---- Chunk 7 part 2: automation detection ----
# Widgets/tools are looked for in the raw HTML (script and iframe addresses), so a blog post
# that merely mentions a product name does not count.
CHATBOT_HTML_MARKERS = ("tawk.to", "intercom", "drift.com", "js.driftt.com", "tidio", "livechatinc",
                        "crisp.chat", "freshchat", "manychat", "botpress", "chatwoot", "smartsupp",
                        "olark", "jivosite", "zopim", "zendesk", "landbot")
CRM_HTML_MARKERS = ("hs-scripts.com", "hsforms", "hubspot", "zohopublic", "zoho.com", "salesforce.com",
                    "force.com", "pardot", "pipedrive", "activecampaign", "freshsales")
# Form plugins / hosted forms: count as a "basic form" even if the form tag is built by script
FORM_HTML_MARKERS = ("wpcf7", "gravityforms", "wpforms", "ninja-forms", "forminator", "typeform.com",
                     "jotform.com", "docs.google.com/forms", "forms.gle", "formspree.io", "elementor-form")

# True: a site with no forms or online ordering counts as "Semi Automated" (PDF: "Website available").
# False: it counts as "Manual" (chunk text: Semi needs "basic forms"). False is the stricter reading.
WEBSITE_ONLY_IS_SEMI_AUTOMATED = False
AUTOMATION_UNKNOWN = "Unknown"


# ---------- Chunk 8: Lead priority scoring ----------
# High   = Manual or Semi Automated + good rating + many reviews + has a phone number
# Low    = already Automated (or no usable phone number)
# Medium = everything else (e.g. Unknown automation, or a decent store with few reviews)
PRIORITY_HIGH = "High"
PRIORITY_MEDIUM = "Medium"
PRIORITY_LOW = "Low"
PRIORITY_VALUES = (PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW)

PRIORITY_HIGH_AUTOMATION = ("Manual", "Semi Automated")   # still doing things by hand = best fit for our service
PRIORITY_LOW_AUTOMATION = ("Automated",)                  # already has online store / app / chatbot / CRM
PRIORITY_HIGH_MIN_RATING = 4.0                            # "good rating" (Google stars)
PRIORITY_HIGH_MIN_REVIEWS = 50                            # "many reviews"; lower this for smaller markets


# ---------- Chunk 9: Pipeline ----------
PIPELINE_STATE_FILE = "data/pipeline_state.json"   # this week's count + place IDs already processed (delete to reset)
PIPELINE_MAX_PER_QUERY = 15        # new leads kept from one city + business type (spreads leads across cities/types)
PIPELINE_MAX_PER_CITY = 25         # new leads kept per city on the first pass
PIPELINE_MAX_DETAILS_PER_QUERY = 15  # Place Details calls (billed) per search; raise it if searches run dry
PIPELINE_MAX_QUERIES = 80          # safety limit on searches per run, so a bad config cannot run up the bill
PIPELINE_QUERY_DELAY_SECONDS = 1   # pause between searches
# If the caps above leave the weekly target unmet, a second pass runs with the per-query and per-city caps removed.


# ---------- Chunk 10: Weekly automation and polish ----------
# Logging and run bookkeeping (relative paths are inside the project folder; run_weekly.py switches to it first)
LOG_DIR = "logs"
LOG_FILE = "weekly.log"            # rotates: weekly.log, weekly.log.1, ...
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 8
RUN_STATUS_FILE = "logs/last_run.json"        # result of the latest run (overwritten each time)
RUN_HISTORY_FILE = "logs/run_history.jsonl"   # one line per run, kept forever
RUN_LOCK_FILE = "data/run.lock"               # stops two runs from overlapping
RUN_LOCK_STALE_HOURS = 6                      # a lock older than this is a crashed run and is ignored

# Google API guard (guard.py). These count billed requests, not leads:
#   search  = one Places Text Search request (one page of results; a search uses up to 3)
#   details = one Place Details request that was NOT already in the on-disk cache
# They are safety rails against a runaway bill, not Google's own quotas. The numbers below are an ESTIMATE of what a
# 100-lead week needs (very roughly 20-40 search and 200-400 details requests), not a measurement: after the first real run
# read "api_usage" in logs/last_run.json and adjust. Set them from your own Google Cloud budget; None = no limit.
# Also set a daily quota cap and a budget alert in Google Cloud Console for a limit Google enforces itself.
API_USAGE_FILE = "data/api_usage.json"        # running total for the current calendar month
API_LIMITS_PER_RUN = {"search": 150, "details": 700}
API_LIMITS_PER_MONTH = {"search": 600, "details": 3000}
API_MAX_CONSECUTIVE_FAILURES = 6              # this many failed Google requests in a row = stop the run (bad key, no billing, offline)
