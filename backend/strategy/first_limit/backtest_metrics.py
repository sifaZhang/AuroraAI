"""Pure nullable-safe PR6.7 event/portfolio reporting helpers."""
from __future__ import annotations
from decimal import Decimal
from statistics import median

def event_metrics(trades, span_days=None):
    complete=[t for t in trades if t.get('net_return') is not None]
    rs=[Decimal(str(t['net_return'])) for t in complete]; wins=[x for x in rs if x>0]; losses=[x for x in rs if x<0]
    return {'signal_count':len(trades),'complete_trade_count':len(complete),'win_rate':Decimal(len(wins))/len(rs) if rs else None,'average_return':sum(rs)/len(rs) if rs else None,'median_return':Decimal(str(median(rs))) if rs else None,'profit_factor':sum(wins)/abs(sum(losses)) if losses else None,'expectancy':sum(rs)/len(rs) if rs else None,'sample_size_warning':len(complete)<30,'small_sample_warning':len(complete)<100,'annualized_status':'available' if span_days and span_days>=250 else 'unstable'}

def benchmark_return(entry_date, exit_date, closes):
    start,end=closes.get(entry_date),closes.get(exit_date)
    return None if start is None or end is None or Decimal(str(start))<=0 else Decimal(str(end))/Decimal(str(start))-1

def sort_signals(signals):
    return sorted(signals,key=lambda x:(-Decimal(str(x['daily_base_score'])),-Decimal(str(x['pullback_score'])),-Decimal(str(x['first_limit_score'])),x['observation_date'],x['symbol']))

def admit_portfolio(signals, cash=Decimal('1000000'), target=Decimal('100000'), max_positions=5):
    accepted=[]; rejected=[]; symbols=set(); events=set(); available=Decimal(str(cash))
    for signal in sort_signals(signals):
        if signal['symbol'] in symbols or signal['event_id'] in events: rejected.append((signal,'duplicate_symbol_or_event'))
        elif len(accepted)>=max_positions: rejected.append((signal,'max_positions_reached'))
        elif available<target: rejected.append((signal,'cash_insufficient'))
        else: accepted.append(signal); symbols.add(signal['symbol']); events.add(signal['event_id']); available-=target
    return accepted,rejected,available

def group_metrics(trades, key):
    groups={}
    for trade in trades: groups.setdefault(str(trade.get(key,'unknown')),[]).append(trade)
    return {name:event_metrics(items) for name,items in groups.items()}

def portfolio_summary(trades):
    """Separate closed returns from unresolved coverage/risk counts."""
    closed=[trade for trade in trades if trade.get('terminal_status')=='closed' and trade.get('net_return') is not None]
    unresolved=[trade for trade in trades if trade.get('terminal_status')=='open_unresolved']
    returns=[Decimal(str(trade['net_return'])) for trade in closed]
    reasons={}
    for trade in unresolved:
        reason=trade.get('unresolved_reason') or 'unknown'
        reasons[reason]=reasons.get(reason,0)+1
    total=len(trades)
    return {
        'trade_count':total,
        'closed_count':len(closed),
        'unresolved_count':len(unresolved),
        'complete_return_count':len(returns),
        'coverage_ratio':Decimal(len(closed))/total if total else None,
        'average_net_return':sum(returns)/len(returns) if returns else None,
        'unresolved_reason_counts':reasons,
    }
