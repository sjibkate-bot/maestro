"""Daily external risk feeds (free, public sources).

Feeds -> Bronze (response kept with its hash) -> governed `risk_signal` records
-> the supply risk rating ("geopolitical & hazard" likelihood factor).

Privacy: no supplier name is sent to any external service. Sanctions lists are
downloaded whole and matched locally; country feeds are queried by country name
only (countries come from the supplier master).

  ofac_sdn       US Treasury OFAC SDN list               daily    sanctions (local match)
  uk_sanctions   UK Sanctions List (FCDO)                daily    sanctions (local match)
  gdacs          GDACS disaster alerts (UN / EU JRC)     daily    hazard
  usgs_quakes    USGS earthquakes M5+, past 7 days       daily    hazard
  wb_stability   World Bank WGI political stability      yearly   stability
  gdelt_unrest   GDELT news volume on unrest per country daily    unrest
"""
import csv
import difflib
import hashlib
import io
import json
import logging
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from statistics import median
from typing import Callable, Optional
from urllib.parse import quote

import httpx

from .db import get_db
from .models import now

log = logging.getLogger(__name__)
USER_AGENT = "maestro-sc-risk-feeds/1.0"
HAZARD_WINDOW_DAYS = 14
HAZARD_MIN_SCORE = 60      # hazard signals below this are shown but do not change ratings
GDELT_DEADLINE_S = 300     # stop querying further countries after this many seconds

# ---------------------------------------------------------------- countries

_COMMON = {"TWN": "Taiwan", "KOR": "South Korea", "PRK": "North Korea", "RUS": "Russia", "IRN": "Iran",
           "VNM": "Vietnam", "USA": "United States", "GBR": "United Kingdom", "SYR": "Syria", "LAO": "Laos",
           "BOL": "Bolivia", "VEN": "Venezuela", "TZA": "Tanzania", "MDA": "Moldova", "CZE": "Czech Republic"}
_US_STATES = {"alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware", "florida",
              "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
              "maryland", "massachusetts", "michigan", "minnesota", "mississippi", "missouri", "montana", "nebraska",
              "nevada", "new hampshire", "new jersey", "new mexico", "new york", "north carolina", "north dakota", "ohio",
              "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota", "tennessee", "texas",
              "utah", "vermont", "virginia", "washington", "west virginia", "wisconsin", "wyoming", "puerto rico", "ca", "ak", "hi"}


@lru_cache(maxsize=2048)
def resolve_country(name: Optional[str]) -> Optional[str]:
    """Free-text country -> ISO 3166 alpha-3, or None."""
    if not name:
        return None
    import pycountry
    s = str(name).strip()
    if s.lower() in _US_STATES or s.upper() in ("US", "U.S.", "U.S.A."):
        return "USA"
    if s.upper() in ("UK", "U.K.", "GREAT BRITAIN", "ENGLAND", "SCOTLAND", "WALES"):
        return "GBR"
    for attr in ("alpha_3", "alpha_2"):
        try:
            c = pycountry.countries.get(**{attr: s.upper()})
            if c:
                return c.alpha_3
        except (KeyError, LookupError):
            pass
    try:
        return pycountry.countries.search_fuzzy(s)[0].alpha_3
    except LookupError:
        return None


def country_name(iso3: str) -> str:
    if iso3 in _COMMON:
        return _COMMON[iso3]
    import pycountry
    c = pycountry.countries.get(alpha_3=iso3)
    return getattr(c, "common_name", None) or (c.name if c else iso3)


# ---------------------------------------------------------------- name matching (sanctions)

_LEGAL = {"ltd", "limited", "inc", "incorporated", "corp", "corporation", "co", "company", "llc", "gmbh", "ag", "sa",
          "plc", "bv", "nv", "srl", "spa", "oy", "ab", "as", "kg", "jsc", "ooo", "pte", "pty", "sdn", "bhd", "the", "llp",
          "lp", "sas", "sarl", "kk", "zao", "oao", "pjsc", "group", "holding", "holdings"}


def norm_name(s: str) -> str:
    toks = re.sub(r"[^a-z0-9 ]", " ", str(s).lower().replace(".", "")).split()
    return " ".join(t for t in toks if t not in _LEGAL)


