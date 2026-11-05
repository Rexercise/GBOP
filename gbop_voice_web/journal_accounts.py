"""Member-owned journal accounts and cash movements; no broker credentials."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re
from uuid import uuid4

from gbop_voice_web import journal_accounting as calc
from gbop_voice_web.journal_context import journal_transaction, binding
from gbop_voice_web.trade_photos import schema, STR, NUM

SCHEMA_SQL = [
    '''CREATE TABLE IF NOT EXISTS journal_accounts (
        id TEXT PRIMARY KEY, guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
        name_key TEXT NOT NULL, payload TEXT NOT NULL, revision INTEGER NOT NULL,
        selected INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
        UNIQUE(guild_id,user_id,name_key))''',
    'CREATE INDEX IF NOT EXISTS journal_accounts_owner ON journal_accounts(guild_id,user_id)',
    'ALTER TABLE journal_accounts ENABLE ROW LEVEL SECURITY',
    'REVOKE ALL ON journal_accounts FROM anon, authenticated',
]
NAMES={'get_journal_accounts','update_journal_account','get_account_performance'}
ACCOUNTING_SCHEMA = """accounting: {account_id (get_journal_accounts owned ID), currency, opened_at (actual ISO+zone or null; not candle time), position_status:open|partially_closed|closed|unknown, opening_balance:fact, initial_risk:fact (total original trade-idea cash risk), risk_pct:fact (0.5=half percent), usual_risk:usual|half_normal, realizations:[{event_key (stable; reuse on corrections),at:actual ISO+zone|null,amount:fact,basis:net|gross,fees:fact|null}], realized_complete:boolean (all realized P/L to date), risk_changes:[{event_key,at,amount:fact,kind:added_risk|reduced_risk|stop_change,note}]}. fact={value:decimal string|null,source:member_confirmed|estimate|awaiting_confirmation|unknown,quote:exact member words}. Unknown value=null; never claim imported verification. Use account preset ONLY on explicit usual/normal risk invocation; half size is ambiguous. Realizations are separate cash events, not cumulative totals. Net already includes fees; gross needs stated fees. Group partials/additions under the same trade idea; keep added risk separate. Never infer fills or actual cash from a chart or planned RR. result_r is member-reported only; use get_account_performance for calculated R. Stage before questions; ONE optional grouped closeout after the story; unknowns can save. Preserve explicit account on earlier trades when switching accounts. Null preserves prior values; an unknown fact retracts a number."""
PROMPT = """
ACCOUNT AND RISK JOURNALING — use the existing conversation, not a questionnaire.
Read get_journal_accounts for the member's saved account(s) and current selection.
Use update_journal_account only when they provide/confirm an account, usual risk,
switch accounts, report cash movements or reconcile a balance. Never request broker
passwords. Name, currency and balance effective time/source belong to that account.
Do not divide balances across accounts or mix currencies. Account selection applies
to NEW stories only; an earlier trade keeps its explicit account unless corrected.
In stage_journal_story/story_json (also each stage_journal_day trade.story), use
accounting for financial facts. Keep this alongside the member's story and entries.
accounting keys: account_id (owned tool ID), currency, opened_at (actual ISO with
zone; unknown if only a candle time is known), position_status (open, partially_closed,
closed, unknown), opening_balance, initial_risk, risk_pct, usual_risk, realizations,
realized_complete, risk_changes. Each monetary fact is {value:decimal string,
source:member_confirmed|estimate|awaiting_confirmation|unknown,quote:exact member words}.
Unknown value=null. Only direct confirmed member statements use member_confirmed.
Never claim imported_verified; only trusted import code can establish that source.
Use risk_pct in percent units (0.5 means half a percent). initial_risk is TOTAL
original dollar/currency risk of this trade IDEA, not one leg or later added risk.
Usual risk is only a suggestion until the member explicitly invokes 'usual risk'
or 'half my normal risk' (usual_risk=usual|half_normal), or supplies actual risk.
'Half size' does not establish risk; leave unknown. Do not silently apply a preset.
realizations: [{event_key:stable key,at:actual ISO or null,amount:fact,basis:net|gross,
fees:fact or null}]. These are separate realized cash events, NOT cumulative totals;
a correction replaces the SAME event_key. Net already includes fees: never subtract
fees twice. Gross requires stated fees (including confirmed zero) before net is known.
realized_complete=true only when the member has identified all realized P/L to date.
An open runner stays open. Closed-trade statistics exclude open and partial trades.
Risk changes: [{event_key,at,amount:fact,kind:added_risk|reduced_risk|stop_change,note}].
Use original-risk-reference R across the grouped idea. Keep additional risk separate;
never average leg R. Missing initial risk keeps calculated R unknown. Planned RR is
not realized R; targets aren't required to calculate realized R. Existing result_r
is explicitly REPORTED R only. Never write a model-calculated R there.
Let the member finish; stage facts first. Offer ONE short grouped end-of-shift closeout
for material missing account/outcome/risk/cash details. Unknown/defer is valid; keep
the draft and save when requested. Don't repeat known facts or ask after each entry.
After one save, get_account_performance provides server-calculated numbers/coverage.
Report journal-tracked realized balance, not live equity. Deposits/withdrawals/resets
and balance corrections are cash movements, never trading profits. Don't replace a
recorded balance to conceal a discrepancy: get_account_performance can compare a
reported balance first, then ask what explains the difference. No broker connection.
Preserve confirmed historical risk/balance facts; derived projections may recalculate
when backdated records are added. Use the same trade/event keys for corrections.
"""
TOOLS=[
    schema('get_journal_accounts','Read only this member’s account profiles, current selection, currencies, effective balance and usual-risk preferences. Required before selecting an account ID or changing a profile. Account/risk narration uses stage_journal_story or stage_journal_day; let them finish, one grouped closeout, unknowns save.',{}),
    schema('update_journal_account','Save a confirmed account/switch/cash movement. action=create|update|select|cash_movement; account_id from owned lookup for existing; expected_revision from latest lookup. data_json: create {name,currency,balance:fact,balance_at:ISO,usual_risk_pct:fact?,usual_risk_amount:fact?}; update {name?,usual_risk_pct?,usual_risk_amount?}; select {}; cash_movement {event_key,kind:deposit|withdrawal|reset|correction,at:ISO,amount:fact (signed balance delta),note}. Never replace seed balance after creation; compare/reconcile first. Use stable event_key for corrections/retries, not a new movement. source_text faithful current voice statement; typed text supplied by server. No hypothetical writes.',
           {'action':{'type':'string'},'account_id':STR,'expected_revision':NUM,'data_json':{'type':'string','description':'fact={value:decimal string|null,source:member_confirmed|estimate|awaiting_confirmation|unknown,quote:exact actual member words}; percent units: 0.5 means half percent. Unknown value=null. Only server imports may assert imported_verified.'},'source_text':STR}),
    schema('get_account_performance','Read verified journal-account calculations and realized-balance curve, original-risk-reference R versus reported R, coverage and uncertainties. Uses this member only. Optional interval ISO times; reported_balance compares, never overwrites records. No live equity.',
           {'account_id':STR,'start_at':STR,'end_at':STR,'reported_balance':STR}),
]


def stamp():return datetime.now(timezone.utc).isoformat()


def _rows(conn,guild,user):
    return conn.execute('SELECT * FROM journal_accounts WHERE guild_id=? AND user_id=? ORDER BY name_key,id',(guild,user)).fetchall()


def _account(row):
    return dict(json.loads(row['payload']),id=row['id'],revision=row['revision'],selected=bool(row['selected']))


def _owned(conn,guild,user,account_id):
    rows=_rows(conn,guild,user)
    selected=[r for r in rows if r['id']==account_id] if account_id else [r for r in rows if r['selected']]
    if len(selected)!=1:raise ValueError('Select one of your journal accounts; no account was assumed.')
    return _account(selected[0])


def _public(account):
    return {k:v for k,v in account.items() if k not in ('audit','operations')}


def get_accounts(db,guild,user,args):
    with journal_transaction(db,args,guild,user) as conn:
        rows=[_public(_account(r)) for r in _rows(conn,guild,user)]
    return dict(ok=True,accounts=rows,instruction='Usual risk is a suggestion until explicitly invoked for a trade.')


def _spoken_numbers(text):
    units=dict(zip('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split(),range(20)))
    units.update(dict(zip('twenty thirty forty fifty sixty seventy eighty ninety'.split(),range(20,100,10))))
    tokens=re.findall(r"[a-z]+|[0-9]+(?:\.[0-9]+)?",text.lower().replace('-',' '))
    result=[];total=0;part=0;active=False;i=0
    while i<len(tokens):
        word=tokens[i]
        if word in units:part+=units[word];active=True
        elif word=='hundred' and active:part*=100
        elif word in ('thousand','million') and active:
            total+=part*(1000 if word=='thousand' else 1000000);part=0
        elif word=='point' and active:
            digits=[];j=i+1
            while j<len(tokens) and tokens[j] in units and units[tokens[j]]<10:
                digits.append(str(units[tokens[j]]));j+=1
            if digits:result.append(Decimal(str(total+part)+'.'+''.join(digits)));i=j-1
            total=part=0;active=False
        elif word=='and' and active:pass
        else:
            if active:result.append(Decimal(total+part))
            total=part=0;active=False
            if word=='half':result.append(Decimal('.5'))
            if word=='quarter':result.append(Decimal('.25'))
        i+=1
    if active:result.append(Decimal(total+part))
    return result


def _ground(value,text):
    """Require original words for new numbers; no model-supplied verification."""
    if isinstance(value,dict):
        if 'source' in value and 'value' in value and value['value'] is not None:
            quote=value.get('quote')
            if not isinstance(quote,str) or quote not in text:
                raise ValueError('The amount’s quote must appear in the actual member narration.')
            # Ground numerals and ordinary spoken cardinals in the actual passage.
            values=_spoken_numbers(quote)
            for token in re.findall(r'[-+]?\d[\d,]*(?:\.\d+)?',quote):
                try:values.append(calc.number(token.replace(',','')))
                except ValueError:pass
            wanted=calc.number(value['value'])
            if wanted not in values and abs(wanted) not in values:
                raise ValueError('Confirm the numeric reading of that amount; the original words remain in the journal.')
        for item in value.values():_ground(item,text)
    elif isinstance(value,list):
        for item in value:_ground(item,text)


def update_account(db,guild,user,args):
    cap=binding(args)
    if cap is None:raise ValueError('Use an authenticated conversation to update accounts.')
    source=getattr(cap.context,'_client_text',None)
    source=source if source is not None else args.get('source_text')
    if not isinstance(source,str) or not source.strip() or len(source)>16000:
        raise ValueError('Retain the member’s actual account instruction.')
    if re.search(r'\b(mock|hypothetical|pretend|example)\b|\b(?:do not|don.t)\s+(?:save|record)',source,re.I):
        return dict(ok=True,saved=False,persisted=False,status='preview_only')
    data=json.loads(args['data_json'])
    if not isinstance(data,dict) or len(json.dumps(data))>12000:raise ValueError('Use a bounded account update.')
    _ground(data,source)
    action=args['action']
    with journal_transaction(db,args,guild,user,serialize=True) as conn:
        if action=='create':
            if set(data)-{'name','currency','balance','balance_at','usual_risk_pct','usual_risk_amount'}:
                raise ValueError('Unknown account profile fields.')
            name=data.get('name','').strip()
            if not name or len(name)>100 or not re.fullmatch('[A-Z]{3}',data.get('currency','')):
                raise ValueError('Provide an account name and three-letter currency.')
            payload=dict(name=name,currency=data['currency'],balance=calc.fact(data['balance']),
                balance_at=calc.instant(data['balance_at']),cash_movements=[],audit=[],operations=[])
            for key in ('usual_risk_pct','usual_risk_amount'):
                payload[key]=calc.fact(data.get(key),positive=True)
            if payload.get('usual_risk_pct') and payload['usual_risk_pct']['value'] is not None and calc.number(payload['usual_risk_pct']['value'])>100:
                raise ValueError('Usual risk percentage cannot exceed 100.')
            old=next((r for r in _rows(conn,guild,user) if r['name_key']==name.casefold()),None)
            if old:
                existing=_account(old)
                if all(existing.get(k)==v for k,v in payload.items() if k not in ('audit','operations')):
                    return dict(ok=True,saved=True,account=_public(existing),replayed=True)
                raise ValueError('That account already exists. Read its revision and correct the same profile.')
            account_id=uuid4().hex
            conn.execute('UPDATE journal_accounts SET selected=0 WHERE guild_id=? AND user_id=?',(guild,user))
            conn.execute('INSERT INTO journal_accounts(id,guild_id,user_id,name_key,payload,revision,selected,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                (account_id,guild,user,name.casefold(),json.dumps(payload),1,1,stamp()))
            account=dict(payload,id=account_id,revision=1,selected=True)
        else:
            account=_owned(conn,guild,user,args.get('account_id'))
            digest=sha256(json.dumps([action,data,args.get('expected_revision')],sort_keys=True).encode()).hexdigest()
            if digest in account.get('operations',[]):
                return dict(ok=True,saved=True,account=_public(account),replayed=True)
            if type(args.get('expected_revision')) is not int or args['expected_revision']!=account['revision']:
                raise ValueError('This account changed. Read its current revision before correcting it.')
            prior=deepcopy(account)
            if action=='select':
                if data:raise ValueError('Account selection needs no profile changes.')
                conn.execute('UPDATE journal_accounts SET selected=0 WHERE guild_id=? AND user_id=?',(guild,user))
                account['selected']=True
            elif action=='update':
                if set(data)-{'name','usual_risk_pct','usual_risk_amount'}:
                    raise ValueError('Seed balance/currency are historical facts. Reconcile with a separate cash movement or account.')
                for key,v in data.items():
                    if key=='name':
                        if not isinstance(v,str) or not v.strip() or len(v)>100:raise ValueError('Use a bounded account name.')
                        account[key]=v.strip()
                    else:
                        account[key]=calc.fact(v,positive=True)
                pct=account.get('usual_risk_pct')
                if pct and pct['value'] is not None and calc.number(pct['value'])>100:
                    raise ValueError('Usual risk percentage cannot exceed 100.')
                if any(r['id']!=account['id'] and r['name_key']==account['name'].casefold() for r in _rows(conn,guild,user)):
                    raise ValueError('That account name already belongs to another of your accounts.')
            elif action=='cash_movement':
                if set(data)-{'event_key','kind','at','amount','note'} or not re.fullmatch('[a-zA-Z0-9_-]{1,64}',str(data.get('event_key',''))):
                    raise ValueError('Use a stable cash movement key and documented fields.')
                if data.get('kind') not in ('deposit','withdrawal','reset','correction'):
                    raise ValueError('Separate deposits, withdrawals, resets and balance corrections from profit.')
                event=dict(data,at=calc.instant(data['at']),amount=calc.fact(data['amount']))
                if event['at']<=account['balance_at']:raise ValueError('The seed already covers cash at or before its effective time.')
                amount=calc.number(event['amount']['value']) if event['amount']['value'] is not None else None
                if amount is not None and ((data['kind']=='deposit' and amount<0) or (data['kind']=='withdrawal' and amount>0)):
                    raise ValueError('Deposits increase balance; withdrawals decrease it.')
                events={e['event_key']:e for e in account['cash_movements']}
                events[event['event_key']]=event
                account['cash_movements']=list(events.values())
            else:raise ValueError('Unknown account action.')
            account['audit'].append(dict(at=stamp(),action=action,before={k:v for k,v in prior.items() if k not in ('audit','operations')},source_text=source))
            account['operations']=(account.get('operations',[])+[digest])[-100:]
            account['revision']+=1
            payload={k:v for k,v in account.items() if k not in ('id','revision','selected')}
            conn.execute('UPDATE journal_accounts SET name_key=?,payload=?,revision=?,selected=?,updated_at=? WHERE id=? AND guild_id=? AND user_id=?',
                (account['name'].casefold(),json.dumps(payload),account['revision'],int(account['selected']),stamp(),account['id'],guild,user))
    from gbop_voice_web.voice_runtime import journal_write_committed
    result=dict(ok=True,saved=True,account=_public(account))
    journal_write_committed(guild,user,result)
    return result


def prepare_accounting(conn,guild,user,previous,patch,source):
    """Bind account/preset now, not retroactively when a profile later changes."""
    _ground(patch,source or '')
    data=calc.merge(previous,patch)
    if not data:return data
    account_id=data.get('account_id')
    if not account_id:
        selected=[r for r in _rows(conn,guild,user) if r['selected']]
        if not selected:return data  # Missing account must not block the draft.
        account_id=selected[0]['id']
        data['account_id']=account_id
    account=_owned(conn,guild,user,account_id)
    if data.get('currency') not in (None,account['currency']):raise ValueError('That account uses a different currency; no conversion was assumed.')
    data['currency']=account['currency']
    if patch.get('usual_risk'):
        phrase=patch['usual_risk']
        pattern=r'\b(?:usual|normal|standard)\s+risk\b'
        if not re.search(pattern,source or '',re.I):raise ValueError('Use a saved risk preference only when the member explicitly invokes usual/normal risk, not half size.')
        multiplier=Decimal('.5') if phrase=='half_normal' else Decimal(1)
        if phrase=='half_normal' and not re.search(r'\bhalf\b',source or '',re.I):raise ValueError('Half normal risk must be explicitly stated.')
        presets=[(k,account.get(k)) for k in ('usual_risk_pct','usual_risk_amount') if calc.confirmed(account.get(k)) is not None]
        if len(presets)!=1:raise ValueError('Confirm which usual risk basis to use; it is missing or has both percent and cash.')
        key,preset=presets[0]
        dest='risk_pct' if key=='usual_risk_pct' else 'initial_risk'
        other='initial_risk' if dest=='risk_pct' else 'risk_pct'
        if other in patch and calc.confirmed(patch[other]) is not None:raise ValueError('Specify one actual original risk basis, not competing defaults.')
        data[dest]=dict(value=calc.decimal_text(calc.confirmed(preset)*multiplier),source='member_confirmed',quote=source)
        data.pop(other,None)
    return calc.validate(data,trusted=True)


def performance_on_connection(conn,guild,user,args):
    account=_owned(conn,guild,user,args.get('account_id'))
    from gbop_voice_web.journal_numbers import journal_display
    display={r['id']:r for r in journal_display(conn,guild,user)}
    rows=conn.execute('''SELECT j.id,j.result_r,d.metadata FROM journals j
        JOIN journal_details d ON d.journal_id=j.id AND d.guild_id=j.guild_id AND d.user_id=j.user_id
        WHERE j.guild_id=? AND j.user_id=? ORDER BY j.id''',(guild,user)).fetchall()
    trades=[]
    unassigned=0
    for row in rows:
        if not display.get(row['id'],{}).get('canonical'):continue
        meta=json.loads(row['metadata'] or '{}')
        if meta.get('kind') not in (None,'trade'):continue
        story=meta.get('journal_story') or '{}'
        story=json.loads(story) if isinstance(story,str) else story
        if not (story.get('accounting') or {}).get('account_id'):unassigned+=1
        trades.append(dict(id=row['id'],trade_number=display[row['id']]['trade_number'],accounting=story.get('accounting'),reported_r=row['result_r']))
    result=calc.project(account,trades,start_at=args.get('start_at'),end_at=args.get('end_at'))
    result['unassigned_journals']=unassigned
    result['coverage_note']='Only explicitly assigned account journals are included. Older/unassigned journals are retained, not silently assigned.'
    if args.get('reported_balance') is not None:
        reported=calc.number(args['reported_balance'])
        result['reconciliation']=dict(reported_balance=calc.decimal_text(reported),difference=calc.decimal_text(reported-calc.number(result['closing_balance'])) if result['closing_balance'] is not None else None,
            instruction='Compare cash movements, fees, missing trades and timing; do not overwrite a balance to hide the difference.')
    return dict(ok=True,**result)


def get_performance(db,guild,user,args):
    with journal_transaction(db,args,guild,user) as conn:
        return performance_on_connection(conn,guild,user,args)
