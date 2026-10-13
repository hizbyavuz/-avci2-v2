// Private read-only Binance USD-M REST. No keys or order/account routes.
const root='https://fapi.binance.com';
const intervals=new Set(['1m','5m','15m','30m','1h','4h']);
let nextRequest=0,blockedUntil=0;
let markets:any=null,marketsAt=0,marketsPending:Promise<any>|null=null;
const snapshots=new Map<string,{at:number,value:any}>(),pending=new Map<string,Promise<any>>();
async function upstream(path:string,params:Record<string,string>={}) {
 if(Date.now()<blockedUntil)throw new Error('UPSTREAM_COOLDOWN');
 const slot=Math.max(Date.now(),nextRequest);
 if(slot-Date.now()>10000)throw new Error('QUEUE_FULL');
 nextRequest=slot+350;await Bun.sleep(Math.max(0,slot-Date.now()));
 if(Date.now()<blockedUntil)throw new Error('UPSTREAM_COOLDOWN');
 const r=await fetch(root+path+'?'+new URLSearchParams(params),{signal:AbortSignal.timeout(12000),redirect:'error'});
 if(!r.ok){if([418,429,451].includes(r.status))blockedUntil=Date.now()+3600000;throw new Error('UPSTREAM_HTTP_'+r.status);}
 return await r.json();
}
async function exchangeInfo(){
 if(markets&&Date.now()-marketsAt<3600000)return markets;
 if(!marketsPending)marketsPending=upstream('/fapi/v1/exchangeInfo').then((x:any)=>{markets=x;marketsAt=Date.now();return x;}).finally(()=>{marketsPending=null;});
 return await marketsPending;
}
async function derivatives(symbol:string){
 const c=snapshots.get(symbol);if(c&&Date.now()-c.at<60000)return c.value;
 if(pending.has(symbol))return await pending.get(symbol);
 const job=(async()=>{
  const info:any=await exchangeInfo(),market=info.symbols?.find((x:any)=>x.symbol===symbol);
  if(!market||market.status!=='TRADING'||market.contractType!=='PERPETUAL'||market.quoteAsset!=='USDT'||market.marginAsset!=='USDT')throw new Error('UNVERIFIED_MARKET');
  const observed_ms=Date.now();
  const [oi,oi_history,long_short,mark]=await Promise.all([
   upstream('/fapi/v1/openInterest',{symbol}),
   upstream('/futures/data/openInterestHist',{symbol,period:'5m',limit:'13'}),
   upstream('/futures/data/globalLongShortAccountRatio',{symbol,period:'5m',limit:'1'}),
   upstream('/fapi/v1/premiumIndex',{symbol})]);
  const value={source:'BINANCE_UM_FUTURES_REST',symbol,observed_ms,market,
   endpoints:['/fapi/v1/exchangeInfo','/fapi/v1/openInterest','/futures/data/openInterestHist','/futures/data/globalLongShortAccountRatio','/fapi/v1/premiumIndex'],oi,oi_history,long_short,mark};
  snapshots.set(symbol,{at:Date.now(),value});
  console.log('FUTURES_NATIVE_DERIVATIVES_OK',JSON.stringify({symbol,points:(oi_history as any[]).length}));return value;
 })().finally(()=>pending.delete(symbol));
 pending.set(symbol,job);return await job;
}
Bun.serve({hostname:'::',port:Number(Bun.env.PORT||8080),async fetch(request){
 const u=new URL(request.url);
 if(u.pathname==='/health')return Response.json({source:'BINANCE_UM_FUTURES_REST',read_only:true});
 if(request.method!=='GET'||!['/klines','/derivatives','/depth','/funding'].includes(u.pathname))return new Response('Not found',{status:404});
 const symbol=u.searchParams.get('symbol')||'';
 if(!/^[A-Z0-9]{1,25}USDT$/.test(symbol))return new Response('Invalid symbol',{status:400});
 try{
  if(u.pathname==='/derivatives')return Response.json(await derivatives(symbol));
  if(u.pathname==='/depth'){const requested_ms=Date.now(),book=await upstream('/fapi/v1/depth',{symbol,limit:'100'});return Response.json({source:'BINANCE_UM_FUTURES_REST',symbol,requested_ms,book});}
  if(u.pathname==='/funding'){
   const startTime=u.searchParams.get('startTime')||'',endTime=u.searchParams.get('endTime')||'';
   if(!/^[0-9]{13}$/.test(startTime)||!/^[0-9]{13}$/.test(endTime)||Number(endTime)<Number(startTime)||Number(endTime)-Number(startTime)>86400000)return new Response('Invalid horizon',{status:400});
   return Response.json({source:'BINANCE_UM_FUTURES_REST',symbol,rates:await upstream('/fapi/v1/fundingRate',{symbol,startTime,endTime,limit:'100'})});
  }
  const interval=u.searchParams.get('interval')||'';if(!intervals.has(interval))return new Response('Invalid interval',{status:400});
  const bars=await upstream('/fapi/v1/klines',{symbol,interval,limit:'300'});if(!Array.isArray(bars))throw new Error('INVALID_KLINES');
  console.log('FUTURES_RELAY_OK',JSON.stringify({symbol,interval,bars:bars.length}));
  return Response.json({source:'BINANCE_UM_FUTURES_REST',endpoint:root+'/fapi/v1/klines',symbol,interval,bars});
 }catch(e){console.log('FUTURES_RELAY_ERROR',symbol,u.pathname,String(e));return new Response('Upstream unavailable: '+String(e),{status:503});}
}});
// A bounded read-only startup probe reports endpoint availability, never a signal.
derivatives('BTCUSDT').then((x:any)=>console.log('NATIVE_REST_BOOT_PROBE',JSON.stringify({
 symbol:x.symbol,oi:x.oi,points:x.oi_history.length,long_short:x.long_short,mark:x.mark,
 market:{status:x.market.status,contractType:x.market.contractType}})))
 .catch(e=>console.log('NATIVE_REST_BOOT_PROBE_ERROR',String(e)));