def match_names(suppliers: list[str], listed: list[dict]) -> list[dict]:
    """Exact match on normalised names, plus close spellings (ratio >= 0.93) for longer names."""
    index = {}
    for e in listed:
        n = norm_name(e["name"])
        if n:
            index.setdefault(n, e)
    keys = list(index)
    hits = []
    for sup in suppliers:
        n = norm_name(sup)
        if not n:
            continue
        if n in index:
            hits.append({"supplier": sup, "entry": index[n], "match": "exact"})
            continue
        if len(n) >= 8:
            close = difflib.get_close_matches(n, keys, n=1, cutoff=0.93)
            if close:
                hits.append({"supplier": sup, "entry": index[close[0]], "match": "close"})
    return hits


# ---------------------------------------------------------------- parsers (pure: text in, rows out)

def parse_ofac(text: str) -> list[dict]:
    """OFAC sdn.csv: no header; ent_num, SDN_Name, SDN_Type, Program, ..."""
    out = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 4 or not row[0].strip().isdigit():
            continue
        clean = lambda v: "" if v.strip() in ("-0-", "-0- ") else v.strip()
        out.append({"uid": row[0].strip(), "name": clean(row[1]), "type": clean(row[2]) or "entity",
                    "program": clean(row[3])})
    return out


def parse_uk(text: str) -> list[dict]:
    """UK Sanctions List CSV (FCDO): a 'Report Date' line, then a header with Unique ID, Name 1..Name 6,
    Regime Name, Designation Type. One row per name/address, so entries repeat."""
    lines = text.lstrip("\ufeff").splitlines()
    start = next((i for i, l in enumerate(lines[:20]) if "Name 6" in l and ("Unique ID" in l or "Group ID" in l)), None)
    if start is None:
        raise ValueError("UK list: header row not found (format changed?)")
    out, seen = [], set()
    for row in csv.DictReader(io.StringIO("\n".join(lines[start:]))):
        parts = [row.get(f"Name {i}", "") for i in (1, 2, 3, 4, 5)] + [row.get("Name 6", "")]
        name = " ".join(p.strip() for p in parts if p and p.strip())
        uid = (row.get("Unique ID") or row.get("Group ID") or "").strip()
        if not name or (uid, name) in seen:
            continue
        seen.add((uid, name))
        out.append({"uid": uid, "name": name, "type": (row.get("Designation Type") or row.get("Group Type") or "").strip(),
                    "program": (row.get("Regime Name") or row.get("Regime") or "").strip()})
    return out


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def parse_gdacs(text: str) -> list[dict]:
    """GDACS RSS: items with gdacs:eventtype, gdacs:alertlevel, gdacs:country, gdacs:iso3, gdacs:fromdate/todate, gdacs:eventid."""
    root = ET.fromstring(text)
    out = []
    for item in root.iter():
        if _local(item.tag) != "item":
            continue
        f = {}
        for ch in item:
            f[_local(ch.tag)] = (ch.text or "").strip()
        out.append({"id": f.get("eventtype", "") + "-" + f.get("eventid", f.get("guid", "")), "title": f.get("title"),
                    "url": f.get("link"), "level": f.get("alertlevel"), "type": f.get("eventtype"),
                    "countries": f.get("country", ""), "iso3": f.get("iso3", ""),
                    "from": f.get("fromdate") or f.get("pubDate"), "to": f.get("todate")})
    return out


def parse_usgs(text: str) -> list[dict]:
    data = json.loads(text)
    out = []
    for f in data.get("features", []):
        p = f.get("properties", {})
        place = p.get("place") or ""
        out.append({"id": f.get("id"), "mag": p.get("mag"), "place": place, "title": p.get("title"), "url": p.get("url"),
                    "time": datetime.fromtimestamp(p["time"] / 1000, tz=timezone.utc) if p.get("time") else None,
                    "country_text": place.rsplit(",", 1)[-1].strip() if "," in place else place.strip()})
    return out


def parse_worldbank(text: str) -> list[dict]:
    data = json.loads(text)
    if isinstance(data, list) and data and isinstance(data[0], dict) and data[0].get("message"):
        raise ValueError(f"World Bank API: {data[0]['message'][0].get('value')}")
    rows = data[1] if isinstance(data, list) and len(data) > 1 and data[1] else []
    out = []
    for r in rows:
        if r.get("value") is None:
            continue
        name = (r.get("country") or {}).get("value")
        iso = r.get("countryiso3code") or resolve_country(name)   # some rows carry no code
        if iso:
            out.append({"iso3": iso, "name": name, "year": r.get("date"), "value": r.get("value")})
    return out


