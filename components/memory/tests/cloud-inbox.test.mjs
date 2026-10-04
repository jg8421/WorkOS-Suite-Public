import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { MemoryStore } from '../src/store.mjs';
import { CloudInboxImporter } from '../src/cloud-inbox.mjs';
function fixture(t) {
  const home=fs.mkdtempSync(path.join(os.tmpdir(),'memory-cloud-test-'));
  t.after(()=>fs.rmSync(home,{recursive:true,force:true}));
  for(const p of ['state','data/events','data/memory/daily','data/inbox/android']) fs.mkdirSync(path.join(home,p),{recursive:true});
  const store=new MemoryStore(home,{privacy:{redactSecrets:true,redactOtp:true}});
  return {home,store,root:path.join(home,'data/inbox/android'),importer:new CloudInboxImporter(store,home)};
}
function batch(root,body) {
 const name=`android-${crypto.createHash('sha256').update(body).digest('hex')}.jsonl`;
 fs.writeFileSync(path.join(root,name),body); return name;
}
test('cloud batches deduplicate with LAN and remain deduplicated after restart',t=>{
 const f=fixture(t);
 const record={source:'android:manual',sourceId:'unique-one',text:'Synthetic cloud note'};
 f.store.remember(record);
 batch(f.root,JSON.stringify(record)+'\n');
 assert.equal(f.importer.scan(),0); assert.equal(f.store.stats().active,1);
 assert.equal(new CloudInboxImporter(f.store,f.home).scan(),0);
 batch(f.root,JSON.stringify({...record,sourceId:'unique-two',text:'Synthetic next note'})+'\n');
 assert.equal(f.importer.scan(),1); assert.equal(f.store.stats().active,2);
});
test('incomplete, tampered and unrelated records are not imported',t=>{
 const f=fixture(t);
 batch(f.root,'{"source":"android:manual"');
 batch(f.root,JSON.stringify({source:'unknown',sourceId:'bad',text:'bad'})+'\n');
 fs.writeFileSync(path.join(f.root,'android-'+ 'a'.repeat(64)+'.jsonl'),JSON.stringify({source:'android:manual',sourceId:'bad2',text:'altered'})+'\n');
 assert.equal(f.importer.scan(),0); assert.equal(f.store.stats().active,0);
});
test('secret redaction is applied when importing a cloud batch',t=>{
 const f=fixture(t);
 batch(f.root,JSON.stringify({source:'android:manual',sourceId:'secrets',text:'password=synthetic-secret verification code 123456'})+'\n');
 assert.equal(f.importer.scan(),1);
 assert.equal(f.store.recent()[0].text.includes('synthetic-secret'),false);
 assert.equal(f.store.recent()[0].text.includes('123456'),false);
});
