#!/usr/bin/env python3
"""
Update airports.json (coords) and snowtam_status.json (SNOWTAM-like NOTAM status)
for a static GitHub Pages dashboard.

Data sources:
- OurAirports airports.csv (for lat/lon)
- Notamify API v2 active NOTAM endpoint (for NOTAMs + interpretation)

Security:
- API key MUST be supplied via env var NOTAMIFY_API_KEY (e.g., GitHub Actions Secret).
- Never embed API keys client-side.
"""
from __future__ import annotations

import os
import sys
import json
import time
import csv
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import requests

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(ROOT, "data")
AIRPORTS_TXT = os.path.join(ROOT, "airports.txt")
AIRPORTS_JSON = os.path.join(DATA_DIR, "airports.json")
STATUS_JSON = os.path.join(DATA_DIR, "snowtam_status.json")

OURAIRPORTS_URL = "https://ourairports.com/data/airports.csv"
NOTAMIFY_URL = "https://api.notamify.com/api/v2/notams"

# Notamify constraints / operational knobs
BATCH_SIZE = int(os.getenv("NOTAMIFY_BATCH_SIZE", "5"))  # docs: max 5 ICAO per call
PER_PAGE = int(os.getenv("NOTAMIFY_PER_PAGE", "30"))     # docs: max 30
MAX_PAGES = int(os.getenv("NOTAMIFY_MAX_PAGES", "1"))    # keep cost down; increase if needed
REQUEST_TIMEOUT = int(os.getenv("HTTP_TIMEOUT_SECS", "25"))
SLEEP_BETWEEN_CALLS = float(os.getenv("SLEEP_BETWEEN_CALLS_SECS", "0.4"))

# How long should a "changed" marker blink in UI (front-end uses the boolean; this is a hint for ops)
BLINK_HINT_MINUTES = int(os.getenv("BLINK_HINT_MINUTES", "90"))

SNOW_HINT_RE = re.compile(
    r"\b(SNOWTAM|RWYCC|RCR|BRAKING|SLUSH|ICE|SNOW|COMPACTED\s+SNOW|WET\s+SNOW|DRY\s+SNOW|MU|CONTAMIN)\b",
    re.IGNORECASE,
)
RUNWAY_CLOSED_RE = re.compile(r"\b(RWY|RUNWAY).{0,24}\b(CLSD|CLOSED)\b|\bAERODROME\s+CLSD\b", re.IGNORECASE)
BRAKING_RE = re.compile(r"\bBRAKING\s+(ACTION\s+)?(GOOD|MEDIUM|POOR)\b", re.IGNORECASE)

# RWYCC patterns: "RWYCC 3/3/2" or "RWYCC: 2" or "RWYCC 5 5 4"
RWYCC_RE = re.compile(r"\bRWYCC\b[^0-9]{0,6}([0-9](?:\s*[/\s]\s*[0-9]){0,5})", re.IGNORECASE)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def read_airport_list() -> List[str]:
    if not os.path.exists(AIRPORTS_TXT):
        raise FileNotFoundError(f"Missing {AIRPORTS_TXT}")
    codes: List[str] = []
    with open(AIRPORTS_TXT, "r", encoding="utf-8") as f:
        for line in f:
            c = line.strip().upper()
            if c and len(c) == 4 and c.isalnum():
                codes.append(c)
    # stable order, unique
    seen = set()
    out = []
    for c in codes:
        if c not in seen:
            out.append(c)
            seen.add(c)
    return out


def sha1_text(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()


def load_prev_status() -> Dict[str, dict]:
    if not os.path.exists(STATUS_JSON):
        return {}
    try:
        with open(STATUS_JSON, "r", encoding="utf-8") as f:
            prev = json.load(f)
        return prev.get("airports", {}) if isinstance(prev, dict) else {}
    except Exception:
        return {}


def ensure_airports_json(icao_list: List[str]) -> None:
    """
    Create data/airports.json if missing, using OurAirports airports.csv.
    If already present and contains all ICAOs, keep as-is.
    """
    os.makedirs(DATA_DIR, exist_ok=True)

    need = set(icao_list)
    if os.path.exists(AIRPORTS_JSON):
        try:
            with open(AIRPORTS_JSON, "r", encoding="utf-8") as f:
                cur = json.load(f)
            have = {a.get("icao") for a in cur.get("airports", []) if isinstance(a, dict)}
            if need.issubset(have):
                print(f"[airports] airports.json already covers {len(have)} ICAOs; skipping coords refresh")
                return
        except Exception:
            pass

    print("[airports] downloading OurAirports airports.csv ...")
    r = requests.get(OURAIRPORTS_URL, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "wizz-snowtam-watch/1.0"})
    r.raise_for_status()

    by_icao: Dict[str, dict] = {}
    # Parse CSV quickly
    decoded = r.content.decode("utf-8", errors="replace").splitlines()
    reader = csv.DictReader(decoded)
    for row in reader:
        ident = (row.get("ident") or "").strip().upper()
        if ident in need:
            try:
                lat = float(row.get("latitude_deg") or "")
                lon = float(row.get("longitude_deg") or "")
            except Exception:
                continue
            by_icao[ident] = {
                "icao": ident,
                "name": (row.get("name") or "").strip(),
                "lat": lat,
                "lon": lon,
                "country": (row.get("iso_country") or "").strip(),
                "municipality": (row.get("municipality") or "").strip(),
            }

    airports = []
    missing = []
    for icao in icao_list:
        if icao in by_icao:
            airports.append(by_icao[icao])
        else:
            missing.append(icao)

    out = {
        "generated_at_utc": utc_now().isoformat(),
        "source": "OurAirports airports.csv",
        "missing": missing,
        "airports": airports,
    }
    with open(AIRPORTS_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"[airports] wrote {len(airports)} airports; missing coords: {len(missing)}")


