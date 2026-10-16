"""Chunk 10: the unattended weekly run. This is the file the scheduler calls.

    python run_weekly.py                         # same options as main.py
    python run_weekly.py --dry-run --target 10 --cities Leeds

Compared with `python main.py` it adds everything an unattended run needs:
  * works from any folder (cron and Task Scheduler start in the wrong one): it switches to the project folder first
  * logs to logs/weekly.log (rotating) as well as the console, including everything the other modules print
  * refuses to start while another run is going (data/run.lock), so a slow run never overlaps the next
  * the Google API guard (guard.py): per-run and per-month request limits, and a stop when Google keeps failing
  * retries opening the sheet, and never lets an exception skip the log
  * refreshes the sheet's dropdowns/colors/filter views after new rows were added (sheet_extras.py)
  * writes logs/last_run.json and logs/run_history.jsonl so you can see at a glance whether the week's run happened

Exit codes (a scheduler marks anything other than 0 as a failed run):
  0 target reached (or already reached earlier this week)   1 finished but short of the target
  2 error (sheet, validation, Google failing, bad setup)    3 stopped by the API request limit
  4 another run is already in progress                      130 interrupted (Ctrl+C)
"""
import logging
import os
import sys
import time
import traceback

import config
import guard
import main as pipeline_main
import runtools

EXIT_OK, EXIT_SHORT, EXIT_ERROR, EXIT_QUOTA, EXIT_LOCKED, EXIT_INTERRUPTED = 0, 1, 2, 3, 4, 130

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


def _retry(fn, tries=3, delay=5, sleep_fn=time.sleep, log=print):
    """Call fn(); on failure wait and try again. SystemExit is retried too, because sheets.get_spreadsheet()
    turns every Google API error (including a passing 503) into sys.exit(message)."""
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except (Exception, SystemExit) as e:
            if attempt == tries:
                raise
            wait = delay * attempt
            log(f"  attempt {attempt}/{tries} failed ({type(e).__name__}: {str(e)[:200]}); retrying in {wait}s")
            sleep_fn(wait)


def _open_sheet(dry_run: bool, sleep_fn=time.sleep):
    sheet_id = config.GOOGLE_SHEET_ID
    if sheet_id and sheet_id != "your_sheet_id_here":
        from sheets import get_worksheet
        return _retry(get_worksheet, sleep_fn=sleep_fn)
    if not dry_run:
        raise SystemExit("GOOGLE_SHEET_ID is missing in .env (see Chunk 2 in the README).")
    print("No GOOGLE_SHEET_ID set: dry run without the sheet, so nothing is skipped as 'already in the sheet'.")
    return None


def _refresh_extras(ws):
    from sheet_extras import refresh_sheet_extras
    return refresh_sheet_extras(ws)


def _csv(value):
    return [v.strip() for v in value.split(",")] if value else None


def _execute(args, open_sheet, pipeline, refresh_extras, logger) -> dict:
    """Do the actual work. Returns the fields for the status file (status, exit_code, message, counts)."""
    countries = pipeline_main.resolve_countries(args.countries)
    ws = open_sheet(args.dry_run)
    result = pipeline(ws, args.target, countries, cities=_csv(args.cities), types=_csv(args.types),
                      dry_run=args.dry_run)

    out = {"week": result.get("week"), "target": result.get("target"), "added_this_run": result.get("added", 0),
           "week_total": result.get("week_total"), "shortfall": result.get("shortfall", 0),
           "searches": result.get("queries", 0), "stop_reason": result.get("stop_reason")}
    added, total, target = out["added_this_run"], out["week_total"], out["target"]

    if result.get("interrupted"):
        out.update(status="interrupted", exit_code=EXIT_INTERRUPTED, message=f"Interrupted after adding {added} leads.")
    elif result.get("stop_kind") == "quota":
        out.update(status="quota", exit_code=EXIT_QUOTA,
                   message=f"Stopped by the API request limit after adding {added} leads: {result['stop_reason']}")
    elif result.get("stop_kind"):
        out.update(status="error", exit_code=EXIT_ERROR,
                   message=f"Stopped because Google kept failing, {added} leads added first: {result['stop_reason']}")
    elif out["shortfall"]:
        out.update(status="short", exit_code=EXIT_SHORT,
                   message=f"Added {added} leads, week total {total}/{target}: short by {out['shortfall']}.")
    elif added == 0:
        out.update(status="ok", exit_code=EXIT_OK, message=f"Week already complete ({total}/{target}). Nothing to do.")
    else:
        out.update(status="ok", exit_code=EXIT_OK, message=f"Added {added} leads, week total {total}/{target}.")

    # New rows can run past the range the dropdowns cover, so refresh the sheet extras. Never fatal: the leads are safe.
    if ws is not None and not args.dry_run and added > 0:
        try:
            refresh_extras(ws)
            logger.info("Sheet extras refreshed (dropdowns, Priority colors, filter views).")
        except Exception:
            logger.warning("Could not refresh the sheet extras. The leads are saved; run `python setup_sheet.py` "
                           "later to refresh them.\n%s", traceback.format_exc())
    return out


