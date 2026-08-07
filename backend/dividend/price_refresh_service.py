from __future__ import annotations
from datetime import date, datetime, timezone, timedelta
from backend.data_sources.tushare import TushareClient
from backend.market_data.a_share_daily_repository import DailyBar, upsert_daily_bars

def refresh_enabled_prices(connection, client: TushareClient, day: date, *, dry_run=False):
    enabled=[dict(r) for r in connection.execute("select symbol,company_name from dividend_stable_universe where is_enabled=1 order by symbol")]
    codes={r['symbol'].split('.')[0]:r for r in enabled}; frame=None; trade=None
    for offset in range(10):
        candidate=day-timedelta(days=offset)
        value=client.call('daily',trade_date=candidate.strftime('%Y%m%d'),fields='ts_code,trade_date,open,high,low,close,vol,amount')
        if value is not None and not value.empty: frame=value; trade=candidate; break
    if frame is None: raise RuntimeError('no Tushare daily data in prior 10 days')
    bars=[]; missing=[]
    for row in frame.to_dict('records'):
        code=str(row['ts_code']).split('.')[0]
        if code in codes:
            bars.append(DailyBar(code,str(row['trade_date']),float(row['open']),float(row['high']),float(row['low']),float(row['close']),float(row.get('vol') or 0),float(row.get('amount') or 0),'tushare_daily','none',datetime.now(timezone.utc)))
    found={str(bar.stock_code) for bar in bars}; missing=[codes[code]['symbol'] for code in set(codes)-found]
    if not dry_run: upsert_daily_bars(connection,bars)
    return {'market_price_date':trade.isoformat(),'enabled_count':len(enabled),'price_found_count':len(bars),'price_missing_count':len(missing),'price_written_count':0 if dry_run else len(bars),'price_unchanged_count':0,'failed_symbols':missing}
