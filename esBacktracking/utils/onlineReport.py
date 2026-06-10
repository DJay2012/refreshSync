#!/usr/bin/env python3
"""
onlineReport.py

Read a CSV, look up documents in MongoDB `socialFeed`, and write a field
(default: 'feedData.language') into a new column (default: 'feed_language').

- Auto-detects an ID column (SOCIALFEEDID/socialFeedId/_id/id) OR a LINK column (LINK/link/feedInfo.link/url/URL).
  You can force either via --id-column or --link-column.
- Repairs mojibake (UTF-8 mis-decoded as Latin-1) in CSV column 'tagInfo.keyword'.

Examples (PowerShell):
  python .\onlineReport.py ".\OnlineTagging report.csv" `
    -o ".\OnlineTagging report_with_lang.csv" `
    --mongo-uri "mongodb://user:pass@host:27020/" `
    --db pnq `
    --collection socialFeed `
    --also-id

Environment fallbacks: MONGO_URI / MONGO_DB / MONGO_COLL
"""

import argparse
import json
import logging
import os
import re
from typing import Dict, Iterable, List, Optional, Tuple, Any

import pandas as pd
from pymongo import MongoClient

# ---------- Logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
log = logging.getLogger("onlineReport")

# ---------- Column detection ----------
ID_COLUMN_CANDIDATES = (
    "socialfeedid", "social_feed_id", "socialFeedId", "_id", "id"
)
LINK_COLUMN_CANDIDATES = (
    "link", "LINK", "feedInfo.link", "url", "URL"
)

def detect_column(df: pd.DataFrame, preferred: Optional[str], candidates: Iterable[str]) -> Optional[str]:
    """Case-insensitive column detection with preferred override."""
    cols_lower = {c.lower(): c for c in df.columns}
    if preferred:
        if preferred in df.columns:
            return preferred
        if preferred.lower() in cols_lower:
            return cols_lower[preferred.lower()]
    for c in candidates:
        if c in df.columns:
            return c
        if c.lower() in cols_lower:
            return cols_lower[c.lower()]
    return None

# ---------- Helpers ----------
def normalize_social_id(val) -> Optional[int]:
    """
    Parse socialFeedId-like values from CSV/Excel:
    - "18219521003", "18219521003.0", "1.8219521003E+10", " 18,219,521,003 "
    Returns int or None.
    """
    if val is None:
        return None
    s = str(val).strip()
    if s == "" or s.lower() in {"nan", "none", "null"}:
        return None
    s = re.sub(r"[,\s]", "", s)
    m = re.fullmatch(r"(-?\d+)(?:\.0+)?", s)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    try:
        if re.search(r"[eE]", s):
            return int(float(s))
    except Exception:
        pass
    s2 = re.sub(r"[^\d-]", "", s)
    if s2 in {"", "-"}:
        return None
    try:
        return int(s2)
    except Exception:
        return None

def normalize_link(val) -> Optional[str]:
    """Trim and return link as string; empty/NA -> None."""
    if val is None:
        return None
    s = str(val).strip()
    if s == "" or s.lower() in {"nan", "none", "null"}:
        return None
    return s

def extract_nested(doc: Dict[str, Any], dotted: str) -> Any:
    """Extract nested value like 'feedData.language' from a dict."""
    cur = doc
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur

# --- Unicode escape decoding for JSON-like cells ---
def decode_unicode_escapes(text: str) -> str:
    """
    Decode Unicode escape sequences.
    Example: "\\u09b8\\u09cb\\u09a8\\u09bf" -> "সনি" (as actual Unicode)
    """
    if not isinstance(text, str) or not text:
        return text
    try:
        if text.startswith("{") and text.endswith("}"):
            try:
                decoded_obj = json.loads(text)
                return json.dumps(decoded_obj, ensure_ascii=False)
            except (json.JSONDecodeError, ValueError):
                pass
        try:
            return text.encode().decode("unicode_escape")
        except (UnicodeDecodeError, UnicodeEncodeError):
            return text
    except Exception:
        return text