def notamify_headers(api_key: str) -> dict:
    # Docs show "Authentication: Bearer <token>" and also OpenAPI sample uses Authorization.
    bearer = f"Bearer {api_key}"
    return {
        "Accept": "application/json",
        "Authorization": bearer,
        "Authentication": bearer,
        "User-Agent": "wizz-snowtam-watch/1.0",
    }


def chunk(lst: List[str], n: int) -> List[List[str]]:
    return [lst[i:i+n] for i in range(0, len(lst), n)]


def extract_rwycc_values(text: str) -> List[int]:
    m = RWYCC_RE.search(text or "")
    if not m:
        return []
    raw = m.group(1)
    vals = []
    for part in re.split(r"[/\s]+", raw.strip()):
        if part.isdigit():
            vals.append(int(part))
    return [v for v in vals if 0 <= v <= 6]


def severity_from_text(text: str) -> Tuple[str, dict]:
    """
    Returns: (severity, evidence)
    Severity classes: green / yellow / orange / red
    """
    t = text or ""
    evidence = {"rwycc": None, "braking": None, "closed": False, "keywords": []}

    if RUNWAY_CLOSED_RE.search(t):
        evidence["closed"] = True
        return "red", evidence

    rwycc = extract_rwycc_values(t)
    if rwycc:
        evidence["rwycc"] = rwycc
        mn = min(rwycc)
        if mn <= 1:
            return "red", evidence
        if mn <= 3:
            return "orange", evidence
        # 4-6 still indicates contamination context but mild
        return "yellow", evidence

    bm = BRAKING_RE.search(t)
    if bm:
        braking = bm.group(2).upper()
        evidence["braking"] = braking
        if braking == "POOR":
            return "red", evidence
        if braking == "MEDIUM":
            return "orange", evidence
        if braking == "GOOD":
            return "yellow", evidence

    # Keyword-only fallback
    kws = [k for k in ["SNOWTAM", "RWYCC", "RCR", "BRAKING", "SLUSH", "ICE", "SNOW", "MU", "CONTAMIN"] if re.search(r"\b"+re.escape(k)+r"\b", t, re.IGNORECASE)]
    evidence["keywords"] = kws

    if re.search(r"\b(POOR|UNUSABLE|UNSAFE)\b", t, re.IGNORECASE):
        return "red", evidence
    if re.search(r"\b(MEDIUM|MODERATE)\b", t, re.IGNORECASE):
        return "orange", evidence
    # otherwise mild
    return "yellow", evidence


def is_snowtam_like(notam_text: str) -> bool:
    if not notam_text:
        return False
    if SNOW_HINT_RE.search(notam_text):
        # Require some runway/winter context to avoid false positives on generic "SNOW" in city names
        if re.search(r"\bRWY\b|\bRUNWAY\b|\bR(?:WY)?CC\b|\bBRAKING\b|\bCONTAMIN", notam_text, re.IGNORECASE):
            return True
        # If explicit SNOWTAM, accept
        if re.search(r"\bSNOWTAM\b", notam_text, re.IGNORECASE):
            return True
    return False


@dataclass
class SnowItem:
    raw: str
    excerpt: str
    description: str


