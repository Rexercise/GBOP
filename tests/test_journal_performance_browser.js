// Run the real account view with controlled responses; no member data or network.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('gbop_voice_web/static/journal-performance.js','utf8');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function setup() {
  const requests=[],nodes=new Map(),window=new EventTarget();
  function node() {
    return {textContent:'',children:[],events:{},hidden:false,value:'',
      get options(){return this.children;},
      replaceChildren(){this.children=[];this.value='';},
      appendChild(child){this.children.push(child);if(child.value&&(!this.value||child.selected))this.value=child.value;},
      append(...children){children.forEach(child=>this.appendChild(child));},
      setAttribute(){},addEventListener(name,callback){this.events[name]=callback;}};
  }
  const document={getElementById(id){if(!nodes.has(id))nodes.set(id,node());return nodes.get(id);},createElement:node,createElementNS:node};
  vm.runInNewContext(source,{document,window,authFetch:url=>new Promise(resolve=>requests.push({url,resolve})),Date,Number,Math,Error,encodeURIComponent});
  const respond=async(data,ok=true)=>{requests.shift().resolve({ok,json:async()=>data});await flush();};
  return {nodes,window,requests,respond,open(){const p=nodes.get('journalPerformance');p.open=true;p.events.toggle();}};
}
const accounts={accounts:[{id:'a1',name:'Private account',currency:'USD',selected:true}]};
const result={currency:'USD',completed_trade_count:2,open_trade_count:0,balance_complete:true,
  opening_balance:'10000',closing_balance:'10099.25',net_trading_pnl:'99.25',return_pct:'0.9925',
  calculated_r_total:'2',reported_r_total:null,r_coverage:{calculated:2,total:2,reported_only:0,unknown:0},
  win_rate_pct:'50',win_rate_coverage:2,unassigned_journals:0,uncertainties:[],
  curve:[{at:'2026-11-01T00:00:00Z',balance:'10000'},{at:'2026-11-02T12:00:00Z',balance:'10150'},{at:'2026-11-03T12:00:00Z',balance:'10099.25'}]};
(async()=>{
  const e=setup();e.open();await e.respond(accounts);await e.respond(result);
  assert.equal(e.nodes.get('journalMetrics').children.length,4);
  assert.equal(e.nodes.get('journalCurve').hidden,false);
  assert.match(e.nodes.get('journalCoverage').textContent,/Calculated R 2/);
  assert.match(e.nodes.get('journalCoverage').textContent,/not live broker equity/);
  e.nodes.get('journalRefresh').events.click();await e.respond(accounts);
  e.window.dispatchEvent(new Event('gbop-account-logout'));
  assert.equal(e.nodes.get('journalAccount').options.length,0);
  assert.equal(e.nodes.get('journalMetrics').children.length,0);
  assert.equal(e.nodes.get('journalSummary').textContent,'');
  await e.respond(result);
  assert.equal(e.nodes.get('journalMetrics').children.length,0,'Late performance cannot restore signed-out data');
  assert.equal(e.nodes.get('journalCurve').hidden,true);
  e.open();e.window.dispatchEvent(new Event('gbop-account-logout'));await e.respond(accounts);
  assert.equal(e.nodes.get('journalAccount').options.length,0,'Late account list cannot restore signed-out names');
  assert.equal(e.requests.length,0,'Stale account response cannot issue a performance request');
  e.open();await e.respond(accounts);await e.respond({...result,balance_complete:false,closing_balance:null,net_trading_pnl:null,return_pct:null,uncertainties:['Missing cash amount']});
  const values=e.nodes.get('journalMetrics').children.map(x=>x.children[1].textContent);
  assert.deepEqual(values.slice(1),['unknown','unknown','unknown']);
  assert.equal(e.nodes.get('journalCurve').hidden,true,'Incomplete balances must not draw an exact curve');
  console.log('PASS: account figures render, unknowns remain unknown, logout fences delayed private responses');
})().catch(err=>{console.error(err);process.exitCode=1;});
