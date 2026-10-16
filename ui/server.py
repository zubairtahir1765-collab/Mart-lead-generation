"""Local dashboard for the Mart Lead Generation System.   Run:  python ui/server.py   ->  http://127.0.0.1:8765
No new packages: standard library only. It reads logs/ and data/, and starts run_weekly.py as a normal subprocess."""
import glob, json, os, subprocess, sys, threading, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import config  # noqa: E402

UI_STATE = "data/ui_state.json"          # call status / follow-up edits made in the dashboard
PORT = int(os.getenv("DASHBOARD_PORT", "8765"))
_proc = {"p": None}
_lock = threading.Lock()


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def history(n=12):
    try:
        with open(config.RUN_HISTORY_FILE, encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip()]
    except (OSError, ValueError):
        rows = []
    return rows[-n:]


def running():
    p = _proc["p"]
    return bool(p and p.poll() is None) or os.path.exists(config.RUN_LOCK_FILE) and \
        (os.path.getmtime(config.RUN_LOCK_FILE) > __import__("time").time() - config.RUN_LOCK_STALE_HOURS * 3600)


def api_status():
    state = read_json(config.PIPELINE_STATE_FILE, {})
    return {
        "target": config.WEEKLY_LEAD_TARGET, "week": state.get("week"), "week_added": state.get("added", 0),
        "last_run": read_json(config.RUN_STATUS_FILE, None), "history": history(),
        "api_month": read_json(config.API_USAGE_FILE, {}), "limits_month": config.API_LIMITS_PER_MONTH,
        "limits_run": config.API_LIMITS_PER_RUN, "running": running(),
        "options": {"locations": config.LOCATIONS, "types": config.BUSINESS_TYPES, "active": config.ACTIVE_COUNTRIES},
        "sheet": bool(config.GOOGLE_SHEET_ID and config.GOOGLE_SHEET_ID != "your_sheet_id_here"),
    }


def api_leads():
    seen, out = set(), []
    for path in sorted(glob.glob(os.path.join(config.ENRICHED_DATA_DIR, "*.json")), reverse=True):
        for l in read_json(path, []):
            key = l.get("lead_id") or l.get("place_id")
            if key in seen:
                continue
            seen.add(key)
            l.pop("site_evidence", None)
            out.append(l)
    edits = read_json(UI_STATE, {})
    for l in out:
        l.update(edits.get(l.get("lead_id"), {}))
    out.sort(key=lambda l: l.get("lead_id", ""), reverse=True)
    return out


def save_edit(lead_id, fields):
    allowed = {k: v for k, v in fields.items() if k in ("call_status", "follow_up_date")}
    with _lock:
        edits = read_json(UI_STATE, {})
        edits.setdefault(lead_id, {}).update(allowed)
        os.makedirs("data", exist_ok=True)
        with open(UI_STATE, "w", encoding="utf-8") as f:
            json.dump(edits, f, ensure_ascii=False, indent=1)
    synced = False
    try:                                    # best effort: mirror the change into the Google Sheet
        if config.GOOGLE_SHEET_ID and config.GOOGLE_SHEET_ID != "your_sheet_id_here":
            from sheets import get_worksheet
            ws = get_worksheet()
            row = ws.find(lead_id, in_column=1).row
            for k, col in (("call_status", "Call Status"), ("follow_up_date", "Follow-up Date")):
                if k in allowed:
                    ws.update_cell(row, config.SHEET_COLUMNS.index(col) + 1, allowed[k])
            synced = True
    except BaseException:
        pass
    return synced


def start_run(body):
    if running():
        return {"ok": False, "error": "A run is already in progress."}
    args = [sys.executable, os.path.join(ROOT, "run_weekly.py")]
    if body.get("dry_run"):
        args.append("--dry-run")
    for flag, key in (("--target", "target"), ("--countries", "countries"), ("--cities", "cities"), ("--types", "types")):
        v = body.get(key)
        if v:
            args += [flag, str(v)]
    _proc["p"] = subprocess.Popen(args, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True, "command": "python run_weekly.py " + " ".join(args[2:])}


def tail_log(offset):
    path = os.path.join(config.LOG_DIR, config.LOG_FILE)
    try:
        size = os.path.getsize(path)
        if offset is None or offset > size:
            offset = max(0, size - 6000)
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
        return {"offset": offset + len(data), "text": data.decode("utf-8", "replace")}
    except OSError:
        return {"offset": 0, "text": ""}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, obj, code=200, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            with open(os.path.join(ROOT, "ui", "index.html"), "rb") as f:
                return self.send(f.read(), ctype="text/html")
        if u.path == "/api/status":
            return self.send(api_status())
        if u.path == "/api/leads":
            return self.send(api_leads())
        if u.path == "/api/log":
            off = parse_qs(u.query).get("offset", [None])[0]
            return self.send(tail_log(int(off) if off else None))
        self.send({"error": "not found"}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 2) or b"{}")
        if self.path == "/api/run":
            return self.send(start_run(body))
        if self.path == "/api/lead":
            return self.send({"ok": True, "synced": save_edit(body.get("lead_id", ""), body)})
        self.send({"error": "not found"}, 404)


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Dashboard: http://127.0.0.1:{PORT}   (Ctrl+C to stop)")
    threading.Timer(0.6, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
