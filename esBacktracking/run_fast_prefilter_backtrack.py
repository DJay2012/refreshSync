#!/usr/bin/env python3
"""
FAST prefiltered backtracking runner (no API / Redis queue).

Standard backtracking scans EVERY article/feed in the date range and percolates
each one — ~10.5M docs for 2026-01-01..today. For a handful of small companies
that is wasteful: <1k docs actually contain their keywords.

This runner injects a keyword PREFILTER (built from each company's own percolator
phrases) into the engine's ES fetch queries, so only candidate documents are
fetched and handed to the UNCHANGED production tagger. Same tags, ~14000x fewer
docs scanned.

How it stays faithful:
- The prefilter is a loose phrase OR across the same text fields the tagger
  concatenates into `content` (headlines+summary+text). It is a SUPERSET of what
  the percolator would match, so it cannot drop a real hit.
- Every candidate still goes through the engine's normal msearch percolation +
  tag creation + Mongo write + dedup + checkpoint path. dry_run is honored.

Usage (inside container):
    python esBacktracking/run_fast_prefilter_backtrack.py
Env override:
    DRY_RUN=true|false   START_DATE=YYYY-MM-DD   END_DATE=YYYY-MM-DD
"""

import os
import sys
import json
import asyncio
from datetime import datetime
from pathlib import Path

current_dir = Path(__file__).parent
sys.path.insert(0, str(current_dir))
sys.path.insert(0, str(current_dir.parent))

from percolator_backtracking import PercolatorBacktrackingEngine
from backtracking_config import BacktrackingConfig

# ---------------------------------------------------------------------------
# RUN PARAMETERS
# ---------------------------------------------------------------------------
COMPANY_IDS = [
    "WESTBRI941",  # WESTBRIDGE CAPITAL
    "KPN486",      # KPN FRESH
    "SAVO481",     # SAVO MART
    "IBO251",      # IBO
]
START_DATE = os.getenv("START_DATE", "2026-01-01")
END_DATE = os.getenv("END_DATE") or datetime.now().strftime("%Y-%m-%d")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("1", "true", "yes")

# Text fields per index that the tagger effectively searches (config.py field_map)
PRINT_TEXT_FIELDS = ["articleData.headlines", "articleData.summary", "articleData.text"]
SOCIAL_TEXT_FIELDS = ["feedData.headlines", "feedData.summary", "feedData.text"]


def _collect_phrases(node, out):
    """Pull every match_phrase 'content' query string out of a percolator query."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "match_phrase" and isinstance(v, dict) and "content" in v:
                c = v["content"]
                out.append(c["query"] if isinstance(c, dict) else c)
            else:
                _collect_phrases(v, out)
    elif isinstance(node, list):
        for x in node:
            _collect_phrases(x, out)


def build_prefilters(engine):
    """Return (print_filter, social_filter) bool-should clauses covering all
    phrases across all languages for the configured companies."""
    es = engine.espreview_engine.es_client
    idx = engine.espreview_config.percolator_index
    resp = es.search(index=idx, body={
        "query": {"terms": {"companyId": COMPANY_IDS}},
        "size": len(COMPANY_IDS) + 5,
    })
    hits = resp.get("hits", {}).get("hits", [])
    phrases = []
    for h in hits:
        src = h["_source"]
        for k, v in src.items():
            if k.startswith("lang_") and v:
                _collect_phrases(v, phrases)
    phrases = list(dict.fromkeys(phrases))  # dedup, keep order
    print(f"  Prefilter built from {len(phrases)} unique phrases across {len(hits)} companies")

    def should(fields):
        return [{"multi_match": {"query": p, "type": "phrase", "fields": fields}} for p in phrases]

    print_filter = {"bool": {"should": should(PRINT_TEXT_FIELDS), "minimum_should_match": 1}}
    social_filter = {"bool": {"should": should(SOCIAL_TEXT_FIELDS), "minimum_should_match": 1}}
    return print_filter, social_filter


def patch_es_client(engine, print_filter, social_filter):
    """Wrap the engine's ES client so every search/count/PIT query against the
    print & social indices gets the keyword prefilter injected as a filter.

    We detect index by the sort field present in the body (articleInfo.articleDate
    vs feedData.feedDate), since the streaming search uses a PIT (no index in URL)."""
    es = engine.espreview_engine.es_client
    orig_search = es.search

    def _inject(body):
        if not isinstance(body, dict):
            return body
        q = body.get("query")
        if not isinstance(q, dict) or "bool" not in q:
            return body
        # Identify index via sort key
        sort = json.dumps(body.get("sort", []))
        if "articleInfo.articleDate" in sort:
            filt = print_filter
        elif "feedData.feedDate" in sort:
            filt = social_filter
        else:
            return body  # not one of our scans (e.g. percolator lookup) — leave alone
        b = q["bool"]
        existing = b.get("filter")
        if existing is None:
            b["filter"] = [filt]
        elif isinstance(existing, list):
            if filt not in existing:
                b["filter"] = existing + [filt]
        else:
            b["filter"] = [existing, filt]
        return body

    def patched_search(*args, **kwargs):
        if "body" in kwargs:
            kwargs["body"] = _inject(kwargs["body"])
        elif args:
            args = (_inject(args[0]),) + args[1:]
        return orig_search(*args, **kwargs)

    es.search = patched_search
    print("  ES client patched: keyword prefilter will be injected into scan queries")


def main():
    config = BacktrackingConfig(
        start_date=START_DATE,
        end_date=END_DATE,
        company_ids=COMPANY_IDS,
        language=None,            # ALL languages
        process_print=True,
        process_online=True,
        dry_run=DRY_RUN,
        tag_workers=32,
        es_page_size=5000,
        msearch_batch_size=200,
        mongo_bulk_batch_size=1000,
        progress_log_interval=2000,
        enable_checkpoints=not DRY_RUN,   # no need to checkpoint a dry run
        use_mongo_checkpoints=True,
        results_file="fast_prefilter_results.json",
    )

    print("=" * 70)
    print("FAST PREFILTER BACKTRACKING" + ("  [DRY RUN]" if DRY_RUN else "  [LIVE — writes tags]"))
    print("=" * 70)
    print(f"Companies : {', '.join(COMPANY_IDS)}")
    print(f"Date range: {START_DATE} -> {END_DATE}")
    print(f"Languages : ALL    Sources: print + online    Dry run: {DRY_RUN}")
    print("=" * 70)

    engine = PercolatorBacktrackingEngine(config)
    print_filter, social_filter = build_prefilters(engine)
    patch_es_client(engine, print_filter, social_filter)

    results = asyncio.run(engine.run_percolator_backtracking(resume=not DRY_RUN))

    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
