// Public-safe browser regression harness. No real DOM, media, network, or provider calls.
const fs=require('fs'),vm=require('vm'),path=require('path'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[2] || path.join(__dirname,'../gbop_voice_web/static/app.js'),'utf8');
function environment({failSession=false,deferDelivered=false,manualReceiptTimeout=false}={}) {
 const requests=[],channels=[],connections=[],receiptResolvers=[];let seq=0,receiptTimeout;
 const el=()=>({textContent:'',disabled:false,dataset:{},classList:{add(){},remove(){}},addEventListener(){},querySelector(){return null},appendChild(){},append(){},play(){return Promise.resolve()}});
 const els=new Map();
 const context={console:{...console,error(){}},setTimeout:(callback,ms)=>manualReceiptTimeout&&ms===5000?(receiptTimeout=callback,undefined):setTimeout(callback,ms),clearTimeout,Date,location:{hostname:'localhost'},window:{isSecureContext:true},document:{getElementById(id){if(!els.has(id))els.set(id,el());return els.get(id)},createElement:el},navigator:{mediaDevices:{getUserMedia:async()=>({getAudioTracks:()=>[],getTracks:()=>[]})}},fetch:async(url,opts)=>{
  requests.push({url,body:opts?.body?JSON.parse(url==='/api/live/session'?'{}':opts.body):null});
  if(url==='/api/me')return{json:async()=>({authenticated:false})};
  if(url==='/api/live/context/delivered')return deferDelivered?new Promise(resolve=>receiptResolvers.push(resolve)):{ok:true,json:async()=>({ok:true})};
  if(url==='/api/live/session')return failSession?{ok:false,text:async()=> 'mock session failure'}:{ok:true,json:async()=>({session_id:'s'+(++seq),sdp:'answer'})};
  return {ok:true,json:async()=>({result:'stub'})};
 },RTCPeerConnection:class{constructor(){this.iceGatheringState='complete';this.localDescription={sdp:'sdp'};this.closed=false;connections.push(this)}addTrack(){}createDataChannel(){let c={readyState:'open',events:{},sent:[],addEventListener(n,f){this.events[n]=f},send(v){this.sent.push(JSON.parse(v))}};channels.push(c);return c}async createOffer(){return{}}async setLocalDescription(){}async setRemoteDescription(){}close(){this.closed=true}}};
 vm.createContext(context);vm.runInContext(source,context);
 return{context,requests,channels,connections,els,expireReceipt:()=>receiptTimeout(),finishReceipt:(ok=true)=>receiptResolvers.shift()({ok,json:async()=>({ok})}),run:code=>vm.runInContext(code,context),event:async(i,data)=>await channels[i].events.message({data:JSON.stringify(data)})};
}
(async()=>{
 const e=environment();await e.run('startVoice()');await e.event(0,{type:'session.started',session:{id:'s1'}});e.run('cleanup()');await e.run('startVoice()');await e.event(1,{type:'session.started',session:{id:'s2'}});e.requests.length=0;
 await e.event(0,{type:'session.input_audio.speech_started'});
 await e.event(0,{type:'session.delegation.created',delegation:{id:'old-delegation',target:'client'}});
 await e.event(0,{type:'session.started',session:{id:'s1'}});
 await e.event(0,{type:'session.closed'});
 e.channels[0].events.close();e.connections[0].ontrack({streams:['stale stream']});
 assert.equal(e.requests.length,0,'Old-source callbacks must not cancel/delegate/close current session');
 assert.equal(e.run('sessionId'),'s2','Old session.started cannot overwrite new session');
 assert.equal(e.run('connected'),true,'Old close event cannot disconnect new session');
 assert.equal(e.connections[1].closed,false,'New peer remains open');
 assert.notEqual(e.els.get('remoteAudio').srcObject,'stale stream');
 console.log('PASS: old-channel callbacks ignored after reconnect');
 e.connections[1].ontrack({streams:['current stream']});await Promise.resolve();
 e.els.get('remoteAudio').currentTime=1;
 await e.event(1,{type:'session.output_audio.started'});
 await e.event(1,{type:'session.output_transcript.delta',delta:'9ate8 failed. Young Lefty failed.'});
 e.els.get('remoteAudio').currentTime=2;
 await e.event(1,{type:'session.output_audio.stopped'});
 let receipts=e.requests.filter(r=>r.url==='/api/live/context/delivered');
 assert.equal(receipts.length,1,'Completed output gets one discussion receipt');
 assert.equal(receipts[0].body.session_id,'s2');
 assert.equal(receipts[0].body.text,'9ate8 failed. Young Lefty failed.');
 await e.event(1,{type:'session.output_audio.stopped'});
 assert.equal(e.requests.filter(r=>r.url==='/api/live/context/delivered').length,1,'Duplicate stop cannot repeat receipt');
 await e.event(1,{type:'session.input_audio.speech_started'});
 await new Promise(resolve=>setImmediate(resolve));
 const ordinary=e.requests.filter(r=>r.url==='/api/live/context/cancel').at(-1).body;
 assert.equal(ordinary.continuation,true,'Ordinary speech after completed playback can answer its clarification');
 assert.equal(ordinary.reply_to_response_id,receipts[0].body.response_id,'Continuation identifies exactly the completed response');
 await e.event(1,{type:'session.delegation.created',delegation:{id:'reply',target:'client'}});
 const delegated=e.requests.filter(r=>r.url==='/api/delegate').at(-1).body;
 assert.equal(delegated.continuation,ordinary.continuation,'Delegate carries fence even when cancel arrives later');
 assert.equal(delegated.reply_to_response_id,ordinary.reply_to_response_id);
 await e.event(1,{type:'session.output_audio.started'});
 await e.event(1,{type:'session.output_transcript.delta',delta:'The 9 AM range delivered'});
 await e.event(1,{type:'session.input_audio.speech_started'});
 assert.equal(e.requests.filter(r=>r.url==='/api/live/context/cancel').at(-1).body.continuation,false,'Interrupted playback must discard clarification context');
 await e.event(1,{type:'session.delegation.created',delegation:{id:'interrupted-reply',target:'client'}});
 assert.equal(e.requests.filter(r=>r.url==='/api/delegate').at(-1).body.continuation,false,'Delegate cannot revive an interrupted question');
 await e.event(1,{type:'session.output_audio.stopped'});
 assert.equal(e.requests.filter(r=>r.url==='/api/live/context/delivered').length,1,'Interrupted output cannot mark all generated text discussed');
 await e.event(0,{type:'session.output_audio.started'});
 await e.event(0,{type:'session.output_transcript.delta',delta:'The 10 AM range failed'});
 await e.event(0,{type:'session.output_audio.stopped'});
 assert.equal(e.requests.filter(r=>r.url==='/api/live/context/delivered').length,1,'Replaced connection cannot acknowledge old output');
 e.run('remotePlaybackReady=false');
 await e.event(1,{type:'session.output_audio.started'});
 await e.event(1,{type:'session.output_transcript.delta',delta:'The 10 AM range failed'});
 e.els.get('remoteAudio').currentTime=3;
 await e.event(1,{type:'session.output_audio.stopped'});
 assert.equal(e.requests.filter(r=>r.url==='/api/live/context/delivered').length,1,'Blocked playback cannot mark generated text as discussed');
 await e.event(1,{type:'session.input_audio.speech_started'});
 assert.equal(e.requests.filter(r=>r.url==='/api/live/context/cancel').at(-1).body.continuation,false,'Blocked playback cannot carry an older clarification');
 console.log('PASS: only current uninterrupted output is acknowledged');
 for (const outcome of ['accepted','rejected','timed_out','interrupted','reconnected']) {
  const r=environment({deferDelivered:true,manualReceiptTimeout:true});await r.run('startVoice()');
  await r.event(0,{type:'session.started',session:{id:'s1'}});
  r.connections[0].ontrack({streams:['stream']});await Promise.resolve();
  r.els.get('remoteAudio').currentTime=1;
  await r.event(0,{type:'session.output_audio.started'});
  await r.event(0,{type:'session.output_transcript.delta',delta:'Should I save this as a mid-trade feeling for Trade #1?'});
  r.els.get('remoteAudio').currentTime=2;
  await r.event(0,{type:'session.output_audio.stopped'});
  await r.event(0,{type:'session.input_audio.speech_started'});
  const pending=r.event(0,{type:'session.delegation.created',delegation:{id:'immediate-yes',target:'client'}});
  assert.equal(r.requests.filter(x=>x.url==='/api/live/context/cancel').length,0,'Immediate continuation waits for delivery acknowledgement');
  assert.equal(r.requests.filter(x=>x.url==='/api/delegate').length,0,'Delegation cannot overtake the acknowledgement');
  if(outcome==='interrupted')await r.event(0,{type:'session.input_audio.speech_started'});
  if(outcome==='reconnected'){r.run('cleanup()');await r.run('startVoice()');}
  if(outcome==='interrupted'||outcome==='reconnected') {
   const fence=r.requests.filter(x=>x.url==='/api/live/context/cancel').at(-1).body;
   assert.equal(fence.continuation,false,'New speech or close fences immediately even with an outstanding receipt');
  }
  if(outcome==='timed_out')r.expireReceipt();
  else r.finishReceipt(outcome!=='rejected');
  await pending;
  await new Promise(resolve=>setImmediate(resolve));
  const cancels=r.requests.filter(x=>x.url==='/api/live/context/cancel');
  const delegates=r.requests.filter(x=>x.url==='/api/delegate');
  if(outcome==='accepted'||outcome==='rejected'||outcome==='timed_out') {
   assert.equal(cancels.length,1);assert.equal(delegates.length,1);
   assert.equal(cancels[0].body.continuation,outcome==='accepted');
   assert.equal(delegates[0].body.continuation,outcome==='accepted');
   assert.equal(delegates[0].body.reply_to_response_id,cancels[0].body.reply_to_response_id);
   if(outcome==='timed_out'){r.finishReceipt(true);await new Promise(resolve=>setImmediate(resolve));assert.equal(r.requests.filter(x=>x.url==='/api/delegate').length,1,'A late receipt cannot revive timed-out confirmation');}
  } else {
   assert.equal(cancels.length,1,'Stale pending continuation cannot emit a late fence');
   assert.equal(delegates.length,0,'Stale pending continuation cannot delegate');
  }
 }
 console.log('PASS: immediate replies wait for receipts; failure, interruption and reconnect fail closed');
 const failed=environment({failSession:true});await failed.run('startVoice()');
 assert.equal(failed.els.get('orb').disabled,false,'Failed connection must re-enable retry button');
 console.log('PASS: session failure leaves retry button usable');
})().catch(e=>{console.error(e);process.exit(1)});