def parse_gdelt_timeline(text: str) -> list[float]:
    data = json.loads(text) if text.strip() else {}
    series = (data.get("timeline") or [{}])[0].get("data", [])
    return [float(d.get("value") or 0) for d in series]


def parse_gdelt_articles(text: str) -> list[dict]:
    data = json.loads(text) if text.strip() else {}
    return [{"title": a.get("title"), "url": a.get("url"), "date": a.get("seendate"), "domain": a.get("domain")}
            for a in data.get("articles", [])]


# ---------------------------------------------------------------- severity rules

GDACS_TYPE_WEIGHT = {"DR": 0.6, "WF": 0.8}   # droughts and wildfires: slow-onset / region-wide, weighted down


def gdacs_severity(level: Optional[str], event_type: Optional[str] = None) -> float:
    base = {"red": 90.0, "orange": 60.0, "green": 20.0}.get((level or "").lower(), 20.0)
    return round(base * GDACS_TYPE_WEIGHT.get((event_type or "").upper(), 1.0), 1)


def quake_severity(mag: Optional[float]) -> float:
    m = mag or 0
    return 90.0 if m >= 7 else 70.0 if m >= 6 else 40.0 if m >= 5 else 20.0


def stability_severity(estimate: float) -> float:
    """WGI political stability estimate runs about -2.5 (worst) to +2.5 (best). Map +0.5 and above to 0
    and -2.5 to 100, so stable countries add nothing and fragile ones dominate."""
    return round(max(0.0, min(100.0, (0.5 - estimate) / 3 * 100)), 1)


def unrest_severity(daily: list[float]) -> tuple[float, float]:
    """Last 3 days' news volume against the median of the earlier days -> (severity, ratio)."""
    daily = daily[:-1]   # the last point is today's partial count
    if len(daily) < 7:
        return 0.0, 1.0
    base = median(daily[:-3]) or 0.0
    recent = sum(daily[-3:]) / 3
    ratio = recent / base if base else (2.0 if recent else 1.0)
    return round(max(0.0, min(100.0, (ratio - 1) * 60 + 20)) if ratio > 1 else 10.0, 1), round(ratio, 2)


def _parse_date(v) -> Optional[datetime]:
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    from email.utils import parsedate_to_datetime
    for f in (lambda s: parsedate_to_datetime(s), lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")),
              lambda s: datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)):
        try:
            return f(str(v))
        except (TypeError, ValueError, IndexError):
            continue
    return None


# ---------------------------------------------------------------- fetching

def fetch(url: str, timeout: float = 120.0, retries: int = 3, backoff: float = 5.0) -> str:
    """GET with retries and backoff (free services rate-limit and reset connections)."""
    last = None
    for attempt in range(retries):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as c:
                r = c.get(url)
                if r.status_code in (429, 502, 503, 504):
                    raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
                r.raise_for_status()
                return r.text
        except (httpx.TransportError, httpx.HTTPStatusError) as e:
            last = e
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code not in (429, 502, 503, 504):
                raise
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    raise last


def _supplier_names() -> list[str]:
    db = get_db()
    names = set(x for x in db.records.distinct("supplier", {"record_type": {"$in": ["supplier", "purchase_order", "product"]}}) if x)
    return sorted(names)


def supplier_countries() -> dict[str, str]:
    """ISO3 -> country name for every country in the supplier master."""
    out = {}
    for c in get_db().records.distinct("country", {"record_type": "supplier"}):
        iso = resolve_country(c)
        if iso:
            out[iso] = country_name(iso)
    return out


def _signal(source, sid, category, severity, title, url=None, country=None, supplier=None, event_date=None, extra=None):
    from .pipeline import build_record
    rec = build_record("risk_signal", f"feed:{source}", "feed", {
        "signal_id": sid, "source": source, "category": category, "risk_score": severity, "title": title, "url": url,
        "country": country, "country_name": country_name(country) if country else None, "supplier": supplier,
        "event_date": event_date}, extra or {})
    rec["attributes"]["feed"] = source
    return rec


