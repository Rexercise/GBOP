"""Deterministic journal accounting. No prices, fills or risk are inferred by AI.

Values stay decimal strings at the persistence/API boundary. A calculated R is
net realized cash / original risk. Added risk is disclosed, never averaged into
leg R. Journal balances are closed/realized cash records, not live equity.
"""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re

SOURCES = {'member_confirmed', 'estimate', 'awaiting_confirmation', 'unknown'}
KNOWN = {'member_confirmed', 'imported_verified'}
FACT_KEYS = {'value', 'source', 'quote'}
FIELDS = {'account_id', 'currency', 'opened_at', 'position_status', 'opening_balance',
          'initial_risk', 'risk_pct', 'usual_risk', 'realizations', 'realized_complete', 'risk_changes'}


def number(value, *, positive=False):
    if isinstance(value, bool) or value is None:
        raise ValueError('Use a finite monetary amount or leave it unknown.')
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('Use a decimal number, without currency symbols.') from None
    if not result.is_finite() or abs(result) > Decimal('1e15') or (positive and result <= 0):
        raise ValueError('The amount must be finite and positive where risk is required.')
    return result


def decimal_text(value):
    return format(value.normalize(), 'f') if value else '0'


def money(value, currency='USD'):
    quantum=Decimal('1') if currency in {'JPY','KRW','CLP'} else Decimal('.001') if currency in {'BHD','KWD','OMR','JOD','TND'} else Decimal('.01')
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def instant(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError('Use the actual date and time with timezone, or leave it unknown.')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('A cash movement or fill time needs an explicit timezone.')
    return result.astimezone(timezone.utc).isoformat()


def fact(value, *, positive=False, trusted=False):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value)-FACT_KEYS:
        raise ValueError('A number needs value, source and the member’s supporting quote.')
    source = value.get('source')
    if source not in SOURCES | ({'imported_verified'} if trusted else set()):
        raise ValueError('Keep confirmed, estimated, awaiting-confirmation and unknown facts distinct.')
    raw = value.get('value')
    if raw is None:
        if source not in {'unknown', 'awaiting_confirmation'}:
            raise ValueError('A known amount needs a value.')
        return dict(value=None, source=source, quote=value.get('quote'))
    amount = number(raw, positive=positive)
    if source == 'unknown':
        raise ValueError('An unknown amount cannot contain an asserted number.')
    quote = value.get('quote')
    if not trusted and (not isinstance(quote, str) or not quote.strip() or len(quote)>2000):
        raise ValueError('Keep the actual supporting words with each supplied number.')
    return dict(value=decimal_text(amount), source=source, quote=quote)


def confirmed(value):
    return number(value['value']) if value and value.get('source') in KNOWN and value.get('value') is not None else None


def validate(value, *, trusted=False):
    if not isinstance(value, dict) or set(value)-FIELDS:
        raise ValueError('Use only documented accounting fields.')
    value = deepcopy(value)
    if value.get('account_id') is not None and not re.fullmatch(r'[a-f0-9]{32}', value['account_id']):
        raise ValueError('Select the owned account returned by get_journal_accounts.')
    if value.get('currency') is not None and not re.fullmatch('[A-Z]{3}', value['currency']):
        raise ValueError('Use a three-letter account currency.')
    if value.get('opened_at') is not None:
        value['opened_at'] = instant(value['opened_at'])
    if value.get('position_status') not in (None, 'unknown', 'open', 'partially_closed', 'closed'):
        raise ValueError('Position status is independent of market objective delivery.')
    for key in ('opening_balance', 'initial_risk', 'risk_pct'):
        if key in value:
            value[key] = fact(value[key], positive=True, trusted=trusted)
    if value.get('risk_pct') and number(value['risk_pct']['value'] or 0) > 100:
        raise ValueError('Risk percent cannot exceed 100.')
    if value.get('usual_risk') is not None and value['usual_risk'] not in ('usual', 'half_normal'):
        raise ValueError('Half size is ambiguous; only explicit usual risk or half normal risk can use a preset.')
    if value.get('realized_complete') is not None and type(value['realized_complete']) is not bool:
        raise ValueError('realized_complete must explicitly identify whether all realized cash is recorded.')
    for collection in ('realizations', 'risk_changes'):
        if collection not in value:
            continue
        if not isinstance(value[collection], list) or len(value[collection]) > 40:
            raise ValueError('Use a bounded list of separate, stably identified cash or risk events.')
        seen = set()
        for event in value[collection]:
            allowed = {'event_key', 'at', 'amount', 'basis', 'fees'} if collection == 'realizations' else {'event_key','at','amount','kind','note'}
            if not isinstance(event,dict) or set(event)-allowed or not re.fullmatch('[a-zA-Z0-9_-]{1,64}', str(event.get('event_key',''))):
                raise ValueError('Each event needs a stable event_key and documented fields.')
            if event['event_key'] in seen:
                raise ValueError('Duplicate event keys would double-count cash.')
            seen.add(event['event_key'])
            if event.get('at') is not None:
                event['at'] = instant(event['at'])
                if value.get('opened_at') and event['at'] < value['opened_at']:
                    raise ValueError('Realized cash cannot precede the trade opening.')
            event['amount'] = fact(event.get('amount'), positive=collection=='risk_changes', trusted=trusted)
            if collection == 'realizations':
                if event.get('basis') not in ('net', 'gross'):
                    raise ValueError('Say whether P/L already includes fees (net) or excludes them (gross).')
                event['fees'] = fact(event.get('fees'), trusted=trusted)
                if event['fees'] and number(event['fees']['value'] or 0) < 0:
                    raise ValueError('Fees are a nonnegative cost.')
            elif event.get('kind') not in ('added_risk', 'reduced_risk', 'stop_change'):
                raise ValueError('Keep later risk additions and stop changes separate from initial risk.')
    return value


