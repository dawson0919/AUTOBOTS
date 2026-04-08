const fs=require('fs'),path=require('path');
const {EMA}=require('./cryptobot/node_modules/technicalindicators');
function loadCSV(f){return fs.readFileSync(f,'utf8').trim().split('\n').slice(1).map(l=>{const c=l.split(',');return{time:+c[0],open:+c[1],high:+c[2],low:+c[3],close:+c[4]};});}
function calcATR(c,p){const a=[];for(let i=0;i<c.length;i++){const tr=i===0?c[i].high-c[i].low:Math.max(c[i].high-c[i].low,Math.abs(c[i].high-c[i-1].close),Math.abs(c[i].low-c[i-1].close));if(i<p-1){a.push(tr);continue;}if(i===p-1){let s=0;for(let j=0;j<=i;j++)s+=(j===0?c[j].high-c[j].low:Math.max(c[j].high-c[j].low,Math.abs(c[j].high-c[j-1].close),Math.abs(c[j].low-c[j-1].close)));a.push(s/p);}else{a.push((a[a.length-1]*(p-1)+tr)/p);}}return a;}
const candles=loadCSV(path.join(__dirname,'COMEX_GC1!, 60.csv'));
const n=candles.length,closes=candles.map(c=>c.close);
const atr14=calcATR(candles,14);
const emaCache={};
for(const p of[3,5,6,8,10,12,14,15,18,20,25,30,35,40,50,60,80])emaCache[p]=EMA.calculate({period:p,values:closes});
function gv(arr,i){const idx=i-(n-arr.length);return idx>=0&&idx<arr.length?arr[idx]:null;}
function bt(cfg){
  const ef=emaCache[cfg.fast],em=emaCache[cfg.mid],es=emaCache[cfg.slow];
  if(!ef||!em||!es)return null;
  const etr=cfg.trailEMA?emaCache[cfg.trailEMA]:em;
  const start=Math.max(cfg.slow,14)+5;
  let bal=100000,pos=null,trades=[],peak=100000,maxDD=0;
  for(let i=start;i<n;i++){
    const c=candles[i],price=c.close;
    const fv=gv(ef,i),fp=gv(ef,i-1),mv=gv(em,i),mp=gv(em,i-1),sv=gv(es,i),sp=gv(es,i-1);
    const atr=atr14[i-(n-atr14.length)];const trv=gv(etr,i);
    if(!fv||!fp||!mv||!mp||!sv||!sp||!atr||!trv)continue;
    const bullNow=fv>mv&&mv>sv,bullPrev=fp>mp&&mp>sp;
    const bearNow=fv<mv&&mv<sv,bearPrev=fp<mp&&mp<sp;
    if(pos){
      const isL=pos.side==='long';
      if(isL&&c.low<=pos.sl){bal+=pos.sl-pos.entry;trades.push({pnl:pos.sl-pos.entry});pos=null;continue;}
      if(!isL&&c.high>=pos.sl){bal+=pos.entry-pos.sl;trades.push({pnl:pos.entry-pos.sl});pos=null;continue;}
      if(cfg.exitMode==='tp'){
        if(isL&&c.high>=pos.tp){bal+=pos.tp-pos.entry;trades.push({pnl:pos.tp-pos.entry});pos=null;continue;}
        if(!isL&&c.low<=pos.tp){bal+=pos.entry-pos.tp;trades.push({pnl:pos.entry-pos.tp});pos=null;continue;}
      }else{
        if(isL){const fp2=price-pos.entry;if(!pos.ta&&fp2>=pos.atr*cfg.tpMult){pos.ta=true;pos.ts=trv;}if(pos.ta){pos.ts=Math.max(pos.ts,trv);if(c.low<=pos.ts){bal+=pos.ts-pos.entry;trades.push({pnl:pos.ts-pos.entry});pos=null;continue;}}}
        else{const fp2=pos.entry-price;if(!pos.ta&&fp2>=pos.atr*cfg.tpMult){pos.ta=true;pos.ts=trv;}if(pos.ta){pos.ts=Math.min(pos.ts,trv);if(c.high>=pos.ts){bal+=pos.entry-pos.ts;trades.push({pnl:pos.entry-pos.ts});pos=null;continue;}}}
      }
      if(cfg.sigExit==='cross'){if(isL&&fv<mv){bal+=price-pos.entry;trades.push({pnl:price-pos.entry});pos=null;continue;}if(!isL&&fv>mv){bal+=pos.entry-price;trades.push({pnl:pos.entry-price});pos=null;continue;}}
      else if(cfg.sigExit==='reverse'){if(isL&&bearNow){bal+=price-pos.entry;trades.push({pnl:price-pos.entry});pos=null;continue;}if(!isL&&bullNow){bal+=pos.entry-price;trades.push({pnl:pos.entry-price});pos=null;continue;}}
    }else{
      if(bullNow&&!bullPrev){pos={side:'long',entry:price,sl:price-atr*cfg.slMult,tp:cfg.exitMode==='tp'?price+atr*cfg.tpMult:0,atr,ta:false,ts:0};continue;}
      if(bearNow&&!bearPrev){pos={side:'short',entry:price,sl:price+atr*cfg.slMult,tp:cfg.exitMode==='tp'?price-atr*cfg.tpMult:0,atr,ta:false,ts:Infinity};continue;}
    }
    if(bal>peak)peak=bal;const dd=(peak-bal)/peak;if(dd>maxDD)maxDD=dd;
  }
  if(pos){const lp=candles[n-1].close;const pnl=pos.side==='long'?lp-pos.entry:pos.entry-lp;bal+=pnl;trades.push({pnl});}
  const w=trades.filter(t=>t.pnl>0),l=trades.filter(t=>t.pnl<=0);
  const gp=w.reduce((s,t)=>s+t.pnl,0),gl=Math.abs(l.reduce((s,t)=>s+t.pnl,0));
  return{t:trades.length,w:w.length,wr:trades.length>0?w.length/trades.length*100:0,ret:(bal-100000)/100000*100,pf:gl>0?gp/gl:99,mdd:maxDD*100,aw:w.length>0?gp/w.length:0,al:l.length>0?gl/l.length:0};
}
console.log("ThreeBlade Optimization - "+n+" candles\n");
const orig=bt({fast:8,mid:15,slow:30,slMult:1.5,exitMode:'tp',tpMult:2.5,sigExit:'cross',trailEMA:15});
console.log("ORIGINAL (8/15/30 SL:1.5 TP:2.5 cross): Ret:"+orig.ret.toFixed(1)+"% PF:"+orig.pf.toFixed(2)+" WR:"+orig.wr.toFixed(1)+"% Trades:"+orig.t+" MDD:"+orig.mdd.toFixed(1)+"%\n");
const results=[];
for(const fast of[3,5,6,8,10,12])
for(const mid of[12,14,15,18,20,25,30])
for(const slow of[25,30,35,40,50,60,80])
for(const slMult of[0.75,1.0,1.5,2.0,2.5,3.0])
for(const exitMode of['tp','trail'])
for(const tpMult of[1.5,2.0,2.5,3.0,4.0,5.0])
for(const sigExit of['cross','reverse','none'])
{
  if(mid<=fast||slow<=mid)continue;
  const trailEMA=exitMode==='trail'?mid:null;
  const r=bt({fast,mid,slow,slMult,exitMode,tpMult,sigExit,trailEMA});
  if(!r||r.t<20)continue;
  results.push({fast,mid,slow,slMult,exitMode,tpMult,sigExit,trailEMA,...r});
}
results.sort((a,b)=>b.ret-a.ret);
console.log("Valid: "+results.length+"\n");
console.log("=== TOP 15 BY RETURN ===");
for(let i=0;i<Math.min(15,results.length);i++){
  const r=results[i];
  console.log((i+1)+". EMA:"+r.fast+"/"+r.mid+"/"+r.slow+" SL:"+r.slMult+" "+r.exitMode+" TP:"+r.tpMult+" exit:"+r.sigExit+" | Ret:"+r.ret.toFixed(1)+"% PF:"+r.pf.toFixed(2)+" WR:"+r.wr.toFixed(1)+"% T:"+r.t+" MDD:"+r.mdd.toFixed(1)+"% W:"+r.aw.toFixed(0)+"/L:"+r.al.toFixed(0));
}
console.log("\n=== TOP 10 BY PF (>=30 trades) ===");
[...results].filter(r=>r.t>=30).sort((a,b)=>b.pf-a.pf).slice(0,10).forEach(r=>{
  console.log("  PF:"+r.pf.toFixed(2)+" EMA:"+r.fast+"/"+r.mid+"/"+r.slow+" SL:"+r.slMult+" "+r.exitMode+" TP:"+r.tpMult+" exit:"+r.sigExit+" | Ret:"+r.ret.toFixed(1)+"% WR:"+r.wr.toFixed(1)+"% T:"+r.t);
});
console.log("\n=== TOP 10 BY WR (>=30 trades) ===");
[...results].filter(r=>r.t>=30).sort((a,b)=>b.wr-a.wr).slice(0,10).forEach(r=>{
  console.log("  WR:"+r.wr.toFixed(1)+"% EMA:"+r.fast+"/"+r.mid+"/"+r.slow+" SL:"+r.slMult+" "+r.exitMode+" TP:"+r.tpMult+" exit:"+r.sigExit+" | Ret:"+r.ret.toFixed(1)+"% PF:"+r.pf.toFixed(2)+" T:"+r.t);
});
// Trail EMA sweep on best
console.log("\n=== TRAIL EMA SWEEP on best base ===");
const best=results[0];
for(const trEMA of[10,15,20,25,30,40,50]){
  if(!emaCache[trEMA])continue;
  const r=bt({fast:best.fast,mid:best.mid,slow:best.slow,slMult:best.slMult,exitMode:'trail',tpMult:best.tpMult,sigExit:best.sigExit,trailEMA:trEMA});
  if(r)console.log("  Trail EMA "+trEMA+": Ret:"+r.ret.toFixed(1)+"% PF:"+r.pf.toFixed(2)+" WR:"+r.wr.toFixed(1)+"% T:"+r.t+" MDD:"+r.mdd.toFixed(1)+"%");
}
