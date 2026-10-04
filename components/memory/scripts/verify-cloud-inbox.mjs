import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {loadConfig} from '../src/config.mjs';
const {config}=loadConfig();
const sourceId='cloud-inbox-smoke-'+Date.now();
const event={type:'activity',source:'android:manual',sourceId,timestamp:new Date().toISOString(),importance:0,
 title:'TEST: cloud inbox pipeline',text:'Synthetic cloud inbox verification, not a fact about the user.',
 tags:['synthetic','test'],metadata:{test:true}};
const body=JSON.stringify(event)+'\n';
const name='android-'+crypto.createHash('sha256').update(body).digest('hex')+'.jsonl';
const folder=path.join(config.dataRoot,'inbox','android');
fs.mkdirSync(folder,{recursive:true});fs.writeFileSync(path.join(folder,name),body);
const id='evt_'+crypto.createHash('sha256').update('android:manual:'+sourceId).digest('hex').slice(0,24);
const endpoint=`http://${config.localApiHost||'127.0.0.1'}:${config.port}/api/memory/${id}`;
let verified=false;
for(let attempt=0;attempt<25;attempt++) {
 await new Promise(resolve=>setTimeout(resolve,1000));
 const response=await fetch(endpoint,{headers:{Authorization:'Bearer '+config.token},signal:AbortSignal.timeout(3000)});
 if(response.ok){const found=await response.json();if(found.sourceId===sourceId&&found.metadata.test){verified=true;break;}}
}
if(!verified)throw new Error('Automatic cloud inbox import was not confirmed.');
console.log(JSON.stringify({verified:true,batch:name,sourceId,synthetic:true,note:'Confirms local synced-inbox merge, not phone Graph authorization/upload.'}));
