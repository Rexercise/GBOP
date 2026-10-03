// Public-safe browser regression harness. No real DOM, media, network, or provider calls.
const fs=require('fs'),vm=require('vm'),path=require('path'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[2] || path.join(__dirname,'../gbop_voice_web/static/app.js'),'utf8');
function environment({failSession=false}={}) {
 const requests=[],channels=[],connections=[];let seq=0;
 const el=()=>({textContent:'',disabled:false,dataset:{},classList:{add(){},remove(){}},addEventListener(){},querySelector(){return null},appendChild(){},append(){},play(){return Promise.resolve()}});
 const els=new Map();
 const context={console:{...console,error(){}},setTimeout,Date,location:{hostname:'localhost'},window:{isSecureContext:true},document:{getElementById(id){if(!els.has(id))els.set(id,el());return els.get(id)},createElement:el},navigator:{mediaDevices:{getUserMedia:async()=>({getAudioTracks:()=>[],getTracks:()=>[]})}},fetch:async(url,opts)=>{
  requests.push({url,body:opts?.body?JSON.parse(url==='/api/live/session'?'{}':opts.body):null});
  if(url==='/api/me')return{json:async()=>({authenticated:false})};
  if(url==='/api/live/session')return failSession?{ok:false,text:async()=> 'mock session failure'}:{ok:true,json:async()=>({session_id:'s'+(++seq),sdp:'answer'})};
  return {ok:true,json:async()=>({result:'stub'})};
 },RTCPeerConnection:class{constructor(){this.iceGatheringState='complete';this.localDescription={sdp:'sdp'};this.closed=false;connections.push(this)}addTrack(){}createDataChannel(){let c={readyState:'open',events:{},sent:[],addEventListener(n,f){this.events[n]=f},send(v){this.sent.push(JSON.parse(v))}};channels.push(c);return c}async createOffer(){return{}}async setLocalDescription(){}async setRemoteDescription(){}close(){this.closed=true}}};
 vm.createContext(context);vm.runInContext(source,context);
 return{context,requests,channels,connections,els,run:code=>vm.runInContext(code,context),event:async(i,data)=>await channels[i].events.message({data:JSON.stringify(data)})};
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
 const failed=environment({failSession:true});await failed.run('startVoice()');
 assert.equal(failed.els.get('orb').disabled,false,'Failed connection must re-enable retry button');
 console.log('PASS: session failure leaves retry button usable');
})().catch(e=>{console.error(e);process.exit(1)});
