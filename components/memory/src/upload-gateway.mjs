import http from 'node:http';
import crypto from 'node:crypto';

export function startUploadGateway(store, config, logger=console) {
  const options=config.publicUpload;
  if(!options?.enabled) return null;
  if(!options.token || options.token.length<32) throw new Error('Upload token must be at least 32 characters.');
  const expected=crypto.createHash('sha256').update('Bearer '+options.token).digest();
  const limits=new Map();
  let lastUnauthorizedLog=0;
  const reply=(res,status,data)=>{res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(data));};
  const server=http.createServer(async(req,res)=>{
    try {
      const actual=crypto.createHash('sha256').update(req.headers.authorization||'').digest();
      if(!crypto.timingSafeEqual(expected,actual)) {
        if(Date.now()-lastUnauthorizedLog>60000){lastUnauthorizedLog=Date.now();logger.log?.('Public upload rejected: HTTP 401 (token mismatch)');}
        return reply(res,401,{error:'Unauthorized'});
      }
      const key=req.headers['cf-connecting-ip']||req.socket.remoteAddress;
      const minute=Math.floor(Date.now()/60000);
      if(limits.size>1024) limits.clear();
      const rate=limits.get(key);
      const next=rate?.minute===minute?{minute,count:rate.count+1}:{minute,count:1};
      limits.set(key,next);
      if(next.count>120) return reply(res,429,{error:'Retry later'});
      // Android's existing health probe expects this route; no private stats are returned.
      if(req.method==='GET' && req.url==='/api/stats') return reply(res,200,{ok:true,service:'personal-memory-upload'});
      if(req.method!=='POST' || req.url!=='/api/events') return reply(res,404,{error:'Not found'});
      const chunks=[];let size=0;
      for await(const chunk of req){size+=chunk.length;if(size>262144){reply(res,413,{error:'Batch too large'});return;}chunks.push(chunk);}
      const body=JSON.parse(Buffer.concat(chunks).toString('utf8'));
      const events=Array.isArray(body)?body:[body];
      if(events.length<1||events.length>100 || events.some(e=>!e || !['android:manual','android:notification','android:accessibility'].includes(e.source)
        || typeof e.sourceId!=='string' || !e.sourceId || e.sourceId.length>500
        || typeof e.text!=='string' || e.text.length>32768 || (e.title!=null && (typeof e.title!=='string'||e.title.length>32768))))
        return reply(res,400,{error:'Invalid Android batch'});
      const results=events.map(e=>store.remember(e,{deferRender:true}));store.rebuildViews();
      logger.log?.(`Public Android upload accepted: count=${events.length}, saved=${results.filter(r=>!r.duplicate).length}, duplicates=${results.filter(r=>r.duplicate).length}`);
      // Legacy Android checks events.length. Echo only the submitted identifiers, never stored memory content.
      return reply(res,201,{saved:results.filter(r=>!r.duplicate).length,duplicates:results.filter(r=>r.duplicate).length,events:events.map(e=>({sourceId:e.sourceId}))});
    } catch {logger.error?.('Upload gateway rejected or failed a request');if(!res.headersSent)reply(res,400,{error:'Upload failed'});else res.end();}
  });
  server.requestTimeout=15000;server.headersTimeout=10000;server.maxConnections=32;
  server.listen(options.port||18766,'127.0.0.1');return server;
}
