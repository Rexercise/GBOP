# Account-aware conversational journaling

Members can name/select an account and report a starting balance and its effective time. Percent or cash risk preferences stay suggestions until explicitly invoked for a trade. Existing journals remain unassigned unless the member supplies an account association.

The existing daily/single-story tools accept financial facts alongside entries and feelings. Missing numbers are valid. Capture the story first, offer one optional grouped closeout, and finalize only on the existing explicit save request. The same trade/event keys support corrections without extra journals or duplicate cash events.

Calculations use Decimal, not model arithmetic. Calculated R is net realized cash divided by original trade-idea risk. Reported R stays separate. Added risk and stop changes are disclosed separately; open runners do not count as completed trades. Net P/L includes fees once; gross P/L requires known fees. Estimates and unknowns do not enter confirmed balance or R totals.

Balances follow dated realized cash and separate deposits, withdrawals, resets and corrections. Backdated records recalculate derived opening balances; explicit confirmed snapshots remain facts. Return links realized-balance subperiod returns around external cashflows. It is not the sum of R and excludes open unrealized P/L. Missing/untimed cash prevents a false exact balance/curve. Reconciliation reports discrepancies without overwriting history.

The voice page's My journal performance panel reads authenticated, member-scoped endpoints. Account selection there only changes the displayed account; changing the active journaling account is an explicit conversation/tool action. There is no broker connection or live equity claim.

Security: journal_accounts has RLS and no anon/authenticated grants. The existing server adapter enforces guild/member scope and rechecks membership; mutations also use the existing conversation-generation fence, member advisory lock, revision checks and retry receipts. All financial facts persist atomically inside the existing journal story. Account profile changes have their own audit trail.

Validation: 2,958-test regression run passed (one existing skip); 18 financial acceptance tests; 14 real PostgreSQL tests passed, including fresh-connection recall, corrections/retries, account isolation and actual RLS/privileges. PostgreSQL was a disposable local 17.11 cluster, not production. The synthetic browser preview verified the balance chart and figures. Live deployment, live authenticated UI and actual spoken journaling are separately verified/reported at release time.

Example: 10,000 at 0.5% risks 50; +150 gives 3R. The next trade risks 50.75; -50.75 gives -1R. Closing balance is 10,099.25, total calculated R is 2 and realized return is 0.9925%.