def merge(previous, patch):
    result = deepcopy(previous or {})
    for key, value in validate(patch).items():
        # Explicit unknown facts retract numbers; null preserves previous data.
        if value is None:
            continue
        if key in ('realizations','risk_changes'):
            events = {e['event_key']: deepcopy(e) for e in result.get(key,[])}
            for event in value:
                events[event['event_key']] = event
            result[key] = list(events.values())
        else:
            result[key] = value
    return validate(result, trusted=True)


def net_cash(event):
    amount = confirmed(event.get('amount'))
    if amount is None:
        return None
    if event['basis'] == 'net':
        return amount  # A disclosed fee is already included: never subtract twice.
    fees = confirmed(event.get('fees'))
    return amount - fees if fees is not None else None


def project(account, trades, *, start_at=None, end_at=None):
    """Recompute chronological derived values; confirmed source facts are immutable.

    Unknown or untimed realized effects break balance-derived risk until an
    explicit balance snapshot is supplied. Equal open/close times are ambiguous.
    External cashflows segment the return: linked subperiod returns, not sum R.
    """
    start = instant(start_at) if start_at else account['balance_at']
    end = instant(end_at) if end_at else None
    if start < account['balance_at'] or (end and end < start):
        raise ValueError('Use an interval after the account opening snapshot, with end after start.')
    seed = confirmed(account['balance'])
    cash = []
    unknown = []
    for flow in account.get('cash_movements', []):
        amount = confirmed(flow['amount'])
        if amount is None:
            unknown.append((flow.get('at'), 'unconfirmed cash movement'))
        else:
            cash.append((flow['at'], 'flow', flow['event_key'], amount))
    stats=[]
    for trade in trades:
        data = trade.get('accounting') or {}
        if data.get('account_id') != account['id']:
            continue
        if data.get('currency') != account['currency']:
            raise ValueError('Account currency differs from saved trade; reconcile the account association.')
        data = validate(data, trusted=True)
        events = data.get('realizations', [])
        complete = data.get('realized_complete') is True and all(net_cash(e) is not None for e in events)
        if data.get('position_status') == 'closed' and not events:
            complete = False
        for event in events:
            amount = net_cash(event)
            at = event.get('at')
            if amount is None or at is None:
                unknown.append((at or data.get('opened_at'), 'unknown/untimed realized cash'))
            else:
                cash.append((at, 'trade', str(trade['id'])+':'+event['event_key'], amount))
        if not complete:
            unknown.append((data.get('opened_at'), 'incomplete realized cash'))
        stats.append(dict(id=trade['id'], trade_number=trade.get('trade_number'), data=data,
                          net=sum((net_cash(e) for e in events if net_cash(e) is not None),Decimal(0)),
                          complete=complete, reported_r=trade.get('reported_r')))
    cash.sort(key=lambda e:(e[0], e[1], e[2]))

    def balance_before(at):
        if seed is None or not at or at < account['balance_at']:
            return None
        if any(t is None or account['balance_at'] < t < at for t,_ in unknown):
            return None
        # Exact ties cannot establish whether the cash was available at entry.
        if any(t==at for t,_,_,_ in cash):
            return None
        return seed + sum((v for t,_,_,v in cash if account['balance_at'] < t < at),Decimal(0))

    output=[]
    for item in stats:
        data=item['data']
        risk=confirmed(data.get('initial_risk'))
        basis=confirmed(data.get('opening_balance'))
        basis_source='member_confirmed' if basis is not None else 'journal_derived'
        if basis is None:
            basis=balance_before(data.get('opened_at'))
        if risk is None and confirmed(data.get('risk_pct')) is not None and basis is not None and basis>0:
            risk=money(basis*confirmed(data['risk_pct'])/100,account['currency'])
        risk_conflict=(risk is not None and basis is not None and confirmed(data.get('risk_pct')) is not None and risk!=money(basis*confirmed(data['risk_pct'])/100,account['currency']))
        if risk_conflict: risk=None
        if risk is not None and risk<=0: risk=None
        closed=data.get('position_status')=='closed'
        calculated=item['net']/risk if risk is not None and item['complete'] else None
        last=max((e.get('at') or '' for e in data.get('realizations',[])),default='')
        in_period=(not start_at and not end_at) or bool(last and start <= last and (not end or last <= end))
        output.append(dict(journal_id=item['id'],trade_number=item['trade_number'],position_status=data.get('position_status','unknown'),
            opening_balance=decimal_text(basis) if basis is not None else None, opening_balance_source=basis_source if basis is not None else 'unknown',
            initial_risk=decimal_text(risk) if risk is not None else None, risk_basis_conflict=risk_conflict,
            net_realized=decimal_text(item['net']) if item['complete'] else None,
            known_net_realized_subtotal=decimal_text(item['net']),realized_complete=item['complete'],
            facts=deepcopy(data),
            calculated_r=decimal_text(calculated) if calculated is not None else None,
            r_source='calculated_original_risk' if calculated is not None else ('member_reported' if item['reported_r'] is not None else 'unknown'),
            reported_r=item['reported_r'],risk_changes=deepcopy(data.get('risk_changes',[])),
            included_in_completed_stats=closed and in_period,closed_at=last or None))
    selected=[t for t in output if t['included_in_completed_stats']]
    known_r=[number(t['calculated_r']) for t in selected if t['calculated_r'] is not None]
    reported=[number(t['reported_r']) for t in selected if t['calculated_r'] is None and t['reported_r'] is not None]
    known_pnl=[number(t['net_realized']) for t in selected if t['realized_complete']]
    period=[e for e in cash if e[0] >= start and (not end or e[0] <= end) and e[0] > account['balance_at']]
    opening=seed + sum((e[3] for e in cash if account['balance_at'] < e[0] < start),Decimal(0)) if seed is not None else None
    opening_known=opening is not None and not any(t is None or account['balance_at'] < t < start for t,_ in unknown)
    valid=opening_known and not any(t is None or (t > account['balance_at'] and (not end or t<=end)) for t,_ in unknown)
    return_valid=valid
    running=opening
    growth=Decimal(1)
    segment=opening
    curve=[dict(at=start,balance=decimal_text(opening),kind='opening',source_key='opening')] if opening is not None else []
    for at,kind,key,amount in period:
        if running is None: break
        if kind=='flow':
            if segment is not None and segment>0:
                growth*=running/segment
            else:return_valid=False
            if any(t==at and k=='trade' for t,k,_,_ in period):return_valid=False
            running+=amount
            segment=running
        else:
            running+=amount
        curve.append(dict(at=at,balance=decimal_text(running),kind=kind,source_key=key))
    if running is not None and segment is not None and segment>0:
        growth*=running/segment
    else:return_valid=False
    net=sum((e[3] for e in period if e[1]=='trade'),Decimal(0))
    flow=sum((e[3] for e in period if e[1]=='flow'),Decimal(0))
    return dict(account_id=account['id'],account_name=account['name'],currency=account['currency'],
        opening_balance=decimal_text(opening) if opening_known else None,
        closing_balance=decimal_text(running) if valid and running is not None else None,
        known_balance_subtotal=decimal_text(running) if running is not None else None,
        balance_complete=valid,net_trading_pnl=decimal_text(net) if valid else None,
        known_net_trading_pnl_subtotal=decimal_text(net),cash_movements=decimal_text(flow),
        return_pct=decimal_text((growth-1)*100) if return_valid else None,
        return_definition='Linked realized-balance subperiod returns, segmented at external cash movements; excludes open unrealized P/L.',
        completed_trade_count=len(selected),open_trade_count=sum(t['position_status'] in ('open','partially_closed') for t in output),
        calculated_r_total=decimal_text(sum(known_r,Decimal(0))) if known_r else None,
        reported_r_total=decimal_text(sum(reported,Decimal(0))) if reported else None,
        r_coverage=dict(calculated=len(known_r),reported_only=len(reported),unknown=len(selected)-len(known_r)-len(reported),total=len(selected)),
        win_rate_pct=decimal_text(Decimal(sum(v>0 for v in known_pnl))/len(known_pnl)*100) if known_pnl else None,
        win_rate_coverage=len(known_pnl),trades=output,
        curve=curve if valid else [],curve_label='Journal-tracked realized balance (not live broker equity)',
        uncertainties=sorted(set(reason for _,reason in unknown)))


def closeout(values):
    """Optional grouped review hints, never a save gate or per-entry interrogation."""
    data=values.get('accounting') or {}
    missing=[]
    if not data.get('account_id'):missing.append('account')
    if data.get('position_status') not in ('open','partially_closed','closed'):missing.append('position outcome')
    if confirmed(data.get('initial_risk')) is None and confirmed(data.get('risk_pct')) is None:
        missing.append('actual original risk')
    elif confirmed(data.get('initial_risk')) is None and not data.get('opened_at') and confirmed(data.get('opening_balance')) is None:
        missing.append('opening balance or actual entry time for percent risk')
    if data.get('realized_complete') is not True:missing.append('net realized P/L to date')
    if any(e.get('basis')=='gross' and confirmed(e.get('fees')) is None for e in data.get('realizations',[])):
        missing.append('fees on gross P/L')
    return dict(missing=missing,defer_allowed=True,instruction='Let the member finish first. One short grouped closeout, only if useful; unknowns never block saving. Cash movements are separate from trade profit.')