def run(argv=None, *, open_sheet=None, pipeline=None, refresh_extras=None, sleep_fn=time.sleep) -> int:
    """The whole unattended run. The keyword arguments let the offline test swap in fakes."""
    args = pipeline_main.build_parser().parse_args(argv)
    logger = runtools.setup_logging(config.LOG_DIR, config.LOG_FILE, config.LOG_MAX_BYTES, config.LOG_BACKUP_COUNT)
    open_sheet = open_sheet or (lambda dry: _open_sheet(dry, sleep_fn))
    pipeline = pipeline or pipeline_main.run_pipeline
    refresh_extras = refresh_extras or _refresh_extras

    status = {"status": "error", "exit_code": EXIT_ERROR, "message": "", "started_at": runtools.now_iso(),
              "args": list(argv) if argv is not None else sys.argv[1:], "dry_run": args.dry_run}
    started = time.monotonic()
    lock = runtools.RunLock(config.RUN_LOCK_FILE, config.RUN_LOCK_STALE_HOURS)
    guard.set_guard(None)        # fresh per-run counters; limits are read from config.py

    with runtools.capture_output(logger):
        try:
            logger.info("=" * 60)
            logger.info("Weekly lead run starting. Arguments: %s", " ".join(status["args"]) or "(none)")
            if not lock.acquire(log=logger.info):
                msg = (f"Another run is already in progress ({lock.holder()}). Not starting a second one. "
                       f"If that run crashed, delete {config.RUN_LOCK_FILE} (locks older than "
                       f"{config.RUN_LOCK_STALE_HOURS} hours are ignored automatically).")
                logger.warning(msg)
                status.update(status="locked", exit_code=EXIT_LOCKED, message=msg)
            else:
                status.update(_execute(args, open_sheet, pipeline, refresh_extras, logger))
        except KeyboardInterrupt:
            logger.warning("Interrupted.")
            status.update(status="interrupted", exit_code=EXIT_INTERRUPTED, message="Interrupted.")
        except pipeline_main.PipelineError as e:
            logger.error("STOPPED: %s", e)
            status.update(status="error", exit_code=EXIT_ERROR, message=str(e))
        except SystemExit as e:      # modules report bad setup with sys.exit("message")
            msg = e.code if isinstance(e.code, str) else f"Exited with code {e.code}"
            logger.error("STOPPED: %s", msg)
            status.update(status="error", exit_code=EXIT_ERROR, message=msg)
        except Exception as e:
            logger.error("Unexpected error:\n%s", traceback.format_exc())
            status.update(status="error", exit_code=EXIT_ERROR, message=f"{type(e).__name__}: {e}")
        finally:
            lock.release()
            status["finished_at"] = runtools.now_iso()
            status["duration_seconds"] = round(time.monotonic() - started)
            try:
                status["api_usage"] = guard.get_guard().usage()
                logger.info(guard.get_guard().usage_line())
            except Exception:
                pass
            try:
                runtools.write_status(config.RUN_STATUS_FILE, status)
                if status["status"] != "locked":     # a skipped duplicate is not a run
                    runtools.append_history(config.RUN_HISTORY_FILE, status)
            except OSError as e:
                logger.warning("Could not write the status files: %s", e)
            # severity makes problems easy to find: grep -E "WARNING|ERROR" logs/weekly.log
            level = {"ok": logging.INFO}.get(status["status"], logging.ERROR if status["status"] == "error"
                                             else logging.WARNING)
            logger.log(level, "Run finished: %s (exit code %s). %s", status["status"].upper(), status["exit_code"],
                       status["message"])
    return status["exit_code"]


def main(argv=None) -> int:
    os.chdir(PROJECT_DIR)        # data/, logs/, credentials.json are all relative to the project folder
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
