/* Read-only account view. All amounts and curve points come from the server. */
(() => {
  const panel = document.getElementById('journalPerformance');
  const select = document.getElementById('journalAccount');
  const summary = document.getElementById('journalSummary');
  const coverage = document.getElementById('journalCoverage');
  const curve = document.getElementById('journalCurve');
  const metrics = document.getElementById('journalMetrics');
  let generation = 0;
  const amount = value => value === null ? 'unknown' : Number(value).toLocaleString(undefined, {maximumFractionDigits: 2});
  function clear() {
    generation++;
    select.replaceChildren();
    curve.replaceChildren(); curve.hidden = true;
    summary.textContent = ''; coverage.textContent = '';
    metrics.replaceChildren();
  }
  window.addEventListener('gbop-account-logout', clear);
  async function refresh(reloadAccounts = false) {
    const ticket = ++generation;
    summary.textContent = 'Loading your journal results…';
    metrics.replaceChildren(); coverage.textContent = '';
    curve.hidden = true;
    try {
      if (reloadAccounts || !select.options.length) {
        const response = await authFetch('/api/journal/accounts');
        if (!response.ok) throw new Error('accounts unavailable');
        const data = await response.json();
        if (ticket !== generation) return;
        select.replaceChildren();
        for (const account of data.accounts) {
          const option = document.createElement('option');
          option.value = account.id;
          option.textContent = `${account.name} · ${account.currency}`;
          option.selected = account.selected;
          select.appendChild(option);
        }
        if (!data.accounts.length) {
          summary.textContent = 'Tell GBOP your account name, currency and starting balance when you’re ready.';
          coverage.textContent = 'Your journals can still be saved without account numbers.';
          return;
        }
      }
      const response = await authFetch('/api/journal/performance?account_id=' + encodeURIComponent(select.value));
      if (!response.ok) throw new Error('performance unavailable');
      const data = await response.json();
      if (ticket !== generation) return;
      const r = data.r_coverage;
      summary.textContent = `${data.currency} · ${data.completed_trade_count} completed · ${data.open_trade_count} open. ${data.balance_complete ? 'Based on recorded realized cash.' : 'Some account figures are incomplete; unknowns stay unknown.'}`;
      for (const [label,value] of [['Opening balance',amount(data.opening_balance)],['Journal balance',amount(data.closing_balance)],['Net realized P/L',amount(data.net_trading_pnl)],['Realized return',data.return_pct===null?'unknown':amount(data.return_pct)+'%']]) {
        const group=document.createElement('div'),term=document.createElement('dt'),detail=document.createElement('dd');
        term.textContent=label;detail.textContent=value;group.append(term,detail);metrics.appendChild(group);
      }
      coverage.textContent = `Calculated R ${amount(data.calculated_r_total)} (${r.calculated}/${r.total} completed trades). Reported-only R ${r.reported_only ? amount(data.reported_r_total) : 'none'} (${r.reported_only} trades). R unknown: ${r.unknown}. Win rate ${amount(data.win_rate_pct)}% from ${data.win_rate_coverage} known completed outcomes. ${data.unassigned_journals} journals unassigned to an account. ${data.uncertainties.length ? data.uncertainties.join('; ')+'. ' : ''}Journal-tracked realized balance, not live broker equity; open unrealized P/L is excluded.`;
      curve.replaceChildren();
      if (data.balance_complete && data.curve.length) {
        const points = data.curve.map(p => ({t: Date.parse(p.at), y: Number(p.balance)}));
        const minX = Math.min(...points.map(p => p.t)), maxX = Math.max(...points.map(p => p.t));
        const minY = Math.min(...points.map(p => p.y)), maxY = Math.max(...points.map(p => p.y));
        const line = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
        line.setAttribute('points', points.map(p => `${30 + (p.t-minX)/(maxX-minX || 1)*460},${150-(p.y-minY)/(maxY-minY || 1)*120}`).join(' '));
        line.setAttribute('fill','none'); line.setAttribute('stroke','#b7e49a'); line.setAttribute('stroke-width','3');
        curve.appendChild(line);
        for (const [y, value] of [[20,maxY],[174,minY]]) {
          const label = document.createElementNS('http://www.w3.org/2000/svg','text');
          label.setAttribute('x','30');label.setAttribute('y',String(y));label.setAttribute('fill','currentColor');label.setAttribute('font-size','12');
          label.textContent = `${data.currency} ${amount(String(value))}`;curve.appendChild(label);
        }
        curve.hidden = false;
      }
    } catch (_) {
      if (ticket !== generation) return;
      summary.textContent = 'Journal results could not be loaded. Your saved journals are unchanged.';
      coverage.textContent = '';curve.replaceChildren();curve.hidden = true;
    }
  }
  panel.addEventListener('toggle', () => {if (panel.open) refresh(true);});
  select.addEventListener('change', () => refresh());
  document.getElementById('journalRefresh').addEventListener('click', () => refresh(true));
})();
