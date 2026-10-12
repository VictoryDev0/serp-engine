// node aio.js <outdir> "kw1" "kw2" ...
// AI Overviews with expanded "Show more" blocks.
const fs=require('fs'), path=require('path');
const {Tab, sleep, GOOGLE, HL, GL} = require('./cdp.js');
const EXTRACT = require('./extract.js');
const OUT=process.argv[2]; const KW=process.argv.slice(3);
const EXPAND=`(()=>{let n=0;for(const b of document.querySelectorAll('div[role=button],button,span[role=button]')){const t=(b.innerText||'').trim();if(/^(Mehr anzeigen|Mehr|Alle anzeigen|Show more|Show all)$/i.test(t)){b.click();n++}}return n})()`;
(async()=>{
  fs.mkdirSync(OUT,{recursive:true});
  const t=await Tab.open(GOOGLE+'/?hl='+HL); await sleep(2500);
  const out=[];
  for(const kw of KW){
    await t.nav(GOOGLE+'/search?q='+encodeURIComponent(kw)+'&hl='+HL+'&gl='+GL+'&pws=0'); await sleep(6000);
    await t.evaluate(EXPAND); await sleep(2500);
    const d=await t.evaluate(EXTRACT);
    out.push({kw, aio:d.aio, aioText:d.aioText, aioLinks:d.aioLinks, blocked:d.blocked});
    fs.writeFileSync(path.join(OUT,'aio.json'), JSON.stringify(out,null,1));
    console.log(kw+' | aio='+d.aio+' links='+d.aioLinks.length+' textLen='+(d.aioText||'').length+(d.blocked?' BLOCKED':''));
    if(d.blocked) break;
    await sleep(3500+Math.random()*2500);
  }
  await t.close(); process.exit(0);
})().catch(e=>{console.error(e);process.exit(1)});
