"""Read-only, counts-only audit for Bilibili learning and chat projection.

The script never prints message text, source text, IDs, authors, URLs, keys, or
model prompts. It only opens SQLite in read-only/query-only mode and emits
aggregate counts and token totals.
"""
import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import quote


def _connect(path):
    value = str(path)
    if value.startswith("file:"):
        if "mode=ro" not in value:
            raise ValueError("SQLite URI must explicitly request mode=ro")
        uri = value
    else:
        uri = "file:" + quote(str(Path(value).resolve()).replace("\\", "/"), safe="/:@") + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def _tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _json(value):
    try:
        result = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


def collect(db, *, now=None, window_days=7, reply_count=400):
    """Return aggregates only; queried text remains local to this process."""
    now = time.time() if now is None else float(now)
    tables = _tables(db)
    result = {
        "captured_at_epoch": int(now),
        "window_days": int(window_days),
        "privacy": "counts_only_no_chat_or_source_text_emitted",
        "bilibili": {},
        "expression_learning": {},
        "chat_projection": {},
        "growth_model_usage": {},
    }

    readings = []
    if "bilibili_readings" in tables:
        readings = [dict(row) for row in db.execute(
            "SELECT bvid,state,metadata,source_id,journal_id FROM bilibili_readings"
        )]
    feeds = Counter()
    states = Counter()
    source_ids = {row["source_id"] for row in readings if row.get("source_id")}
    source_text = {}
    if "growth_sources" in tables and source_ids:
        marks = ",".join("?" for _ in source_ids)
        source_text = {row["id"]: row["body"] for row in db.execute(
            f"SELECT id,body FROM growth_sources WHERE id IN ({marks})", tuple(source_ids)
        )}
    subtitle_readings = 0
    subtitle_segments = 0
    readings_with_material = 0
    for row in readings:
        states[str(row.get("state") or "unknown")] += 1
        metadata = _json(row.get("metadata"))
        feeds[str(metadata.get("feed") or "unknown")] += 1
        raw_body = source_text.get(row.get("source_id"))
        body = _json(raw_body)
        if raw_body:
            readings_with_material += 1
        segments = body.get("subtitles")
        if not isinstance(segments, list):
            # Older source records used `segments` for the subtitle list.
            segments = body.get("segments")
        if isinstance(segments, list) and segments:
            subtitle_readings += 1
            subtitle_segments += len(segments)
    result["bilibili"] = {
        "reading_records": len(readings),
        "distinct_videos": len({row.get("bvid") for row in readings if row.get("bvid")}),
        "states": dict(sorted(states.items())),
        "entry_feeds": dict(sorted(feeds.items())),
        "readings_with_subtitles": subtitle_readings,
        "readings_with_saved_material": readings_with_material,
        "readings_without_saved_material": max(0, len(readings) - readings_with_material),
        "readings_with_material_without_subtitles": max(0, readings_with_material - subtitle_readings),
        "subtitle_segments_available": subtitle_segments,
    }

    journals = []
    if "growth_journals" in tables:
        journals = [dict(row) for row in db.execute(
            "SELECT id,sources,body FROM growth_journals ORDER BY created DESC"
        )]
    bili_source_ids = set()
    if "growth_sources" in tables:
        bili_source_ids = {row[0] for row in db.execute(
            "SELECT id FROM growth_sources WHERE kind='bilibili'"
        )}
    bili_journals = []
    for row in journals:
        try:
            ids = json.loads(row.get("sources") or "[]")
        except (TypeError, ValueError):
            ids = []
        if isinstance(ids, list) and bili_source_ids.intersection(map(str, ids)):
            bili_journals.append(row)

    expression_rows = []
    if "growth_insights" in tables and "growth_memory_policy" in tables:
        expression_rows = [dict(row) for row in db.execute("""
            SELECT i.memory_id,i.journal_id,coalesce(p.source_kind,'legacy') source_kind,
                coalesce(p.chat_enabled,0) chat_enabled
            FROM growth_insights i LEFT JOIN growth_memory_policy p ON p.memory_id=i.memory_id
            WHERE i.category='expression'
        """)]
    bili_journal_ids = {row["id"] for row in bili_journals}
    bili_expressions = [row for row in expression_rows if row["journal_id"] in bili_journal_ids]
    evidence_counts = Counter()
    if "memory_evidence" in tables and bili_expressions:
        ids = [row["memory_id"] for row in bili_expressions]
        marks = ",".join("?" for _ in ids)
        evidence_rows = db.execute(f"SELECT memory_id,excerpt,receipt FROM memory_evidence WHERE memory_id IN ({marks})", ids)
        for row in evidence_rows:
            receipt = _json(row["receipt"])
            event = receipt.get("reaction_event")
            if isinstance(event, dict):
                evidence_counts[str(event.get("reaction_kind") or "unclassified")] += 1
                continue
            excerpt = str(row["excerpt"] or "")
            journal = next((item for item in bili_journals
                            if item["id"] == next((e["journal_id"] for e in bili_expressions
                                                    if e["memory_id"] == row["memory_id"]), None)), None)
            source_body = {}
            if journal:
                try:
                    linked = json.loads(journal["sources"] or "[]")
                except (TypeError, ValueError):
                    linked = []
                for source_id in linked:
                    raw = source_text.get(source_id)
                    if raw is None:
                        found = db.execute("SELECT body FROM growth_sources WHERE id=?", (source_id,)).fetchone()
                        raw = found[0] if found else None
                    candidate = _json(raw)
                    if candidate:
                        source_body = candidate
                        break
            if any(isinstance(item, dict) and excerpt and excerpt in str(item.get("text") or "")
                   for item in source_body.get("subtitles", source_body.get("segments", []))):
                evidence_counts["subtitle"] += 1
            elif any(isinstance(item, dict) and excerpt and excerpt in str(item.get("text") or "")
                     and item.get("parent_id") for item in source_body.get("comments", [])):
                evidence_counts["reply_thread_or_comment"] += 1
            elif any(isinstance(item, dict) and excerpt and excerpt in str(item.get("text") or "")
                     for item in source_body.get("comments", [])):
                evidence_counts["whole_video_comment"] += 1
            elif any(isinstance(item, dict) and excerpt and excerpt in str(item.get("text") or "")
                     for item in source_body.get("danmaku", [])):
                evidence_counts["danmaku"] += 1
            else:
                evidence_counts["unverified_or_other"] += 1
    speech_summaries = 0
    expression_summaries = 0
    for row in bili_journals:
        body = _json(row.get("body"))
        speech_summaries += bool(str(body.get("speech_summary") or "").strip())
        expression_summaries += bool(str(body.get("expression_summary") or "").strip())
    result["expression_learning"] = {
        "bilibili_reflection_journals": len(bili_journals),
        "journals_with_verified_speech_summary": int(speech_summaries),
        "journals_with_general_expression_observation": int(expression_summaries),
        "bilibili_expression_memories": len(bili_expressions),
        "chat_enabled_bilibili_expression_memories": sum(bool(row["chat_enabled"]) for row in bili_expressions),
        "verified_evidence_kinds": dict(sorted(evidence_counts.items())),
    }

    projection = Counter()
    samples = 0
    if "reply_metrics" in tables:
        rows = db.execute("SELECT metrics FROM reply_metrics ORDER BY updated DESC LIMIT ?", (max(1, reply_count),))
        for row in rows:
            samples += 1
            metrics = _json(row["metrics"])
            growth = metrics.get("growth")
            if not isinstance(growth, dict):
                projection["diagnostics_missing"] += 1
                continue
            count = growth.get("count")
            try:
                count = max(0, int(count))
            except (TypeError, ValueError, OverflowError):
                projection["diagnostics_missing"] += 1
                continue
            projection["with_reference"] += count > 0
            projection["without_reference"] += count == 0
            projection["reference_items"] += count
    result["chat_projection"] = {
        "reply_records_sampled": samples,
        "replies_with_growth_reference": projection["with_reference"],
        "replies_without_growth_reference": projection["without_reference"],
        "replies_missing_growth_diagnostics": projection["diagnostics_missing"],
        "reference_items_total": projection["reference_items"],
        "share_of_records_with_reference": round(projection["with_reference"] / samples, 4) if samples else None,
    }

    cutoff = now - max(1, window_days) * 86400
    usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "by_kind": {}}
    if {"growth_usage_links", "growth_runs", "model_token_usage"}.issubset(tables):
        rows = db.execute("""
            SELECT r.kind,u.input_tokens,u.output_tokens,u.total_tokens
            FROM growth_usage_links l
            JOIN growth_runs r ON r.id=l.run_id
            JOIN model_token_usage u ON u.id=l.usage_id
            WHERE u.created>=?
        """, (cutoff,))
        kinds = {}
        for row in rows:
            kind = str(row["kind"] or "unknown")
            item = kinds.setdefault(kind, {"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
            item["requests"] += 1
            usage["requests"] += 1
            for field in ("input_tokens", "output_tokens", "total_tokens"):
                amount = max(0, int(row[field] or 0))
                item[field] += amount
                usage[field] += amount
        usage["by_kind"] = dict(sorted(kinds.items()))
        # The link table was added after some historical usage rows. Report
        # unlinked growth-prefixed requests separately instead of hiding them.
        linked = {row[0] for row in db.execute(
            "SELECT usage_id FROM growth_usage_links WHERE usage_id IN "
            "(SELECT id FROM model_token_usage WHERE created>=?)", (cutoff,)
        )}
        unlinked = db.execute("""
            SELECT count(*) requests,coalesce(sum(input_tokens),0) input_tokens,
                coalesce(sum(output_tokens),0) output_tokens,coalesce(sum(total_tokens),0) total_tokens
            FROM model_token_usage WHERE created>=? AND operation LIKE 'growth_%'
        """, (cutoff,)).fetchone()
        linked_growth = db.execute("""
            SELECT count(DISTINCT u.id) FROM model_token_usage u
            JOIN growth_usage_links l ON l.usage_id=u.id WHERE u.created>=?
        """, (cutoff,)).fetchone()[0]
        usage["growth_prefixed_unlinked"] = {
            "requests": max(0, int(unlinked["requests"] or 0) - int(linked_growth or 0)),
            "input_tokens": max(0, int(unlinked["input_tokens"] or 0) - sum(
                row["input_tokens"] for row in db.execute("""
                    SELECT u.input_tokens FROM model_token_usage u JOIN growth_usage_links l ON l.usage_id=u.id
                    WHERE u.created>=? AND u.operation LIKE 'growth_%'
                """, (cutoff,)))),
            "output_tokens": max(0, int(unlinked["output_tokens"] or 0) - sum(
                row["output_tokens"] for row in db.execute("""
                    SELECT u.output_tokens FROM model_token_usage u JOIN growth_usage_links l ON l.usage_id=u.id
                    WHERE u.created>=? AND u.operation LIKE 'growth_%'
                """, (cutoff,)))),
            "total_tokens": max(0, int(unlinked["total_tokens"] or 0) - sum(
                row["total_tokens"] for row in db.execute("""
                    SELECT u.total_tokens FROM model_token_usage u JOIN growth_usage_links l ON l.usage_id=u.id
                    WHERE u.created>=? AND u.operation LIKE 'growth_%'
                """, (cutoff,)))),
        }
    result["growth_model_usage"] = usage
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only, counts-only Bilibili learning and projection audit")
    parser.add_argument("database", help="SQLite path or file: URI with mode=ro")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--replies", type=int, default=400)
    args = parser.parse_args(argv)
    if args.days < 1 or args.days > 365 or args.replies < 1 or args.replies > 10000:
        parser.error("days/replies out of allowed range")
    try:
        db = _connect(args.database)
        try:
            print(json.dumps(collect(db, window_days=args.days, reply_count=args.replies), ensure_ascii=False, indent=2))
        finally:
            db.close()
    except (OSError, sqlite3.Error, ValueError) as error:
        print(f"audit failed: {type(error).__name__}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
