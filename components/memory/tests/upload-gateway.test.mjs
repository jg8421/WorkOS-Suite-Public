import test from 'node:test';
import assert from 'node:assert/strict';
import {once} from 'node:events';
import {startUploadGateway} from '../src/upload-gateway.mjs';
test('public gateway requires its own token, forbids reads and validates Android batches',async()=>{
  const records=[];const token='a'.repeat(43);
  const server=startUploadGateway({remember(e){records.push(e);return {duplicate:false};},rebuildViews(){}},{publicUpload:{enabled:true,port:19877,token}},{error(){}});
  await once(server,'listening');
  try {
    const request=(route,opts={})=>fetch('http://127.0.0.1:19877'+route,{...opts,headers:{Authorization:'Bearer '+token,...opts.headers}});
    assert.equal((await request('/api/stats',{headers:{Authorization:''}})).status,401);
    assert.equal((await request('/api/stats')).status,200);
    assert.equal((await request('/api/recent')).status,404);
    assert.equal((await request('/api/forget',{method:'POST'})).status,404);
    assert.equal((await request('/api/events',{method:'POST',body:JSON.stringify({source:'windows:context',sourceId:'test',text:'test'})})).status,400);
    const response=await request('/api/events',{method:'POST',body:JSON.stringify({source:'android:manual',sourceId:'test',text:'Synthetic test'})});
    assert.equal(response.status,201);const ack=await response.json();assert.deepEqual(ack,{saved:1,duplicates:0,events:[{sourceId:'test'}]});assert.equal(ack.events.length,1);assert.equal(records.length,1);
  } finally {server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
});
