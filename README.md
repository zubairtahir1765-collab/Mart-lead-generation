# Mart Lead Generation System

Every week it finds at least 100 new, phone-verified mart / grocery / retail leads (UK, UAE, Pakistan, Saudi Arabia),
works out how each one takes orders, delivers and automates, scores them High / Medium / Low, and appends them to a
Google Sheet for the calling team. Leads already in the sheet are never added twice.

This top half is the day-to-day guide: setup, scheduling, checking that it ran, and fixing problems.
The chunk-by-chunk reference (how each part works inside) starts at [Chunk 1](#chunk-1-setup-and-config).

---

## Quick start

1. **Set up Google** (API key, service account, the sheet): follow [Chunk 1](#chunk-1-setup-and-config) and [Chunk 2](#chunk-2-google-sheet-template) below.
2. **Prepare the sheet once**: `python setup_sheet.py` writes the 21 headers, formatting, dropdowns, Priority colors and filter views.
3. **Try a small run that writes nothing**: `python run_weekly.py --dry-run --target 10 --cities Leeds`
4. **Do a real run**: `python run_weekly.py` (about 100 leads; takes a while because it visits mart websites).
5. **Schedule it** (next section), then confirm the first scheduled run (see [Check that it ran](#check-that-it-ran)).

`run_weekly.py` is the command to schedule. It takes the same options as `main.py` (`--target`, `--countries`, `--cities`,
`--types`, `--dry-run`) and adds logging, a run lock, API limits and the status files.

> **Which countries run?** The weekly run uses `ACTIVE_COUNTRIES` in `config.py`, which is **United Kingdom only** right now
> (it was set small for testing). The brief also covers UAE and Pakistan (Saudi Arabia optional). When you are ready, add
> them to `ACTIVE_COUNTRIES`, or pass `--countries "United Kingdom,UAE,Pakistan"` in the scheduled command.

---

## Weekly automation

Pick one. All three run the same `run_weekly.py`.

| Option | Good when | Watch out for |
|---|---|---|
| **Windows Task Scheduler** | the lead-gen PC is on most Mondays | the PC must be on and you logged in (a missed run is made up when it is back on) |
| **cron** (Linux / Mac / a small server) | you have a machine that is always on | cron does nothing if the machine is off at that moment |
| **GitHub Actions** | you want no machine involved | secrets setup; the runner is wiped each run (fine: the sheet is the memory) |

### Option A: Windows Task Scheduler

Use the project's virtual environment (`python -m venv venv` + `pip install -r requirements.txt`). The task starts with
a different PATH than your terminal, so `deploy\run_weekly.bat` uses `venv\Scripts\python.exe` when it exists.

```powershell
powershell -ExecutionPolicy Bypass -File deploy\install_windows_task.ps1
# another day or time:
powershell -ExecutionPolicy Bypass -File deploy\install_windows_task.ps1 -Day Tuesday -Time 07:30
```
It registers a task called `MartLeadsWeekly` (default: Mondays 09:00, runs a missed run when the PC is back on, never two at once,
gives up after 3 hours). Run it now to test: `Start-ScheduledTask -TaskName MartLeadsWeekly`. Remove it:
`Unregister-ScheduledTask -TaskName MartLeadsWeekly -Confirm:$false`.

The task runs as you while you are logged in. To run it logged out, open the task in the Task Scheduler app,
Properties, "Run whether user is logged on or not", and enter your Windows password.

Plain-command alternative (no missed-run catch-up):
```
schtasks /Create /SC WEEKLY /D MON /ST 09:00 /TN MartLeadsWeekly /TR "\"C:\full\path\to\mart-lead-gen\deploy\run_weekly.bat\""
```

### Option B: cron (Linux / Mac)

```bash
chmod +x deploy/run_weekly.sh
mkdir -p logs
crontab -e        # paste the line from deploy/crontab.example, with your real path
crontab -l        # check it is there
```
The line (Mondays 08:00, machine time):
```
0 8 * * 1 /full/path/to/mart-lead-gen/deploy/run_weekly.sh >> /full/path/to/mart-lead-gen/logs/cron.out 2>&1
```
`run_weekly.sh` switches to the project folder and uses `venv/bin/python` if it exists, so cron's empty environment is not a problem.

### Option C: GitHub Actions

The workflow is `.github/workflows/weekly-leads.yml` (Mondays 03:00 UTC, plus a "Run workflow" button).

1. Push the project to a **private** GitHub repository (`.env`, `credentials.json` and `data/` are git-ignored; never commit them).
   If the repository root is the folder *above* `mart-lead-gen`, move the workflow to that root's `.github/workflows/` and
   uncomment the `defaults: run: working-directory: mart-lead-gen` block in it.
2. Repo **Settings > Secrets and variables > Actions > New repository secret**, add three secrets:
   `GOOGLE_PLACES_API_KEY`, `GOOGLE_SHEET_ID`, and `GOOGLE_CREDENTIALS_JSON` (paste the **entire contents** of `credentials.json`).
3. Optional: a repository **variable** `WEEKLY_ARGS` for the scheduled run, e.g. `--countries "United Kingdom,UAE"`.
4. Test: **Actions > Weekly mart leads > Run workflow**. In the "args" box try `--dry-run --target 10 --cities Leeds` first.

Good to know: cron times are UTC and GitHub can start a run a few minutes late. A failed run (any non-zero exit code) shows as a
red run, and GitHub notifies the person who last edited the workflow's schedule (if their notification settings allow it). The logs are saved as a downloadable artifact for 30 days. On *public* repositories GitHub switches
scheduled workflows off after 60 days without repository activity (another reason to use a private one).

---

## Check that it ran

Three places, from quick to detailed:

| File | What it tells you |
|---|---|
| `logs/last_run.json` | the latest run: `status`, `exit_code`, how many leads were added, the week's total, API requests used, the reason if it stopped |
| `logs/run_history.jsonl` | one line per run, forever. A week with no line means the schedule did not fire |
| `logs/weekly.log` | everything that happened, with timestamps (rotates at 1 MB, keeps 8 old files). Problems only: `grep -E "WARNING|ERROR" logs/weekly.log` |

**Exit codes** (also the `status` field). A scheduler treats anything but 0 as a failed run:

| Code | status | Meaning | What to do |
|---|---|---|---|
| 0 | `ok` | week's target reached (or was already reached earlier this week) | nothing |
| 1 | `short` | finished but fewer than the target; the log says why | usually add countries or raise limits (see Troubleshooting) |
| 2 | `error` | sheet not set up, sheet write failed, validation failed, or Google kept failing | read `message` in `last_run.json` |
| 3 | `quota` | stopped by the API request limit (see below) | wait for next month, or raise the limit on purpose |
| 4 | `locked` | another run was already going, so this one did not start | nothing, unless it keeps happening (see Troubleshooting) |
| 130 | `interrupted` | someone pressed Ctrl+C | run it again; finished batches are kept |

**Done check for Chunk 10 ("it runs on its own once a week")**: register the schedule, trigger it once by hand with the
scheduler's own "run now" (`Start-ScheduledTask -TaskName MartLeadsWeekly`, or a `* * * * *` cron line for one minute, or
GitHub's "Run workflow"), and confirm a new line in `logs/run_history.jsonl` and a sensible `logs/last_run.json`. Then the next
Monday's line appearing by itself is the real proof.

---

## API request limits (cost guardrails)

Google bills per request, so `guard.py` counts the billed requests and stops before a limit is crossed. It sits right where the
HTTP request is made, so it protects `python main.py` runs too, not only scheduled ones. Settings are in the Chunk 10 block of `config.py`:

| Setting | Default | Counts |
|---|---|---|
| `API_LIMITS_PER_RUN["search"]` | 150 | Places Text Search requests per run (one page of results = one request; a search uses up to 3) |
| `API_LIMITS_PER_RUN["details"]` | 700 | Place Details requests per run that were **not** already in the on-disk cache |
| `API_LIMITS_PER_MONTH["search"]` / `["details"]` | 600 / 3000 | the same, per calendar month (kept in `data/api_usage.json`, starts again each month) |
| `API_MAX_CONSECUTIVE_FAILURES` | 6 | Google requests failing in a row before the run stops (bad key, billing off, no internet) |

- The defaults are **starting points, based on my estimate** of what a 100-lead week needs (very roughly 20-40 search and
  200-400 details requests), not on a measured run. After your first real run, look at `api_usage` in `logs/last_run.json` for the
  actual numbers and set the limits from those and your own Google Cloud budget (`None` removes a limit).
  Check current prices on the Google Maps Platform pricing page.
- A request is counted when it is sent, including ones that then fail, so the count can be slightly above what Google bills. That is the safe direction.
- At a limit the run **stops cleanly**: batches already written to the sheet stay, progress is saved, exit code is 3, and the message names the limit and the setting to raise.
- Six failures in a row is treated as a setup problem, not a one-off: the run stops with exit code 2 instead of trying all remaining searches.
- These are your own safety rails. For a limit Google enforces itself, also set a **daily quota cap** and a **budget alert**
  in Google Cloud Console (APIs & Services > Places API (New) > Quotas; Billing > Budgets & alerts).
- Website crawling (Chunk 6) and the Google Sheets API are not billed and are not counted here. The Sheets API is only called a few times per run.

---

## Logging and error handling

What an unattended run does when something goes wrong:

| Situation | What happens |
|---|---|
| One search or one place fails | logged, skipped, the run continues (as before) |
| 6 Google requests fail in a row | run stops, exit 2, reason in the log |
| API request limit reached | run stops, exit 3, finished batches kept |
| The sheet cannot be opened (a passing Google hiccup) | retried 3 times (waits 5 s, 10 s) before giving up |
| Writing a batch to the sheet fails | retried, then the run stops; the batch is saved in `data/enriched/` and not counted, so the next run redoes it |
| Validation of a batch fails | run stops, nothing from that batch is written |
| The previous run is still going | the new one exits immediately with code 4 (lock file `data/run.lock`) |
| A run crashed and left the lock | a lock older than 6 hours is ignored automatically; to rerun sooner, delete `data/run.lock` |
| The cosmetic sheet refresh fails | logged as a warning; the leads are already saved; the run still succeeds |
| Anything unexpected | full traceback in the log, exit 2, the lock is always released |

Everything the other modules `print()` (progress, per-place lines) is captured into `logs/weekly.log` too, so nothing needed
changing in Chunks 3-9. Non-Latin business names (Arabic, Urdu) are kept intact in the log file and are shown as `?` on a
console that cannot display them, instead of crashing the run.

---

## Sheet extras

Applied by `python setup_sheet.py` and refreshed automatically after each weekly run that adds leads
(`sheet_extras.py`; safe to repeat, it never duplicates anything):

- **Dropdowns**: Call Status (`Not Called`, `Called`, `No Answer`, `Interested`, `Not Interested`, `Follow-up`), Lead Priority, Website Available,
  Delivery System, Automation Status. They now cover the whole sheet as it grows (Chunk 2 stopped at row 1000). Typing free text is still allowed.
- **Lead Priority colors**: High green, Medium amber, Low grey. A color rule is added only if it is missing, so a color you change by hand is kept.
- **Filter views** (Data > Filter views): **To call: High priority** (High and Not Called, most reviews first) and **Follow-ups due** (Follow-up, earliest date first).
  Created only if missing, so your edits to them stay.
- **Header filter** (the funnel on row 1): created once by `setup_sheet.py`. The weekly refresh does not touch it, because resetting it
  would clear the filter someone is using.

---

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| No new line in `run_history.jsonl` on Monday | the schedule did not fire. Windows: the PC was off or you were logged out (see the note under Option A). cron: the machine was off. GitHub: check the Actions tab, and that the repo has had activity in the last 60 days if it is public |
| `short`, "Every search for the selected countries is used up" | the active countries do not hold enough new phone-verified leads. Add countries (`--countries all`) or raise `PIPELINE_MAX_DETAILS_PER_QUERY` / `PIPELINE_MAX_QUERIES` (and the matching API limits) |
| `error`: "6 Google API requests failed in a row" | open the log: a `403` means the key is restricted/invalid or billing/Places API (New) is not enabled; a connection error means no internet |
| `quota` | you hit your own limit. It is working as designed. Raise the limit deliberately, or wait for next month |
| `error`: "header row is missing or different" | run `python setup_sheet.py` |
| `error`: "could not open the sheet" | the sheet is not shared with the service account email as Editor, or `GOOGLE_SHEET_ID` is wrong |
| exit code 4 every week | `data/run.lock` is left over and fresh. Delete it |
| Started a new sheet and the week shows "already complete" | delete `data/pipeline_state.json` (see Chunk 9) |
| `ModuleNotFoundError` in `cron.out` / `task_scheduler.out` | the scheduler used the wrong Python. Create `venv` in the project folder and run `pip install -r requirements.txt` there |

---

## Project layout

```
run_weekly.py        the command to schedule (Chunk 10)        main.py          the pipeline; `python main.py` also works
guard.py             API request limits + failure breaker      search.py        Places search (Chunk 3)
runtools.py          log file, run lock, status files          details.py       Place Details (Chunk 4)
sheet_extras.py      dropdowns, colors, filter views           cleaning.py      phone check, dedupe (Chunk 5)
setup_sheet.py       one-time sheet setup                      website.py       website/social enrichment (Chunk 6)
sheets.py            Google Sheets helpers (Chunk 2)           detection.py     order / delivery / automation (Chunk 7)
config.py            every setting                             scoring.py       High / Medium / Low (Chunk 8)
deploy/              cron + Windows scheduler files            .github/workflows/weekly-leads.yml
logs/  data/         created on first run (git-ignored)        test_*.py        checks (below)
```

## Tests

Offline checks need no internet, API key or Google account:
```bash
python test_automation_offline.py      # Chunk 10: API guard, logging, lock, run_weekly end to end (with fakes)
python test_sheet_extras_offline.py    # Chunk 10: dropdown / color / filter-view requests
python test_pipeline_offline.py        # Chunk 9: the pipeline
python test_cleaning_offline.py  python test_website_offline.py  python test_detection_offline.py  python test_scoring.py
```
The `test_places.py`, `test_search.py`, `test_details.py`, `test_website.py`, `test_detection.py`, `test_priority.py` scripts
are live checks and use real (billed) API requests, so they count towards the API limits above.

---

## Chunk 1: Setup and config

### 1. Install
```bash
python -m venv venv
# Windows: venv\Scripts\activate
# Mac/Linux: source venv/bin/activate
pip install -r requirements.txt
```

### 2. Google Cloud
1. Create a project at https://console.cloud.google.com
2. Enable billing (set a budget alert)
3. Enable: Places API (New), Google Sheets API, Google Drive API
4. Create an API key and restrict it to Places API (New)

### 3. Add your key
```bash
cp .env.example .env     # Windows: copy .env.example .env
```
Edit `.env` and paste your key.

### 4. Test
```bash
python test_places.py
```
Success = `Status: 200` plus a list of Leeds grocery stores.


## Chunk 2: Google Sheet template

### 1. Create the service account
1. In Google Cloud Console (same project), go to **IAM & Admin -> Service Accounts -> Create service account**.
2. Open it -> **Keys -> Add key -> Create new key -> JSON**. Save the file as `credentials.json` in the project root.
3. Copy the service account email (looks like `name@project.iam.gserviceaccount.com`).

### 2. Create the Google Sheet
1. Create a blank Google Sheet (e.g. "Mart Leads").
2. Click **Share** and add the service account email as **Editor**.
3. Copy the Sheet ID from the URL: `https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit`

### 3. Update `.env`
```
GOOGLE_SHEET_ID=<SHEET_ID>
GOOGLE_CREDENTIALS_FILE=credentials.json
WORKSHEET_NAME=Leads
```

### 4. Run
```bash
python setup_sheet.py   # writes the 21 headers, formatting, dropdowns
python test_sheet.py    # appends one dummy row
```
Success = a `TEST - Dummy Mart` row appears in your sheet with Lead ID `LD-0001`.


## Chunk 3: Lead search

`search.py` provides `search_leads(city, business_type, country)`:
- Builds queries like `Asian Grocery Store in Leeds, UK`
- Uses Places Text Search (New) with pagination (up to 3 pages, about 60 results)
- Returns normalized, de-duplicated raw results (place_id, name, address, coordinates, rating, reviews, types, business status)
- `save_raw()` stores results in `data/raw/` as JSON

Run:
```bash
python test_search.py
python test_search.py "Manchester" "Supermarket" "United Kingdom"
```
Success = 20+ unique results printed.


## Chunk 4: Place details

- `models.py`: the `Lead` dataclass. `to_sheet_dict()` returns keys matching the 21 sheet columns.
- `details.py`:
  - `get_place_details(place_id)` fetches phone, website, rating, review count, Google Maps link and status. Results are cached in `data/details_cache/`, so re-runs cost nothing.
  - `build_lead(raw, details)` merges a search result and its details into a `Lead`.
  - `enrich_results(raw_results, limit)` does it for a whole list and skips permanently closed businesses.
  - `completeness_report(leads)` shows how many leads have each field.

The Google Maps link is stored on the lead and appended to Notes in the sheet (there is no dedicated column for it in the required 21).

Run:
```bash
python test_details.py
python test_details.py "Leeds" "Supermarket" "United Kingdom" 20
```


## Chunk 5: Cleaning, verification and deduplication

`cleaning.py`:
- `normalize_phone(raw, country)` parses with `phonenumbers`, checks the number is valid and belongs to the lead's country, and returns E.164 plus international format.
- `clean_leads(leads, start_id=0, existing_phones=None, existing_name_addr=None)`:
  1. tidies names and addresses
  2. drops leads with a missing, invalid or wrong-country phone
  3. removes duplicates by place ID, phone, or name + address (keeps the most complete record)
  4. skips leads already in your sheet if you pass the existing keys
  5. assigns Lead IDs (`LD-0001`...) continuing from `start_id` (`sheets.max_lead_number(ws)` gives the current highest)
- `validate_output(leads)` returns a list of problems; empty means done.
- Rejected leads and the reason are saved to `data/cleaned/*_rejected.json`.

Run:
```bash
python test_cleaning_offline.py      # no API needed
python test_cleaning.py              # live, Leeds grocery stores
```

## Chunk 6: Website and social enrichment

`website.py`:
- `classify_website(url)` cleans the URL Google gave us (adds https, strips `utm_` tracking). If Google's "website" is really a Facebook/Instagram page it moves to Social Media Links and **Website Available = No**; delivery-platform and map links are dropped.
- `enrich_site(url)` visits the homepage plus up to 3 contact/about pages (4 pages max), obeys `robots.txt`, and finds:
  - **Email**: best one goes in Email, up to 2 more in Notes ("Other emails: ..."). Handles `mailto:`, `[at]/[dot]`, Cloudflare-hidden emails; ignores `noreply@`, image names, web-agency footers.
  - **Social links**: first Instagram and Facebook profile/page on the site, stored as `Instagram: <url> | Facebook: <url>`. Share buttons, posts and tracking pixels are ignored.
  - **Owner/Manager name**: only when stated plainly (`Owner: X`, `Founded by X`, `X, Store Manager`, JSON-LD founder). Stored as `Name (Role)`. Company names and look-alike phrases are rejected.
- `enrich_leads(leads)` runs this for every lead in parallel, never overwrites a field that is already filled, and crawls a site shared by several branches only once. Successful crawls are cached in `data/website_cache/`; failures are not.
- `enrichment_report(leads)` is the done check: % of leads with a real website that got an email, social link or owner name (target 30%+).
- Facebook and Instagram pages themselves are never opened (login walls / terms); we only record links found on the mart's own site.
- Leads whose site is unreachable or blocks bots get "Website unreachable/blocked" added to Notes.

Run:
```bash
python test_website_offline.py     # no internet needed
python test_website.py             # live, Leeds grocery stores
python test_website.py "Dubai" "Supermarket" "UAE" 40
```
Settings (page limit, timeout, workers, ignore lists) are in the Chunk 6 block of `config.py`.

## Chunk 7: Order method, delivery and automation detection

Rule-based, no AI calls. While the Chunk 6 crawler has a page open, `detection.collect_evidence()` notes which
keywords, links and widgets it contains and stores that small `evidence` dict with the crawl result (not the page text).
`detection.detect_leads(leads)` turns the evidence into sheet values, so rules can be tuned without crawling again.

**Order Method**: any of `Phone Orders`, `WhatsApp Orders`, `Website Orders`, `Mobile App`, `Delivery Platforms`, joined with ` + `.
- WhatsApp: a `wa.me/<number>` style link beats a text mention; share buttons (`wa.me/?text=`) are ignored.
- Website: add-to-cart / basket / checkout wording in visible text, shop markers (Shopify, WooCommerce...), `/cart` links, "order online".
- Mobile App: App Store / Google Play link or wording; a delivery platform's own app is not counted.
- Delivery Platforms: Deliveroo, Uber Eats, Just Eat, Talabat, Noon, Careem, Foodpanda, InstaShop, etc. (`DELIVERY_PLATFORMS` in `config.py`).

**Delivery System**: `Own Delivery Team` / `Third-party Delivery` / `No Delivery` / `Unknown`.
Own drivers/team wording wins, then a platform or courier (DPD, Royal Mail...), then generic "we deliver / free delivery",
then "collection only" / "no delivery". "No delivery charge" is not "No Delivery".

**Automation Status**: `Manual` / `Semi Automated` / `Automated` / `Unknown`.
- Automated: online store/cart, own mobile app, chatbot widget (Tawk.to, Tidio, Intercom...), CRM script (HubSpot, Zoho, Salesforce...) or order notifications wording (order confirmation, tracking, SMS updates).
- Semi Automated: a website with a basic contact/order form (or "order online" wording) and no store. Search boxes, logins and one-field newsletter sign-ups do not count as forms.
- Manual: no website, or a website with no forms or online ordering. A WhatsApp button alone is still Manual (staff reply by hand). Listings on Deliveroo etc. are the platform's automation, not the mart's.
- Unknown: the website was unreachable or blocked.
- `WEBSITE_ONLY_IS_SEMI_AUTOMATED` in `config.py` flips "website with no forms" to Semi Automated (the PDF's wording is "Website available"); default is the stricter Manual.

**Notes**: a short reason is added, e.g.
`Has WhatsApp button, no cart; delivery: not stated; automation: Manual (website has no forms or online ordering)`.

**Fallbacks**: `Unknown` for order method and delivery when there is no website, the site is unreachable/blocked, or nothing was found.
Set `INFER_WALK_IN_WHEN_NO_SIGNALS = True` to say `Walk-in Only` for scanned sites with no ordering signals instead.
If Google's "website" was really a Deliveroo/Uber Eats page, it is kept as a hint, so the lead gets `Delivery Platforms` / `Third-party Delivery`.

**Done check**: `detection_report(leads)` / `validate_detection(leads)` confirm every lead has all three fields filled with allowed values
and a Notes reason. `detect_leads()` never leaves a field blank: a lead that errors is set to `Unknown`.

**Upgrading**: cache files from older versions have no automation evidence, so those sites are crawled once more automatically
(`EVIDENCE_VERSION` in `detection.py`). The Automation Status dropdown gained `Unknown`: re-run `python setup_sheet.py` to refresh it in your sheet.

Pipeline: `enrich_leads(leads)` then `detect_leads(leads)`.

Run:
```bash
python test_detection_offline.py     # no internet needed
python test_detection.py             # live, Leeds grocery stores
python test_detection.py "Dubai" "Supermarket" "UAE" 40
```

## Chunk 8: Lead priority scoring

Rule-based, no API calls. `scoring.score_leads(leads)` sets **Lead Priority** (`High` / `Medium` / `Low`) on every lead.
Checked in this order:

| Priority | When |
|----------|------|
| **Low** | Automation is `Automated` (already has a store, app, chatbot or CRM), **or** there is no phone number |
| **High** | Automation is `Manual` or `Semi Automated` **and** rating >= 4.0 **and** reviews >= 50 **and** has a phone number |
| **Medium** | Everything else: automation `Unknown`, rating/reviews missing or not high enough |

- A short reason goes into Notes, e.g. `Priority High: Manual, 4.6 stars, 212 reviews, phone ok` or
  `Priority Medium: Unknown, 3.8 stars, 12 reviews, phone ok; not High because automation unclear, rating under 4, under 50 reviews`.
- **Call Status** is set to `Not Called` when blank. **Follow-up Date** is left blank.
- Thresholds are in the Chunk 8 block of `config.py` (`PRIORITY_HIGH_MIN_RATING`, `PRIORITY_HIGH_MIN_REVIEWS`, ...).
  Review counts run lower in some markets (e.g. smaller Pakistani cities), so lower the review threshold if High looks too rare.
- `validate_priority(leads)` / `priority_report(leads)` are the done check: every lead has High, Medium or Low.

Pipeline: `enrich_leads(leads)` -> `detect_leads(leads)` -> `score_leads(leads)`.

Run:
```bash
python test_scoring.py                 # no internet needed
python test_priority.py                # live, Leeds grocery stores
python test_priority.py "Dubai" "Supermarket" "UAE" 40
```

## Chunk 9: Pipeline and Sheet export

One command fills the sheet with this week's new, clean leads:
```bash
python main.py                                  # 100 leads from ACTIVE_COUNTRIES (config.py)
python main.py --dry-run                        # same run, but nothing is written to the sheet
python main.py --target 150
python main.py --countries "UAE,Pakistan"       # or: --countries all
python main.py --cities "Leeds,London" --types "Supermarket,Mini Mart"
```
Needs everything from Chunks 1-2 (API key, `credentials.json`, sheet shared, `python setup_sheet.py` run once).

**What a run does**, looping countries -> cities -> business types in the order of `config.py`:
1. Reads the sheet first. Leads already there from earlier weeks are skipped (same phone, or same name + address).
2. Per search: Places search -> details -> clean/verify phones -> website enrichment -> detection -> priority scoring.
3. Appends each finished batch to the sheet straight away (one API call per batch), so a crash never loses finished work.
   Every batch is also saved in `data/enriched/`.
4. Stops the moment the week's target is reached (the last batch is trimmed to fit exactly).

**Keeping it fair and cheap** (Chunk 9 block of `config.py`):
- `PIPELINE_MAX_PER_QUERY` (15) and `PIPELINE_MAX_PER_CITY` (25) stop one city or one business type from filling the whole week.
  Within a search the best-reviewed leads are kept. If the target is still not met, a second pass runs without these caps.
- `PIPELINE_MAX_DETAILS_PER_QUERY` (30) limits billed Place Details calls per search.
- `PIPELINE_MAX_QUERIES` (80) is a hard stop on searches per run, so a wrong setting cannot run up the bill.

**Weekly memory**: `data/pipeline_state.json` holds this ISO week's count and every place already processed.
Run the command twice in a week and the second run only fetches what is still missing; places seen before are never
paid for again. Delete the file to reset it (for example if you clear the sheet to start over).

**Exit codes**: `0` target reached, `1` short of the target (the summary says why), `2` stopped on an error
(sheet not set up, failed sheet write, failed validation, Google failing repeatedly), `3` stopped by the API request limit (Chunk 10). If the sheet write fails, the batch is kept in `data/enriched/`
and is not counted, so just run the command again.

**Done check**: `python main.py` prints `Pipeline complete: the sheet has N leads this week`, with N at least 100, and a
breakdown by city and priority.

Notes:
- The Chunk 2 dropdowns covered rows 2 to 1000. Since Chunk 10 they are re-applied to the whole sheet after every weekly run, so there is no limit to manage.
- Lead IDs continue from the highest `LD-` number already in the sheet.

Run the offline check (no internet, no Google account needed):
```bash
python test_pipeline_offline.py
```
First real run: try `python main.py --dry-run --target 10 --cities Leeds` to see it work, then `python main.py`.

## Chunk 10: Weekly automation and polish

Operational details are in the top half of this README. In short, what Chunk 10 added:

- `run_weekly.py` (the entry point to schedule), `guard.py` (API limits), `runtools.py` (logging, run lock, status files),
  `sheet_extras.py` (dropdowns, colors, filter views), `deploy/` and `.github/workflows/weekly-leads.yml` (the three schedulers).
- Small changes to existing files: `search.py` and `details.py` call the guard at each billed request; `main.py` stops cleanly on a
  guard stop (`stop_reason` / `stop_kind` in the summary, exit code 3 for the request limit) and exposes `build_parser()`;
  `setup_sheet.py` also applies the extras; `config.py` has a Chunk 10 block.
- No new Python packages.

**Done check**: the schedule fires by itself once a week and leaves a new line in `logs/run_history.jsonl`
(see [Check that it ran](#check-that-it-ran)). Offline check: `python test_automation_offline.py` and `python test_sheet_extras_offline.py`.

## Roadmap
1. Setup and config
2. Google Sheet template
3. Lead search
4. Place details
5. Cleaning, verification, dedupe
6. Website and social enrichment
7. Order method / delivery / automation detection
8. Lead priority scoring
9. Pipeline and Sheet export
10. Weekly automation and polish

## Dashboard (optional)

A local web interface over the same files and commands. No new packages.
```bash
python ui/server.py        # opens http://127.0.0.1:8765
```
- **Overview**: the week's 100-lead progress, last run status, Google API use against your monthly limits, recent runs.
- **Leads**: search and filter the leads saved in `data/enriched/`, open one to call, set Call Status and Follow-up Date (saved locally, and written to the sheet when it is connected).
- **Run**: choose target, countries, city, type and dry run, start `run_weekly.py`, and watch `logs/weekly.log` live. It shares the run lock, so it cannot overlap a scheduled run.
