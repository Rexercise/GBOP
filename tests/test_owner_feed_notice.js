// No network or browser needed: execute the real owner-notice functions.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const text = fs.readFileSync('gbop_voice_web/static/app.js', 'utf8');
const start = text.indexOf('let ownerFeedGeneration =');
const end = text.indexOf('\nlet pc =', start);
assert(start >= 0 && end > start);
const requests = [];
let hidden = true;
const els = {
  ownerFeedCard: { classList: { add: () => { hidden = true; }, remove: () => { hidden = false; } } },
  ownerFeedStatus: { textContent: '' }, ownerFeedCheck: { addEventListener: () => {} },
};
const context = vm.createContext({els, Date, Error,
  authFetch: () => new Promise(resolve => requests.push(resolve)),
});
vm.runInContext(text.slice(start, end), context);
const flush = () => new Promise(resolve => setImmediate(resolve));
const response = notice => ({ok:true,json:async()=>({ok:true,notice,checked_at:2000000000})});
(async () => {
  vm.runInContext('setOwnerFeedAccess(false)',context);
  assert.equal(requests.length,0);assert(hidden);
  vm.runInContext('setOwnerFeedAccess(true)',context);
  vm.runInContext('refreshOwnerFeedHealth()',context);
  requests[1](response('New stale warning'));await flush();
  requests[0](response('Old healthy result'));await flush();
  assert(els.ownerFeedStatus.textContent.startsWith('New stale warning'));
  assert(els.ownerFeedStatus.textContent.includes('2033-05-18 03:33:20 UTC'));
  vm.runInContext('refreshOwnerFeedHealth();setOwnerFeedAccess(false)',context);
  requests[2](response('Private owner notice'));await flush();
  assert(hidden);assert.equal(els.ownerFeedStatus.textContent,'');
  vm.runInContext('setOwnerFeedAccess(true)',context);
  requests[3]({ok:false,json:async()=>({ok:false})});await flush();
  assert(els.ownerFeedStatus.textContent.includes('unverified'));
  console.log('Owner notice: member exclusion, out-of-order responses, logout race, failure state PASS');
})().catch(err=>{console.error(err);process.exitCode=1;});
