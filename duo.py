#!/usr/bin/env python3
"""Track which apps have added iPhone Duo support, using the iTunes Lookup API."""
import argparse
import csv
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / "watchlist.json"
META = HERE / "meta.json"  # global poll time, kept out of watchlist.json so daily diffs stay small
SITE = HERE / "site"
LOOKUP = "https://itunes.apple.com/lookup"
DUO_RE = re.compile(r"\biPhone Duo\b", re.I)
BATCH = 100
DELAY = 4.0  # seconds between API requests; the sync is slow on purpose
CHART_URL = "https://itunes.apple.com/{cc}/rss/{chart}/limit=100/{genre}json"
CHARTS = ["topfreeapplications", "toppaidapplications", "topgrossingapplications",
          "topfreeipadapplications", "toppaidipadapplications", "topgrossingipadapplications"]
# Non-game genres, then Games and its subgenres. Skips Catalogs, Magazines and Stickers.
GENRES = [6018, 6000, 6026, 6017, 6016, 6015, 6023, 6014, 6027, 6013, 6012, 6020, 6011, 6010,
          6009, 6008, 6007, 6006, 6024, 6005, 6004, 6003, 6002, 6001] + list(range(7001, 7020))


def load():
    db = json.loads(DB.read_text()) if DB.exists() else {}
    for app in db.values():
        app.setdefault("tag", "mine")
    return db


def save(db):
    DB.write_text(json.dumps(db, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch_json(url, retries=5):
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            code = getattr(e, "code", None)
            if code is not None and code not in (403, 429) and code < 500:
                return None
            time.sleep(DELAY * 2 ** (attempt + 1))
    return None


def lookup(bundle_ids, country="us"):
    found = {}
    for i in range(0, len(bundle_ids), BATCH):
        if i:
            time.sleep(DELAY)
        chunk = bundle_ids[i : i + BATCH]
        q = urllib.parse.urlencode({"bundleId": ",".join(chunk), "country": country, "limit": 200})
        data = fetch_json(f"{LOOKUP}?{q}")
        if data is None:
            print(f"warning: lookup failed for batch starting at {i}", file=sys.stderr)
            continue
        for res in data["results"]:
            found[res["bundleId"]] = res
    return found


def snippet(notes):
    m = DUO_RE.search(notes)
    if not m:
        return None
    start = max(0, m.start() - 60)
    return notes[start : m.end() + 60].replace("\n", " ").strip()


def add_apps(db, apps, tag="mine"):
    for bid, name in apps:
        db.setdefault(bid, {"name": name, "status": "unknown", "source": None, "history": [], "tag": tag})
        if tag == "mine":
            db[bid]["tag"] = "mine"  # an app you own outranks being in the survey


def cmd_seed(args):
    db = load()
    with open(args.file, newline="") as f:
        rows = list(csv.reader(f, skipinitialspace=True))[1:]
    add_apps(db, [(r[0], r[2]) for r in rows if len(r) >= 3])
    save(db)
    print(f"{len(db)} apps in watchlist")


def cmd_add(args):
    db = load()
    for bid in args.bundle_ids:
        add_apps(db, [(bid, bid)])
    save(db)


def cmd_remove(args):
    db = load()
    for bid in args.bundle_ids:
        db.pop(bid, None)
    save(db)


def cmd_set(args):
    db = load()
    if args.bundle_id not in db:
        sys.exit("not in watchlist")
    app = db[args.bundle_id]
    if args.status == "auto":
        app["status"], app["source"] = "unknown", None
        # Re-derive from last known notes on the next check.
        app["history"] = []
    else:
        app["status"], app["source"] = args.status, "manual"
    save(db)


def cmd_survey(args):
    db = load()
    before = len(db)
    chart_ids = [(c, g) for c in CHARTS for g in [None] + GENRES]
    for n, (chart, genre) in enumerate(chart_ids):
        if n:
            time.sleep(1.0)
        url = CHART_URL.format(cc=args.country, chart=chart, genre=f"genre={genre}/" if genre else "")
        data = fetch_json(url)
        entries = (data or {}).get("feed", {}).get("entry", [])
        if isinstance(entries, dict):
            entries = [entries]
        for rank, e in enumerate(entries, 1):
            bid = e["id"]["attributes"].get("im:bundleId")
            if not bid:
                continue
            add_apps(db, [(bid, e["im:name"]["label"])], tag="survey")
            app = db[bid]
            app["category"] = e.get("category", {}).get("attributes", {}).get("label", app.get("category"))
            app["best_rank"] = min(rank, app.get("best_rank", rank))
        print(f"{chart} {genre or 'overall'}: {len(entries)} entries, {len(db)} apps total", file=sys.stderr)
        if n % 10 == 0:
            save(db)
    save(db)
    print(f"added {len(db) - before} survey apps; {len(db)} in watchlist")


def cmd_export(args):
    """Write the compact rows the static site reads."""
    db = load()
    rows = []
    polled = json.loads(META.read_text())["last_checked"][:10] if META.exists() else None
    for bid, a in db.items():
        if a.get("missing") or not a.get("checked"):
            continue
        hits = [h for h in a["history"] if h["mentions_duo"]]
        first = min(hits, key=lambda h: h["released"]) if hits else None
        last = a["history"][-1]["version"] if a["history"] else ""
        rows.append({
            "name": a["name"], "bundle_id": bid, "track_id": a.get("track_id"),
            "owner": "mine" if a["tag"] == "mine" else "survey",
            "category": a.get("category") or "Uncategorized",
            "status": a["status"], "source": a["source"] or "",
            "first_seen": first["released"][:10] if first else "",
            "version": first["version"] if first else last,
            "evidence": (first["evidence"] or "")[:160] if first else "",
            "best_rank": a.get("best_rank"), "checked": polled or a["checked"][:10],
        })
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, separators=(",", ":"), ensure_ascii=False))
    print(f"exported {len(rows)} apps to {args.out}")