# ---------------------------------------------------------------- feeds

def _sanctions(source: str, url: str, parser: Callable, label: str):
    def run():
        text = fetch(url)
        listed = [e for e in parser(text) if "individual" not in (e.get("type") or "").lower()]  # suppliers are organisations
        if not listed:
            raise ValueError(f"{label}: no entries parsed (format changed?)")
        sigs = []
        for h in match_names(_supplier_names(), listed):
            e = h["entry"]
            sigs.append(_signal(source, f"{e['uid']}:{norm_name(h['supplier'])}", "sanctions",
                                100.0 if h["match"] == "exact" else 80.0,
                                f"Possible {label} match: '{h['supplier']}' ~ '{e['name']}'" + (f" ({e['program']})" if e.get("program") else ""),
                                url, supplier=h["supplier"], event_date=now(),
                                extra={"match": h["match"], "listed_name": e["name"], "program": e.get("program"),
                                       "verify": "Name match only - confirm identity (address, registration) before acting"}))
        return text, sigs, {"entries": len(listed), "suppliers_checked": len(_supplier_names())}
    return run


def _gdacs():
    url = "https://www.gdacs.org/xml/rss.xml"
    text = fetch(url)
    cutoff = datetime.now(timezone.utc) - timedelta(days=HAZARD_WINDOW_DAYS)
    sigs = []
    for e in parse_gdacs(text):
        if (e["level"] or "").lower() not in ("orange", "red"):
            continue  # green alerts are minor (mostly small wildfires) and would drown the signal
        to = _parse_date(e["to"]) or _parse_date(e["from"])
        if to and to.astimezone(timezone.utc) < cutoff:
            continue
        isos = [x.strip() for x in re.split(r"[,;]", e["iso3"]) if x.strip()] or \
               [i for i in (resolve_country(c) for c in re.split(r"[,;]", e["countries"])) if i]
        for iso in isos or [None]:
            sigs.append(_signal("gdacs", f"{e['id']}:{iso}", "hazard", gdacs_severity(e["level"], e["type"]), e["title"] or e["id"],
                                e["url"], country=iso, event_date=to or _parse_date(e["from"]),   # still active -> latest date
                                extra={"alert_level": e["level"], "event_type": e["type"], "from": e["from"], "to": e["to"]}))
    return text, sigs, {"events": len(sigs)}


def _usgs():
    url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson"
    text = fetch(url)
    sigs = []
    for q in parse_usgs(text):
        if (q["mag"] or 0) < 5:
            continue
        iso = resolve_country(q["country_text"])
        if not iso:
            continue  # offshore / ocean regions
        sigs.append(_signal("usgs_quakes", q["id"], "hazard", quake_severity(q["mag"]), q["title"], q["url"],
                            country=iso, event_date=q["time"], extra={"magnitude": q["mag"], "place": q["place"]}))
    return text, sigs, {"quakes": len(sigs)}


def _worldbank():
    url = "https://api.worldbank.org/v2/country/all/indicator/GOV_WGI_PV.EST?source=3&format=json&mrv=1&per_page=500"
    text = fetch(url)
    import pycountry
    sigs = []
    for r in parse_worldbank(text):
        if not pycountry.countries.get(alpha_3=r["iso3"]):
            continue  # regional aggregates
        sigs.append(_signal("wb_stability", r["iso3"], "stability", stability_severity(r["value"]),
                            f"Political stability {r['value']:+.2f} (World Bank WGI {r['year']})",
                            f"https://data.worldbank.org/indicator/PV.EST?locations={r['iso3']}", country=r["iso3"],
                            event_date=_parse_date(f"{r['year']}-12-31"), extra={"estimate": r["value"], "year": r["year"]}))
    return text, sigs, {"countries": len(sigs)}


