"""Persistent, versioned PR6.3 event results."""
from __future__ import annotations
import json, sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable
from .detector import EventDecision
from .rules import normalize_symbol

def _now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def upsert_events(connection: sqlite3.Connection, values: Iterable[tuple[object, object, str, EventDecision, object, object, object, object, object, object, str|None]]) -> int:
    rows=[]; evidence_rows=[]; now=_now()
    for symbol, day, version, d, op, high, low, close, pre_close, upper, run_id in values:
        if not d.is_first_limit:
            raise ValueError("first_limit_events only accepts confirmed first-limit decisions")
        security=normalize_symbol(symbol)
        rows.append((security.canonical,security.exchange,str(day),version,d.status.value,None if d.is_limit_up_close is None else int(d.is_limit_up_close),None if d.touched_upper_limit is None else int(d.touched_upper_limit),None if d.is_first_limit is None else int(d.is_first_limit),None if d.is_one_word_limit is None else int(d.is_one_word_limit),None if d.is_consecutive_limit is None else int(d.is_consecutive_limit),d.consecutive_limit_days,20,d.observed_lookback_days,str(d.previous_limit_up_date) if d.previous_limit_up_date else None,op,high,low,close,pre_close,upper,str(d.upper_limit_source or ''),json.dumps(sorted(x.value for x in d.reasons)),json.dumps(sorted(d.quality_flags)),run_id,now,now,now))
        if d.price_limit_evidence:
            payload={**d.price_limit_evidence,"symbol":security.canonical,"trade_date":str(day),"is_close_limit_up":d.is_limit_up_close}
            evidence_rows.append((security.canonical,str(day),version,"PRICE_LIMIT_SOURCE",json.dumps(payload,ensure_ascii=False),now))
    connection.executemany('''INSERT INTO first_limit_events(symbol,exchange,trade_date,detection_version,detection_status,is_limit_up_close,touched_upper_limit,is_first_limit,is_one_word_limit,is_consecutive_limit,consecutive_limit_days,lookback_trading_days,observed_lookback_days,previous_limit_up_date,open,high,low,close,pre_close,upper_limit_price,upper_limit_source,exclusion_reasons,quality_flags,source_run_id,detected_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol,trade_date,detection_version) DO UPDATE SET detection_status=excluded.detection_status,is_limit_up_close=excluded.is_limit_up_close,touched_upper_limit=excluded.touched_upper_limit,is_first_limit=excluded.is_first_limit,is_one_word_limit=excluded.is_one_word_limit,is_consecutive_limit=excluded.is_consecutive_limit,consecutive_limit_days=excluded.consecutive_limit_days,observed_lookback_days=excluded.observed_lookback_days,previous_limit_up_date=excluded.previous_limit_up_date,exclusion_reasons=excluded.exclusion_reasons,quality_flags=excluded.quality_flags,source_run_id=excluded.source_run_id,detected_at=excluded.detected_at,updated_at=excluded.updated_at''',rows)
    if evidence_rows:
        connection.executemany("""INSERT INTO first_limit_event_evidence(symbol,trade_date,detection_version,rule_code,evidence_json,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(symbol,trade_date,detection_version,rule_code) DO UPDATE SET evidence_json=excluded.evidence_json,updated_at=excluded.updated_at""", evidence_rows)
    return len(rows)
def get_events_for_date(connection, day, version, *, detected_only=False):
    sql='SELECT * FROM first_limit_events WHERE trade_date=? AND detection_version=?'; args=[str(day),version]
    if detected_only: sql+=' AND detection_status=\'detected\''
    return connection.execute(sql+' ORDER BY symbol',args).fetchall()
def get_events_for_symbol(connection,symbol,start,end): return connection.execute('SELECT * FROM first_limit_events WHERE symbol=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date',(normalize_symbol(symbol).canonical,str(start),str(end))).fetchall()
def get_indeterminate_events(connection, day, version): return connection.execute("SELECT * FROM first_limit_events WHERE trade_date=? AND detection_version=? AND detection_status='indeterminate' ORDER BY symbol",(str(day),version)).fetchall()
def date_version_complete(connection, day, version):
    return connection.execute("SELECT 1 FROM first_limit_sync_runs WHERE sync_type='detect' AND status='success' AND parameters_json LIKE ? LIMIT 1",(f'%"detection_version": "{version}"%',)).fetchone() is not None
def scan_statistics(connection, run_id):
    row=connection.execute('SELECT planned_count,success_count,skipped_count,failure_count,status FROM first_limit_sync_runs WHERE run_id=?',(run_id,)).fetchone()
    return dict(row) if row else None
