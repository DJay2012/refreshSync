#!/usr/bin/env python3
"""
add_lang_from_mongo.py

Read a CSV, fetch articleInfo.language from MongoDB for each article ID,
and write it into a new column (default: 'article_language').

Usage (example):
  python add_lang_from_mongo.py "/mnt/data/PrintTagging report.csv" \
      -o "/mnt/data/PrintTagging report_with_lang.csv" \
      --mongo-uri "mongodb://localhost:27017/" --db pnq --collection article \
      --id-column ARTICLEID

You can also set env vars MONGO_URI / MONGO_DB / MONGO_COLL.
"""

import argparse
import json
import logging
import os
import re
from typing import Dict, Iterable, List, Optional

import pandas as pd
from pymongo import MongoClient


# ---------- Logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
log = logging.getLogger("add_lang_from_mongo")


# ---------- Helpers ----------
ID_COLUMN_CANDIDATES = ("articleid", "article_id", "articleId", "_id", "id")

def detect_id_column(df: pd.DataFrame, preferred: Optional[str] = None) -> Optional[str]:
    """Pick an ID column in a case-insensitive way, honoring a preferred name if provided."""
    cols_lower = {c.lower(): c for c in df.columns}
    if preferred:
        if preferred in df.columns:
            return preferred
        if preferred.lower() in cols_lower:
            return cols_lower[preferred.lower()]

    for c in ID_COLUMN_CANDIDATES:
        if c in df.columns:
            return c
        if c.lower() in cols_lower:
            return cols_lower[c.lower()]
    return None


def decode_unicode_escapes(text: str) -> str:
    """
    Decode Unicode escape sequences in text.
    Converts strings like '\\u09b8\\u09cb\\u09a8\\u09bf' to readable Unicode text.
    """
    if not isinstance(text, str) or not text:
        return text
    
    try:
        # Handle JSON-like strings that might contain Unicode escapes
        if text.startswith('{') and text.endswith('}'):
            try:
                # Try to parse as JSON first, which will automatically decode Unicode escapes
                decoded_obj = json.loads(text)
                # Convert back to JSON string with proper Unicode characters
                return json.dumps(decoded_obj, ensure_ascii=False)
            except (json.JSONDecodeError, ValueError):
                pass
        
        # Handle direct Unicode escape sequences
        # This will decode sequences like \u09b8 to actual Unicode characters
        try:
            decoded = text.encode().decode('unicode_escape')
            return decoded
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
        
        # If all else fails, return original text
        return text
    except Exception:
        return text