def _gdelt(pause: float = 10.0):
    countries = supplier_countries()
    if not countries:
        return "", [], {"note": "no supplier countries in the supplier master"}
    sigs, raw, failed = [], {}, []
    terms = '(protest OR strike OR riot OR unrest OR blockade OR "port closure" OR coup OR sanctions)'
    t0 = time.monotonic()
    for i, (iso, name) in enumerate(sorted(countries.items())):
        if time.monotonic() - t0 > GDELT_DEADLINE_S:
            failed.append(f"{name}: skipped (time limit)")
            continue
        q = quote(f'"{name}" {terms}')
        base = f"https://api.gdeltproject.org/api/v2/doc/doc?query={q}&format=json"
        try:
            if i:
                time.sleep(pause)   # GDELT asks clients to keep request rates low
            tl = fetch(base + "&mode=timelinevolraw&timespan=30d", backoff=30)
            time.sleep(pause)
            try:
                arts = parse_gdelt_articles(fetch(base + "&mode=artlist&maxrecords=5&timespan=3d&sort=datedesc", backoff=30))
            except Exception:
                arts = []   # headlines are evidence only; the volume series is what scores
        except Exception as e:
            failed.append(f"{name}: {e}")
            continue
        raw[iso] = {"timeline": tl[:20000], "articles": arts}
        sev, ratio = unrest_severity(parse_gdelt_timeline(tl))
        sigs.append(_signal("gdelt_unrest", iso, "unrest", sev,
                            f"Unrest news in {name}: {ratio:.1f}x usual volume over the last 3 days",
                            arts[0]["url"] if arts else None, country=iso, event_date=now(),
                            extra={"ratio": ratio, "headlines": arts}))
    if failed and not sigs:
        raise RuntimeError("GDELT unavailable for every country: " + "; ".join(failed[:3]))
    return json.dumps(raw), sigs, {"countries": len(countries), "failed": failed}


FEEDS = {
    "ofac_sdn": {"name": "US OFAC SDN sanctions list", "category": "sanctions", "cadence": "daily", "publisher": "US Treasury",
                 "sends": "nothing (list downloaded, matched locally)",
                 "run": _sanctions("ofac_sdn", "https://www.treasury.gov/ofac/downloads/sdn.csv", parse_ofac, "OFAC SDN")},
    "uk_sanctions": {"name": "UK Sanctions List", "category": "sanctions", "cadence": "daily", "publisher": "UK Foreign, Commonwealth & Development Office",
                     "sends": "nothing (list downloaded, matched locally)",
                     "run": _sanctions("uk_sanctions", "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.csv", parse_uk, "UK sanctions")},
    "gdacs": {"name": "GDACS disaster alerts", "category": "hazard", "cadence": "daily", "publisher": "UN OCHA / EU JRC",
              "sends": "nothing", "run": _gdacs},
    "usgs_quakes": {"name": "USGS earthquakes M5+ (7 days)", "category": "hazard", "cadence": "daily", "publisher": "US Geological Survey",
                    "sends": "nothing", "run": _usgs},
    "wb_stability": {"name": "World Bank political stability (WGI)", "category": "stability", "cadence": "daily check, yearly data",
                     "publisher": "World Bank", "sends": "nothing", "run": _worldbank},
    "gdelt_unrest": {"name": "GDELT unrest news by country", "category": "unrest", "cadence": "daily", "publisher": "GDELT Project",
                     "sends": "country names from the supplier master (never supplier names)", "run": _gdelt},
}


# ---------------------------------------------------------------- running

def feed_settings() -> dict:
    doc = get_db().feed_config.find_one({"_id": "feeds"}) or {}
    return {k: doc.get(k, {"enabled": True}) for k in FEEDS}


def set_enabled(feed_id: str, enabled: bool, user: str) -> dict:
    from . import governance
    if feed_id not in FEEDS:
        raise KeyError(feed_id)
    get_db().feed_config.update_one({"_id": "feeds"}, {"$set": {f"{feed_id}.enabled": bool(enabled)}}, upsert=True)
    governance.audit("feed_setting", user or "system", {"feed": feed_id, "enabled": bool(enabled)})
    return feeds()[[f["id"] for f in feeds()].index(feed_id)]


