// Minimal raw-CDP driver for a debug Chrome (Node >= 22, global WebSocket, no npm dependencies).
// Playwright cannot attach to every Chrome build/profile, raw DevTools protocol always works.
const http = require('http');
const HOST = process.env.CDP_HOST || '127.0.0.1';
const PORT = parseInt(process.env.CDP_PORT || '9222', 10);
function req(method, path) {
  return new Promise((res, rej) => {
    const r = http.request({host:HOST, port:PORT, path, method}, resp => {
      let b=''; resp.on('data', d=>b+=d); resp.on('end', ()=>{ try{res(JSON.parse(b))}catch(e){res(b)} });
    }); r.on('error', rej); r.end();
  });
}
class Tab {
  constructor(info){ this.info=info; this.id=0; this.pending=new Map(); }
  static async open(url='about:blank'){
    const info = await req('PUT', '/json/new?'+encodeURIComponent(url));
    const t = new Tab(info);
    await new Promise((res,rej)=>{ t.ws=new WebSocket(info.webSocketDebuggerUrl); t.ws.onopen=res; t.ws.onerror=rej;
      t.ws.onmessage=(m)=>{ const d=JSON.parse(m.data); if(d.id&&t.pending.has(d.id)){ t.pending.get(d.id)(d); t.pending.delete(d.id);} }; });
    await t.send('Page.enable'); await t.send('Runtime.enable');
    return t;
  }
  send(method, params={}){ const id=++this.id; return new Promise(res=>{ this.pending.set(id,res); this.ws.send(JSON.stringify({id,method,params})); }); }
  async nav(url){ await this.send('Page.navigate',{url}); }
  async evaluate(expr){ const r=await this.send('Runtime.evaluate',{expression:expr,returnByValue:true,awaitPromise:true}); if(r.result&&r.result.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0,300)); return r.result&&r.result.result?r.result.result.value:undefined; }
  async html(){ return this.evaluate('document.documentElement.outerHTML'); }
  async text(){ return this.evaluate('document.body?document.body.innerText:""'); }
  async shot(){ const r=await this.send('Page.captureScreenshot',{format:'png'}); return Buffer.from(r.result.data,'base64'); }
  async close(){ try{this.ws.close();}catch(e){} await req('GET','/json/close/'+this.info.id); }
}
const sleep = ms => new Promise(r=>setTimeout(r,ms));
// Google locale settings shared by the scripts.
const GOOGLE = 'https://' + (process.env.SERP_GOOGLE_HOST || 'www.google.com');
const HL = process.env.SERP_HL || 'en';
const GL = process.env.SERP_GL || 'us';
const CONSENT_JS = `(()=>{for(const b of document.querySelectorAll('button')){if(/^(Alle akzeptieren|Accept all)$/.test(b.innerText.trim())){b.click();return 'clicked'}}return 'none'})()`;
module.exports = {Tab, sleep, req, GOOGLE, HL, GL, CONSENT_JS};