def fetch_notams_for_batch(api_key: str, batch: List[str], starts_at: str, ends_at: str) -> List[dict]:
    """
    Fetch NOTAMs for a list of up to 5 locations. Returns raw notam objects.
    """
    headers = notamify_headers(api_key)
    out: List[dict] = []
    for page in range(1, MAX_PAGES + 1):
        params = [
            ("page", str(page)),
            ("per_page", str(PER_PAGE)),
            ("starts_at", starts_at),
            ("ends_at", ends_at),
        ]
        for icao in batch:
            params.append(("location", icao))

        r = requests.get(NOTAMIFY_URL, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        if r.status_code == 401:
            raise RuntimeError("Notamify returned 401 Unauthorized. Check NOTAMIFY_API_KEY secret/header.")
        r.raise_for_status()
        data = r.json()
        notams = data.get("notams") or []
        if not notams:
            break
        out.extend(notams)
        if len(notams) < PER_PAGE:
            break
        time.sleep(0.2)
    return out


def build_status(icao_list: List[str], api_key: str) -> dict:
    prev = load_prev_status()

    now = utc_now()
    # Notamify limitation: starts_at cannot be earlier than one day before current UTC
    starts = (now - timedelta(days=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    ends = (now + timedelta(days=7)).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    airports_out: Dict[str, dict] = {}

    batches = chunk(icao_list, BATCH_SIZE)
    total_batches = len(batches)
    print(f"[notamify] querying {len(icao_list)} airports in {total_batches} batches (batch_size={BATCH_SIZE})")

    for bi, batch in enumerate(batches, start=1):
        print(f"[notamify] batch {bi}/{total_batches}: {', '.join(batch)}")
        try:
            notams = fetch_notams_for_batch(api_key, batch, starts, ends)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            for icao in batch:
                airports_out[icao] = {
                    "loaded": True,
                    "has_snowtam": False,
                    "severity": "gray",
                    "changed": False,
                    "last_change_utc": prev.get(icao, {}).get("last_change_utc"),
                    "error": err,
                    "items": [],
                    "hash": prev.get(icao, {}).get("hash"),
                }
            # continue to next batch
            time.sleep(SLEEP_BETWEEN_CALLS)
            continue

        # Group NOTAMs by airport
        by_icao: Dict[str, List[dict]] = {icao: [] for icao in batch}
        for n in notams:
            code = (n.get("icao_code") or "").strip().upper()
            if code in by_icao:
                by_icao[code].append(n)

        for icao in batch:
            snow_items: List[SnowItem] = []
            worst_sev = "green"
            worst_evidence = None

            for n in by_icao.get(icao, []):
                raw = n.get("icao_message") or ""
                if not is_snowtam_like(raw):
                    continue

                interp = n.get("interpretation") or {}
                excerpt = interp.get("excerpt") or ""
                desc = interp.get("description") or ""

                sev, ev = severity_from_text(raw + "\n" + desc)
                if worst_sev == "green":
                    worst_sev = sev
                    worst_evidence = ev
                else:
                    order = {"green":0,"yellow":1,"orange":2,"red":3}
                    if order.get(sev,0) > order.get(worst_sev,0):
                        worst_sev = sev
                        worst_evidence = ev

                snow_items.append(SnowItem(raw=raw, excerpt=excerpt, description=desc))

            has_snow = len(snow_items) > 0
            if not has_snow:
                worst_sev = "green"

            # hash for change detection (raw + interpretation excerpt/description)
            blob = "\n\n---\n\n".join([it.raw + "\n" + it.excerpt + "\n" + it.description for it in snow_items])
            new_hash = sha1_text(blob) if has_snow else "NO_SNOWTAM"
            old_hash = (prev.get(icao) or {}).get("hash")

            changed = (old_hash is not None and old_hash != new_hash)
            last_change = (prev.get(icao) or {}).get("last_change_utc")
            if changed:
                last_change = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")

            airports_out[icao] = {
                "loaded": True,
                "has_snowtam": has_snow,
                "severity": worst_sev,
                "changed": changed,
                "last_change_utc": last_change,
                "error": None,
                "items": [it.__dict__ for it in snow_items],
                "evidence": worst_evidence,
                "hash": new_hash,
            }

        time.sleep(SLEEP_BETWEEN_CALLS)

    return {
        "generated_at_utc": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source": "Notamify /api/v2/notams (active)",
        "window": {"starts_at": starts, "ends_at": ends},
        "batch_size": BATCH_SIZE,
        "per_page": PER_PAGE,
        "max_pages": MAX_PAGES,
        "blink_hint_minutes": BLINK_HINT_MINUTES,
        "airports": airports_out,
    }


def main() -> int:
    api_key = os.getenv("NOTAMIFY_API_KEY", "").strip()
    if not api_key:
        print("ERROR: missing NOTAMIFY_API_KEY environment variable.", file=sys.stderr)
        return 2

    icao_list = read_airport_list()
    print(f"[init] loaded {len(icao_list)} ICAO codes from airports.txt")

    ensure_airports_json(icao_list)

    status = build_status(icao_list, api_key)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATUS_JSON, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2, ensure_ascii=False)
    print(f"[done] wrote {STATUS_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