def run_feed(feed_id: str, user: str = "scheduler") -> dict:
    from . import governance
    from .pipeline import store
    if feed_id not in FEEDS:
        raise KeyError(feed_id)
    db = get_db()
    started = now()
    run = {"feed": feed_id, "started_at": started, "user": user}
    try:
        text, sigs, info = FEEDS[feed_id]["run"]()
        digest = hashlib.sha256(text.encode()).hexdigest()
        db.bronze_files.insert_one({"sha256": digest, "filename": f"{feed_id}-{started:%Y%m%dT%H%M%S}",
                                    "source_id": f"feed:{feed_id}", "size": len(text), "received_at": started,
                                    "content": text.encode() if len(text) <= 15 * 1024 * 1024 else None})
        res = store(sigs, {"feed": feed_id, "summary": info, "sha256": digest}, f"feed:{feed_id}", "feed",
                    {"record_type": "risk_signal", "feed": feed_id, "file": f"{feed_id}-{started:%Y%m%dT%H%M%S}"},
                    replace_keys=[{"record_type": "risk_signal", "source": feed_id}])
        failed = info.get("failed") or []
        run.update({"status": "partial" if failed else "ok", "signals": res["records"], "info": info,
                    "error": (f"{len(failed)} part(s) failed: " + "; ".join(failed[:3]))[:400] if failed else None})
    except Exception as e:  # network down, format changed, rate limited...
        log.warning("Feed %s failed: %s", feed_id, e)
        run.update({"status": "error", "error": f"{type(e).__name__}: {e}"[:400], "signals": 0})
    run["finished_at"] = now()
    db.feed_runs.insert_one(dict(run))
    governance.audit("feed_run", user, {k: run.get(k) for k in ("feed", "status", "signals", "error")})
    run.pop("_id", None)
    return run


_running = threading.Lock()


def run_all(user: str = "scheduler") -> list[dict]:
    if not _running.acquire(blocking=False):
        return [{"status": "busy", "note": "a refresh is already running"}]
    try:
        settings = feed_settings()
        return [run_feed(f, user) for f in FEEDS if settings[f]["enabled"]]
    finally:
        _running.release()


def run_in_background(user: str, feed_id: Optional[str] = None) -> dict:
    """Start a refresh without holding the caller's request open."""
    if _running.locked():
        return {"status": "busy", "note": "a refresh is already running"}
    if feed_id and feed_id not in FEEDS:
        raise KeyError(feed_id)

    def job():
        if feed_id:
            with _running:
                run_feed(feed_id, user)
        else:
            run_all(user)
    threading.Thread(target=job, name="risk-feeds-manual", daemon=True).start()
    return {"status": "started", "feed": feed_id or "all"}


def is_running() -> bool:
    return _running.locked()


def feeds() -> list[dict]:
    db = get_db()
    settings = feed_settings()
    out = []
    for fid, f in FEEDS.items():
        last = db.feed_runs.find_one({"feed": fid}, sort=[("started_at", -1)])
        last_ok = db.feed_runs.find_one({"feed": fid, "status": {"$in": ["ok", "partial"]}}, sort=[("started_at", -1)])
        out.append({"id": fid, **{k: f[k] for k in ("name", "category", "cadence", "publisher", "sends")},
                    "enabled": settings[fid]["enabled"],
                    "last_run": last.get("started_at") if last else None, "status": last.get("status") if last else "never run",
                    "error": last.get("error") if last else None,
                    "last_success": last_ok.get("started_at") if last_ok else None,
                    "signals": db.records.count_documents({"record_type": "risk_signal", "source": fid})})
    return out


# ---------------------------------------------------------------- daily scheduler

_thread: Optional[threading.Thread] = None


def start_scheduler() -> None:
    """Run all enabled feeds once a day at RISK_FEEDS_HOUR (UTC), and at start-up if the last run is over a day old."""
    global _thread
    if os.getenv("RISK_FEEDS_ENABLED", "true").lower() != "true" or (_thread and _thread.is_alive()):
        return
    hour = int(os.getenv("RISK_FEEDS_HOUR", "2"))

    def loop():
        time.sleep(30)
        last = get_db().feed_runs.find_one({}, sort=[("started_at", -1)])
        last_at = last["started_at"].replace(tzinfo=timezone.utc) if last and last["started_at"].tzinfo is None else (last or {}).get("started_at")
        if not last_at or datetime.now(timezone.utc) - last_at > timedelta(hours=24):
            run_all()
        while True:
            n = datetime.now(timezone.utc)
            nxt = n.replace(hour=hour, minute=0, second=0, microsecond=0)
            if nxt <= n:
                nxt += timedelta(days=1)
            time.sleep((nxt - n).total_seconds())
            run_all()

    _thread = threading.Thread(target=loop, name="risk-feeds", daemon=True)
    _thread.start()