def cmd_star(args):
    db = load()
    for bid in args.bundle_ids:
        db[bid]["starred"] = not args.off
    save(db)


def notify(text):
    subprocess.run(
        ["osascript", "-e", f'display notification {json.dumps(text)} with title "iPhone Duo"'],
        check=False,
    )


def cmd_check(args):
    db = load()
    found = lookup(list(db), args.country)
    changes = []
    for bid, app in db.items():
        res = found.get(bid)
        if not res:
            app["missing"] = True
            continue
        app.pop("missing", None)
        app["name"] = res["trackName"]
        app["track_id"] = res["trackId"]
        app.setdefault("checked", now())  # "has been checked at least once"; see META for the poll time
        version, notes = res["version"], res.get("releaseNotes", "")
        seen = {h["version"] for h in app["history"]}
        if version not in seen:
            app["history"].append(
                {
                    "version": version,
                    "released": res["currentVersionReleaseDate"],
                    "mentions_duo": bool(DUO_RE.search(notes)),
                    "evidence": snippet(notes),
                }
            )
            if app["history"][-1]["mentions_duo"] and app["source"] != "manual":
                if app["status"] != "supported" and (app["tag"] == "mine" or app.get("starred")):
                    changes.append((app["name"], f'{app["name"]} {version}: {app["history"][-1]["evidence"]}'))
                app["status"], app["source"] = "supported", "release_notes"
    save(db)
    META.write_text(json.dumps({"last_checked": now()}) + "\n")
    for _, line in changes:
        print("NEW DUO SUPPORT:", line)
    if changes and args.notify:
        notify(f"{len(changes)} app(s) added iPhone Duo support: " + ", ".join(name for name, _ in changes))
    if not changes:
        print("no new Duo support detected")


def cmd_report(args):
    db = load()
    mine = {b: a for b, a in db.items() if a["tag"] == "mine"}
    groups = {"supported": [], "unsupported": [], "unknown": []}
    for bid, app in mine.items():
        groups[app["status"]].append((app["name"], bid, app))
    for status, items in groups.items():
        print(f"\n{status.upper()} ({len(items)})")
        for name, bid, app in sorted(items, key=lambda x: x[0].lower()):
            extra = ""
            if app["source"] == "release_notes":
                extra = " — " + next(h["evidence"] for h in reversed(app["history"]) if h["evidence"])
            elif app["source"] == "manual":
                extra = " (manual)"
            if app.get("missing"):
                extra += " [not found in store]"
            print(f"  {name}{extra}")

    survey = {b: a for b, a in db.items() if a["tag"] == "survey" and not a.get("missing") and a.get("checked")}
    if not survey:
        return

    def pct(apps):
        n = sum(a["status"] == "supported" for a in apps)
        return f"{n}/{len(apps)} ({100 * n / len(apps):.0f}%)" if apps else "-"

    print(f"\nSURVEY: {pct(list(survey.values()))} mention iPhone Duo (lower bound)")
    print("  by best category rank:")
    for cap in (10, 25, 50, 100):
        print(f"    top {cap:<3} {pct([a for a in survey.values() if a.get('best_rank', 999) <= cap])}")
    cats = {}
    for a in survey.values():
        cats.setdefault(a.get("category", "?"), []).append(a)
    print("  by category:")
    for c, apps in sorted(cats.items(), key=lambda kv: -len(kv[1])):
        print(f"    {c:<24} {pct(apps)}")
    if args.survey:
        print("\nSURVEY APPS WITH DUO SUPPORT")
        for a in sorted((a for a in survey.values() if a["status"] == "supported"), key=lambda a: a["name"].lower()):
            print(f"  {a['name']} ({a.get('category', '?')})")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed", help="import ideviceinstaller output")
    s.add_argument("file")
    s.set_defaults(fn=cmd_seed)
    s = sub.add_parser("add")
    s.add_argument("bundle_ids", nargs="+")
    s.set_defaults(fn=cmd_add)
    s = sub.add_parser("remove")
    s.add_argument("bundle_ids", nargs="+")
    s.set_defaults(fn=cmd_remove)
    s = sub.add_parser("set", help="manual override: supported | unsupported | auto")
    s.add_argument("bundle_id")
    s.add_argument("status", choices=["supported", "unsupported", "auto"])
    s.set_defaults(fn=cmd_set)
    s = sub.add_parser("check", help="poll the App Store and update statuses")
    s.add_argument("--country", default="us")
    s.add_argument("--notify", action="store_true", help="macOS notification on new support")
    s.set_defaults(fn=cmd_check)
    s = sub.add_parser("survey", help="add top-chart apps to the watchlist as survey apps")
    s.add_argument("--country", default="us")
    s.set_defaults(fn=cmd_survey)
    s = sub.add_parser("export", help="write dashboard rows as JSON")
    s.add_argument("--out", default=str(SITE / "apps.json"))
    s.set_defaults(fn=cmd_export)
    s = sub.add_parser("star", help="notify about a survey app too")
    s.add_argument("bundle_ids", nargs="+")
    s.add_argument("--off", action="store_true")
    s.set_defaults(fn=cmd_star)
    s = sub.add_parser("report")
    s.add_argument("--survey", action="store_true", help="also list survey apps that support Duo")
    s.set_defaults(fn=cmd_report)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