def process_unicode_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Decode JSON-like columns that may contain Unicode escapes."""
    unicode_columns: List[str] = []
    for col in df.columns:
        if any(k in col.lower() for k in ["source", "keyword", "tag", "detail", "json"]):
            unicode_columns.append(col)
    for col in df.columns:
        if col not in unicode_columns:
            sample = df[col].dropna().astype(str).head(10)
            if any(v.strip().startswith("{") and v.strip().endswith("}") for v in sample):
                unicode_columns.append(col)
    if unicode_columns:
        log.info(f"Processing Unicode escapes in columns: {unicode_columns}")
        for col in unicode_columns:
            df[col] = df[col].apply(decode_unicode_escapes)
    return df

# --- Mojibake repair (UTF-8 read as Latin-1) for tagInfo.keyword ---
MOJIBAKE_MARKERS = ("Ã", "Â", "â", "à", "å", "æ", "¤", "©", "ƒ", "‰", "™", "€")
INDIC_BLOCK = re.compile(r"[\u0900-\u0D7F]")  # Devanagari..Malayalam

def _looks_like_mojibake(s: str) -> bool:
    if not isinstance(s, str) or not s:
        return False
    # suspicious Latin-1 high chars AND not already Indic script
    return any(m in s for m in MOJIBAKE_MARKERS) and not INDIC_BLOCK.search(s)

def decode_mojibake_if_needed(s: str) -> str:
    """
    Fix common mojibake from UTF-8 interpreted as ISO-8859-1/Windows-1252.
    Example: "àª®à«àªà«" -> "મોટો"
    """
    if not isinstance(s, str) or not s:
        return s
    if not _looks_like_mojibake(s):
        return s
    try:
        fixed = s.encode("latin1", "ignore").decode("utf-8", "ignore")
        # Optional second pass if still looks broken
        if _looks_like_mojibake(fixed):
            fixed2 = fixed.encode("latin1", "ignore").decode("utf-8", "ignore")
            if not _looks_like_mojibake(fixed2):
                return fixed2
        return fixed
    except Exception:
        return s

def process_mojibake_column(df: pd.DataFrame, column_name: str = "tagInfo.keyword") -> pd.DataFrame:
    """Apply mojibake repair to the specified column (case-insensitive)."""
    cols_lower = {c.lower(): c for c in df.columns}
    key = column_name.lower()
    if key in cols_lower:
        real_col = cols_lower[key]
        log.info(f"Fixing mojibake in column: {real_col}")
        df[real_col] = df[real_col].apply(decode_mojibake_if_needed)
    else:
        log.info(f"Column '{column_name}' not found; skipping mojibake fix.")
    return df

# ---------- Mongo lookups ----------
def get_collection(uri: str, db: str, coll: str):
    client = MongoClient(
        uri,
        connectTimeoutMS=20000,
        serverSelectionTimeoutMS=20000,
        retryWrites=True,
    )
    return client, client[db][coll]

def fetch_by_ids(
    coll,
    ids: List[Optional[int]],
    field_path: str,
    batch_size: int = 1000,
) -> Tuple[Dict[int, Optional[Any]], Dict[int, Optional[int]]]:
    """
    Return:
      value_map: _id -> extracted field value
      id_map   : _id -> _id (identity map, useful for --also-id)
    """
    value_map: Dict[int, Optional[Any]] = {}
    id_map: Dict[int, Optional[int]] = {}

    unique_ids = [i for i in dict.fromkeys(ids) if i is not None]
    log.info(f"Querying by IDs: {len(unique_ids)} unique IDs in batches of {batch_size}...")
    if unique_ids:
        log.info(f"Sample IDs: {unique_ids[:5]}")

    projection = {"_id": 1, field_path: 1}

    for start in range(0, len(unique_ids), batch_size):
        chunk = unique_ids[start : start + batch_size]
        cursor = coll.find({"_id": {"$in": chunk}}, projection)
        found = 0
        hits = 0
        for doc in cursor:
            found += 1
            mongo_id = int(doc["_id"])
            val = extract_nested(doc, field_path)
            if val is not None:
                hits += 1
            value_map[mongo_id] = val
            id_map[mongo_id] = mongo_id
        log.info(f"Batch {start//batch_size+1}: found={found}, matched_with_value={hits}")
    return value_map, id_map

def fetch_by_links(
    coll,
    links: List[Optional[str]],
    field_path: str,
    batch_size: int = 500,
) -> Tuple[Dict[str, Optional[Any]], Dict[str, Optional[int]]]:
    """
    Return:
      value_by_link: link -> field value
      id_by_link   : link -> Mongo _id (int)
    """
    value_by_link: Dict[str, Optional[Any]] = {}
    id_by_link: Dict[str, Optional[int]] = {}

    unique_links = [s for s in dict.fromkeys(links) if s]
    log.info(f"Querying by LINKS: {len(unique_links)} unique links in batches of {batch_size}...")
    if unique_links:
        log.info(f"Sample LINKS: {unique_links[:5]}")

    projection = {"_id": 1, "feedInfo.link": 1, field_path: 1}

    for start in range(0, len(unique_links), batch_size):
        chunk = unique_links[start : start + batch_size]
        cursor = coll.find({"feedInfo.link": {"$in": chunk}}, projection)
        found = 0
        hits = 0
        for doc in cursor:
            found += 1
            link = extract_nested(doc, "feedInfo.link")
            val = extract_nested(doc, field_path)
            mongo_id = int(doc["_id"]) if "_id" in doc else None
            if link is None:
                continue
            if val is not None:
                hits += 1
            value_by_link[link] = val
            id_by_link[link] = mongo_id
        log.info(f"Batch {start//batch_size+1}: found={found}, matched_with_value={hits}")
    return value_by_link, id_by_link

# ---------- Main ----------
def main():
    parser = argparse.ArgumentParser(description="Add a field (default: feedData.language) from Mongo socialFeed to CSV.")
    parser.add_argument("input_csv", help="Path to input CSV")
    parser.add_argument("-o", "--output-csv", default=None, help="Output CSV (default: auto-suffix)")
    parser.add_argument("--inplace", action="store_true", help="Overwrite input CSV in place")

    # Column control
    parser.add_argument("--id-column", default=None, help="CSV column holding socialFeed IDs")
    parser.add_argument("--link-column", default=None, help="CSV column holding the LINK (feedInfo.link)")
    parser.add_argument("--column-name", default="feed_language", help="New column name for the fetched value")
    parser.add_argument("--also-id", action="store_true", help="Also write the Mongo _id we matched to (column: socialfeed_id_from_mongo)")

    # Mongo
    parser.add_argument("--mongo-uri", default=os.getenv("MONGO_URI", "mongodb://localhost:27017/"))
    parser.add_argument("--db", default=os.getenv("MONGO_DB", "pnq"))
    parser.add_argument("--collection", default=os.getenv("MONGO_COLL", "socialFeed"))

    # Field selection
    parser.add_argument("--field", default="feedData.language", help="Dotted path to fetch (e.g., feedData.language)")

    # Batch size
    parser.add_argument("--batch-size", type=int, default=1000)

    # Toggle mojibake repair for tagInfo.keyword
    try:
        from argparse import BooleanOptionalAction
        parser.add_argument(
            "--fix-mojibake",
            action=BooleanOptionalAction,
            default=True,
            help="Auto-fix mojibake in 'tagInfo.keyword' (default: enabled)."
        )
    except Exception:
        parser.add_argument(
            "--fix-mojibake",
            action="store_true",
            default=True,
            help="Auto-fix mojibake in 'tagInfo.keyword' (default: enabled)."
        )

    args = parser.parse_args()

    # Read CSV as strings to avoid Excel-mangled ints
    log.info(f"Reading CSV: {args.input_csv}")
    df = pd.read_csv(args.input_csv, dtype=str, keep_default_na=False, encoding="utf-8-sig")

    # Detect columns
    id_col = detect_column(df, args.id_column, ID_COLUMN_CANDIDATES)
    link_col = detect_column(df, args.link_column, LINK_COLUMN_CANDIDATES)

    mode = None
    if id_col:
        mode = "id"
        log.info(f"Using ID column: {id_col}")
    elif link_col:
        mode = "link"
        log.info(f"Using LINK column: {link_col}")
    else:
        raise SystemExit(
            "Could not detect a usable ID or LINK column.\n"
            f"Tried ID candidates: {', '.join(ID_COLUMN_CANDIDATES)}\n"
            f"Tried LINK candidates: {', '.join(LINK_COLUMN_CANDIDATES)}\n"
            "Use --id-column or --link-column to specify explicitly."
        )

    # Connect Mongo
    client, coll = get_collection(args.mongo_uri, args.db, args.collection)

    try:
        if mode == "id":
            raw_ids = [normalize_social_id(v) for v in df[id_col].tolist()]
            if any(v is None for v in raw_ids):
                log.warning("Some rows have missing/unparseable IDs; those will be blank.")
            val_map, id_map = fetch_by_ids(
                coll, raw_ids, field_path=args.field, batch_size=args.batch_size
            )
            df[args.column_name] = [val_map.get(i) if i is not None else None for i in raw_ids]
            if args.also_id:
                df["socialfeed_id_from_mongo"] = [id_map.get(i) if i is not None else None for i in raw_ids]

        else:  # mode == "link"
            raw_links = [normalize_link(v) for v in df[link_col].tolist()]
            if any(v is None for v in raw_links):
                log.warning("Some rows have missing/blank LINKS; those will be blank.")
            val_by_link, id_by_link = fetch_by_links(
                coll, raw_links, field_path=args.field, batch_size=min(args.batch_size, 500)
            )
            df[args.column_name] = [val_by_link.get(s) if s else None for s in raw_links]
            if args.also_id:
                df["socialfeed_id_from_mongo"] = [id_by_link.get(s) if s else None for s in raw_links]

    finally:
        client.close()

    # Optional: decode any JSON-ish columns with Unicode escapes
    df = process_unicode_columns(df)

    # Repair mojibake specifically for tagInfo.keyword
    if args.fix_mojibake:
        df = process_mojibake_column(df, column_name="tagInfo.keyword")

    # Write output
    if args.inplace and not args.output_csv:
        out_path = args.input_csv
    else:
        if args.output_csv:
            out_path = args.output_csv
        else:
            base, ext = os.path.splitext(args.input_csv)
            out_path = f"{base}_with_lang{ext or '.csv'}"

    df.to_csv(out_path, index=False, encoding="utf-8-sig")

    # Stats
    total = len(df)
    hits = df[args.column_name].notna().sum()
    log.info(f"Done. {args.field} found for {hits} / {total} rows.")
    log.info(f"Wrote: {out_path}")

if __name__ == "__main__":
    main()
