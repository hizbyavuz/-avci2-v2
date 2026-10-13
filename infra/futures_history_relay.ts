// Private read-only relay. No keys, accounts, orders, or arbitrary upstream URLs.
const intervals = new Set(['1m','5m','15m','30m','1h','4h']);
let nextRequest = 0;
let blockedUntil = 0;
Bun.serve({hostname:'::', port:Number(Bun.env.PORT || 8080), async fetch(request) {
  const u = new URL(request.url);
  if (u.pathname === '/health') return Response.json({source:'BINANCE_UM_FUTURES_REST'});
  if (request.method !== 'GET' || u.pathname !== '/klines') return new Response('Not found',{status:404});
  const symbol = u.searchParams.get('symbol') || '';
  const interval = u.searchParams.get('interval') || '';
  if (!/^[A-Z0-9]{2,25}USDT$/.test(symbol) || !intervals.has(interval)) return new Response('Invalid query',{status:400});
  if (Date.now() < blockedUntil) return new Response('Upstream cooldown',{status:503});
  const slot = Math.max(Date.now(), nextRequest);
  if (slot - Date.now() > 10000) return new Response('Busy',{status:429});
  nextRequest = slot + 350;
  await Bun.sleep(Math.max(0,slot-Date.now()));
  if (Date.now() < blockedUntil) return new Response('Upstream cooldown',{status:503});
  const endpoint = 'https://fapi.binance.com/fapi/v1/klines';
  const query = new URLSearchParams({symbol,interval,limit:'300'});
  try {
    const r = await fetch(endpoint+'?'+query,{signal:AbortSignal.timeout(12000),redirect:'error'});
    if (!r.ok) {
      if ([418,429,451].includes(r.status)) blockedUntil = Date.now()+3600000;
      console.log('FUTURES_RELAY_UNAVAILABLE',JSON.stringify({symbol,interval,status:r.status}));
      return new Response('Upstream status '+r.status,{status:503});
    }
    const bars = await r.json();
    if (!Array.isArray(bars)) return new Response('Invalid upstream response',{status:502});
    console.log('FUTURES_RELAY_OK',JSON.stringify({symbol,interval,bars:bars.length}));
    return Response.json({source:'BINANCE_UM_FUTURES_REST',endpoint,symbol,interval,bars});
  } catch(e) { console.log('FUTURES_RELAY_ERROR',String(e)); return new Response('Upstream unavailable',{status:502}); }
}});