def process_unicode_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Process DataFrame columns that might contain Unicode escape sequences.
    Focuses on columns that typically contain JSON-like data with Unicode.
    """
    # Columns that commonly contain JSON with Unicode escapes
    unicode_columns = []
    
    # Look for columns that might contain JSON-like data
    for col in df.columns:
        if any(keyword in col.lower() for keyword in ['source', 'keyword', 'tag', 'detail']):
            unicode_columns.append(col)
    
    # Also check for columns that contain curly braces (JSON-like)
    for col in df.columns:
        if col not in unicode_columns:
            sample_values = df[col].dropna().head(10)
            if any(str(val).strip().startswith('{') and str(val).strip().endswith('}') for val in sample_values):
                unicode_columns.append(col)
    
    log.info(f"Processing Unicode escapes in columns: {unicode_columns}")
    
    # Apply Unicode decoding to identified columns
    for col in unicode_columns:
        log.info(f"Decoding Unicode escapes in column: {col}")
        df[col] = df[col].apply(decode_unicode_escapes)
    
    return df


def normalize_article_id(val) -> Optional[int]:
    """
    Robustly parse an article ID from typical CSV/Excel forms:
    - "12345678" / "12345678.0"
    - "1.2345E+7"
    - stray commas/spaces
    Returns int or None.
    """
    if val is None:
        return None
    s = str(val).strip()
    if s == "" or s.lower() in {"nan", "none", "null"}:
        return None

    # Remove thousands separators and whitespace
    s = re.sub(r"[,\s]", "", s)

    # If plain integer like "12345" or "12345.0"
    m = re.fullmatch(r"(-?\d+)(?:\.0+)?", s)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None

    # Scientific notation like "1.234E+7"
    try:
        if re.search(r"[eE]", s):
            return int(float(s))
    except Exception:
        pass

    # Last resort: strip non-digits (keeps minus as well)
    s2 = re.sub(r"[^\d-]", "", s)
    if s2 == "" or s2 == "-":
        return None
    try:
        return int(s2)
    except Exception:
        return None


def fetch_languages(
    ids: List[Optional[int]],
    batch_size: int = 1000,
) -> Dict[int, Optional[str]]:
    """
    Fetch articleInfo.language for a list of _id values in batches.
    Returns a dict: _id -> language (or None if missing).
    """
    client = MongoClient(
        os.getenv("PG_MONGO_URI", "mongodb://localhost:27017/"),
        connectTimeoutMS=20000,
        serverSelectionTimeoutMS=20000,
        retryWrites=True,
    )
    coll = client["pnq"]["article"]

    result: Dict[int, Optional[str]] = {}
    unique_ids = [i for i in dict.fromkeys(ids) if i is not None]  # keep order, drop None/dups
    log.info(f"Querying Mongo: {len(unique_ids)} unique IDs in batches of {batch_size}…")
    
    # Debug: Show first few IDs we're looking for
    log.info(f"Sample IDs to query: {unique_ids[:5]}")

    for start in range(0, len(unique_ids), batch_size):
        chunk = unique_ids[start : start + batch_size]
        log.info(f"Querying batch {start//batch_size+1}: {len(chunk)} IDs")
        
        cursor = coll.find(
            {"_id": {"$in": chunk}},
            {"articleData.language": 1}
        )
        found = 0
        languages_found = 0
        for doc in cursor:
            found += 1
            lang = None
            ad = doc.get("articleData")
            if isinstance(ad, dict):
                lang = ad.get("language")
                if lang:
                    languages_found += 1
            result[int(doc["_id"])] = lang
        
        log.info(f"Batch {start//batch_size+1}: found {found} documents, {languages_found} with language field")

    client.close()
    return result


def main():
    parser = argparse.ArgumentParser(description="Add articleInfo.language to CSV via Mongo lookups.")
    parser.add_argument("input_csv", help="Path to input CSV")
    parser.add_argument("-o", "--output-csv", default=None, help="Path to output CSV (default: auto-suffix)")
    parser.add_argument("--inplace", action="store_true", help="Overwrite input CSV in place")
    parser.add_argument("--id-column", default=None, help="Column name that holds article IDs (case-insensitive)")
    parser.add_argument("--column-name", default="article_language", help="Name of the new column to write")

    parser.add_argument("--batch-size", type=int, default=1000)
    args = parser.parse_args()

    # Read CSV as strings to avoid Excel-mangled ints
    log.info(f"Reading CSV: {args.input_csv}")
    df = pd.read_csv(args.input_csv, dtype=str, keep_default_na=False, encoding="utf-8-sig")

    id_col = detect_id_column(df, args.id_column)
    if not id_col:
        raise SystemExit(
            f"Could not detect an article ID column. "
            f"Tried: {', '.join(ID_COLUMN_CANDIDATES)}. "
            f"Use --id-column to specify explicitly."
        )
    log.info(f"Using ID column: {id_col}")

    # Normalize IDs
    ids = [normalize_article_id(v) for v in df[id_col].tolist()]
    missing_id_rows = sum(1 for x in ids if x is None)
    if missing_id_rows:
        log.warning(f"{missing_id_rows} rows have missing/unparseable IDs; language will be blank for those.")

    # Query Mongo
    lang_map = fetch_languages(ids, args.batch_size)

    # Map back to DataFrame
    df[args.column_name] = [lang_map.get(i) if i is not None else None for i in ids]

    # Process Unicode escape sequences in the DataFrame
    log.info("Processing Unicode escape sequences in CSV data...")
    df = process_unicode_columns(df)

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
    hits = sum(1 for i in ids if i is not None and i in lang_map)
    log.info(f"Done. Languages found for {hits} / {len(df)} rows.")
    log.info(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()
