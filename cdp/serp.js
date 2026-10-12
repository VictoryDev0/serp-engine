// node serp.js <outdir> <keywords.txt> [ai_prompts.txt]
// Rankings + saved HTML per keyword, then optional Google AI Mode (udm=50) answers per prompt.
const fs=require('fs'), path=require('path');
const {Tab, sleep, GOOGLE, HL, GL, CONSENT_JS} = require('./cdp.js');
const EXTRACT = require('./extract.js');
const OUT = process.argv[2];
const KW = fs.readFileSync(process.argv[3],'utf8').split('\n').map(s=>s.trim()).filter(Boolean);
const AIQ = process.argv[4] ? fs.readFileSync(process.argv[4],'utf8').split('\n').map(s=>s.trim()).filter(Boolean) : [];
const rnd=(a,b)=>a+Math.random()*(b-a);
const slugify=(s)=>s.replace(/[^\p{L}\p{N}]+/gu,'_');
(async()=>{
  fs.mkdirSync(OUT,{recursive:true});
  const t = await Tab.open(GOOGLE+'/?hl='+HL);
  await sleep(3000);
  await t.evaluate(CONSENT_JS);
  await sleep(1500);
  const results=[];
  for (const kw of KW) {
    const url=GOOGLE+'/search?q='+encodeURIComponent(kw)+'&hl='+HL+'&gl='+GL+'&pws=0&num=20';
    await t.nav(url); await sleep(rnd(4500,6000));
    let d; try { d = await t.evaluate(EXTRACT); } catch(e){ d={error:String(e)}; }
    if (d && d.consent) { await t.evaluate(CONSENT_JS); await sleep(2500); d = await t.evaluate(EXTRACT); }
    d.keyword=kw; d.ts=new Date().toISOString();
    const html = await t.html();
    fs.writeFileSync(path.join(OUT, slugify(kw)+'.html'), html);
    delete d.aioText; // kept in the saved html
    results.push(d);
    fs.writeFileSync(path.join(OUT,'serp.json'), JSON.stringify(results,null,1));
    console.log((d.blocked?'BLOCKED ':'')+kw+' | organic='+ (d.organic||[]).length+' ads='+(d.ads||[]).length+' pack='+d.pack+' aio='+d.aio);
    if (d.blocked) { console.log('blocked, stopping'); break; }
    await sleep(rnd(2500,4500));
  }
  // AI Mode
  const ai=[];
  for (const q of AIQ) {
    const url=GOOGLE+'/search?q='+encodeURIComponent(q)+'&hl='+HL+'&gl='+GL+'&udm=50';
    await t.nav(url); await sleep(12000);
    const txt = await t.text();
    const links = await t.evaluate(`[...document.querySelectorAll('a[href^="http"]')].map(a=>a.href).filter(h=>!/google\\.|gstatic|youtube\\.com/.test(h))`);
    const slug = slugify(q).slice(0,60);
    fs.writeFileSync(path.join(OUT,'aimode_'+slug+'.txt'), txt);
    fs.writeFileSync(path.join(OUT,'aimode_'+slug+'.png'), await t.shot());
    ai.push({q, textLen:txt.length, links:[...new Set(links)].slice(0,60), text:txt.slice(0,8000)});
    fs.writeFileSync(path.join(OUT,'aimode.json'), JSON.stringify(ai,null,1));
    console.log('AI: '+q+' | len='+txt.length);
    await sleep(rnd(4000,7000));
  }
  await t.close();
})().catch(e=>{console.error(e);process.exit(1)});
