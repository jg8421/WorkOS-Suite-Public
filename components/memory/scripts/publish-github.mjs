import fs from 'node:fs';
import path from 'node:path';
import {spawnSync} from 'node:child_process';

// Uses the user's existing Git Credential Manager session. Never prints credentials.
const root=path.resolve(import.meta.dirname,'..');
const origin=spawnSync('git',['remote','get-url','origin'],{cwd:root,encoding:'utf8',windowsHide:true}).stdout.trim();
const expected=process.env.PERSONAL_MEMORY_GITHUB_OWNER||origin.match(/github\.com[:/]([^/]+)\//)?.[1];
if(!expected) throw new Error('Set PERSONAL_MEMORY_GITHUB_OWNER or configure the GitHub origin.');
const credentials=spawnSync('git',['credential','fill'],{
 input:`protocol=https\nhost=github.com\nusername=${expected}\n\n`,encoding:'utf8',
 env:{...process.env,GCM_INTERACTIVE:'never'},windowsHide:true
});
if(credentials.status!==0) throw new Error('GitHub credential manager requires sign-in.');
const values=Object.fromEntries(credentials.stdout.split(/\r?\n/).filter(Boolean).map(line=>{
 const i=line.indexOf('=');return [line.slice(0,i),line.slice(i+1)];
}));
if(values.host!=='github.com'||!values.password) throw new Error('Expected GitHub credentials unavailable.');
async function api(route,method='GET',body) {
 const response=await fetch('https://api.github.com'+route,{
  method,headers:{Authorization:`Bearer ${values.password}`,'User-Agent':'Personal-Memory-Publisher',
   Accept:'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/json'},
  body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(45000),redirect:'error'
 });
 if(!response.ok) {const e=new Error(`GitHub HTTP ${response.status} (${method} ${route})`);e.status=response.status;throw e;}
 return response.json();
}
const me=await api('/user');
if(me.login!==expected) throw new Error('GitHub account differs from the connected account.');
const list=spawnSync('git',['ls-files','-z'],{cwd:root,encoding:'utf8',windowsHide:true});
if(list.status!==0) throw new Error('Could not inspect staged source.');
const files=list.stdout.split('\0').filter(Boolean);
if(!files.length) throw new Error('No source files staged.');
for(const name of files) {
 if(/(^|\/)(node_modules|data|logs|state|\.manual-build|\.build|classes)(\/|$)|\.(keystore|jks|pfx|p12|pem|key|idsig|class)$|(^|\/)(config|runtime)\.json$/.test(name))
  throw new Error('Runtime/build/credential file unexpectedly staged: '+name);
 const bytes=fs.readFileSync(path.join(root,name));
 if(!name.endsWith('.apk')&&/-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\b(?:ghp|gho|github_pat)_[A-Za-z0-9_]{24,}\b|\bsk-[A-Za-z0-9]{24,}\b/.test(bytes.toString('utf8')))
  throw new Error('Credential candidate found: '+name);
}
let repo;
if(process.argv.includes('--update')) {
 const prior=JSON.parse(fs.readFileSync(path.resolve(root,'../../outputs/PersonalMemory-GitHub.json'),'utf8'));
 if(!prior.repository.startsWith(expected+'/personal-memory-component')) throw new Error('Unexpected update target.');
 repo=await api('/repos/'+prior.repository);
 if(repo.owner.login!==expected || repo.private!==true) throw new Error('Expected owned private repository.');
} else {
 let name='personal-memory-component';
 for(let i=0;i<10;i++) {
  try {await api(`/repos/${expected}/${name}`);name='personal-memory-component-'+(i+2);}
  catch(e){if(e.status===404)break;throw e;}
 }
 repo=await api('/user/repos','POST',{name,private:true,auto_init:true,
  description:'Android + Windows personal assistant memory/context hub, OneDrive sync and MCP for Codex/DSH.'});
}
const repository=`${expected}/${repo.name}`;
console.log(JSON.stringify({created:true,repository,url:repo.html_url,private:repo.private}));
const branch=repo.default_branch||'main';
const ref=await api(`/repos/${repository}/git/ref/heads/${branch}`);
const base=await api(`/repos/${repository}/git/commits/${ref.object.sha}`);
const cleanHistory=process.argv.includes('--clean-history');
let backupDirectory;
if(cleanHistory) {
 const branches=await api(`/repos/${repository}/branches`);
 const tags=await api(`/repos/${repository}/tags`);
 if(branches.length!==1 || branches[0].name!==branch || tags.length) throw new Error('Additional refs exist; review before history cleanup.');
 backupDirectory=path.resolve(root,'../../outputs/private-history-backup-'+Date.now());
 fs.mkdirSync(backupDirectory,{recursive:true,mode:0o700});
 const commits=[],trees=new Map(),blobIds=new Set();
 let next=ref.object.sha;
 while(next) {
  if(commits.length>=100) throw new Error('History exceeds backup limit.');
  const saved=await api(`/repos/${repository}/git/commits/${next}`);commits.push(saved);
  const oldTree=await api(`/repos/${repository}/git/trees/${saved.tree.sha}?recursive=1`);
  if(oldTree.truncated) throw new Error('Backup tree was truncated.');
  trees.set(saved.tree.sha,oldTree);
  for(const entry of oldTree.tree)if(entry.type==='blob')blobIds.add(entry.sha);
  if(saved.parents.length>1)throw new Error('Merge history requires an expanded backup procedure.');
  next=saved.parents[0]?.sha;
 }
 const blobs=new Map(),ids=[...blobIds];let cursor=0;
 await Promise.all(Array.from({length:6},async()=>{while(cursor<ids.length){const id=ids[cursor++];blobs.set(id,await api(`/repos/${repository}/git/blobs/${id}`));}}));
 fs.writeFileSync(path.join(backupDirectory,'remote-git-objects.json'),JSON.stringify({repository,ref,commits,trees:[...trees],blobs:[...blobs]}),{mode:0o600});
 const verified=JSON.parse(fs.readFileSync(path.join(backupDirectory,'remote-git-objects.json'),'utf8'));
 if(verified.commits.length!==commits.length || verified.blobs.length!==blobIds.size)throw new Error('Local history backup failed verification.');
 console.log(JSON.stringify({historyBackupVerified:true,commits:commits.length,blobs:blobs.size,backupDirectory}));
}
const tree=[];
for(const name of files) {
 const bytes=fs.readFileSync(path.join(root,name));
 if(name.endsWith('.apk')) {
  const blob=await api(`/repos/${repository}/git/blobs`,'POST',{content:bytes.toString('base64'),encoding:'base64'});
  tree.push({path:name,mode:'100644',type:'blob',sha:blob.sha});
 } else tree.push({path:name,mode:'100644',type:'blob',content:bytes.toString('utf8')});
}
const newTree=await api(`/repos/${repository}/git/trees`,'POST',cleanHistory?{tree}:{base_tree:base.tree.sha,tree});
const commit=await api(`/repos/${repository}/git/commits`,'POST',{
 message:'Release personal memory assistant v'+JSON.parse(fs.readFileSync(path.join(root,'package.json'),'utf8')).version,tree:newTree.sha,parents:cleanHistory?[]:[ref.object.sha],
 author:{name:'Personal Memory Release',email:'release@example.invalid'},committer:{name:'Personal Memory Release',email:'release@example.invalid'}
});
if(cleanHistory && (await api(`/repos/${repository}/git/ref/heads/${branch}`)).object.sha!==ref.object.sha)throw new Error('Remote changed during backup; not overwriting.');
await api(`/repos/${repository}/git/refs/heads/${branch}`,'PATCH',{sha:commit.sha,force:cleanHistory});
const verification=await api(`/repos/${repository}/git/trees/${commit.tree.sha}?recursive=1`);
const published=verification.tree.filter(e=>e.type==='blob');
if(files.some(name=>!published.some(e=>e.path===name))) throw new Error('Published file list mismatch.');
if(cleanHistory && published.length!==files.length)throw new Error('Unexpected old files remain.');
const remote=spawnSync('git',['remote','add','origin',repo.clone_url],{cwd:root,encoding:'utf8',windowsHide:true});
if(remote.status!==0) console.log('Remote already configured; not overwritten.');
const summary={repository,url:repo.html_url,private:repo.private,branch,commit:commit.sha,files:published.length,
 apk:`${repo.html_url}/blob/${branch}/releases/PersonalMemory-${JSON.parse(fs.readFileSync(path.join(root,'package.json'),'utf8')).version}.apk`,directGraphStatus:'optional; requires Microsoft app registration and user authorization'};
fs.writeFileSync(path.resolve(root,'../../outputs/PersonalMemory-GitHub.json'),JSON.stringify(summary,null,2));
console.log(JSON.stringify(summary));
if(process.argv.includes('--release')) {
 const version=JSON.parse(fs.readFileSync(path.join(root,'package.json'),'utf8')).version;
 const release=await api(`/repos/${repository}/releases`,'POST',{tag_name:'v'+version,target_commitish:commit.sha,name:'Personal Memory Assistant v'+version,body:fs.readFileSync(path.join(root,'CHANGELOG.md'),'utf8'),draft:false,prerelease:false});
 const apkName='PersonalMemory-'+version+'.apk';
 const upload=await fetch(release.upload_url.split('{')[0]+'?name='+encodeURIComponent(apkName),{
  method:'POST',headers:{Authorization:`Bearer ${values.password}`,'User-Agent':'Personal-Memory-Publisher','Content-Type':'application/vnd.android.package-archive'},
  body:fs.readFileSync(path.join(root,'releases',apkName)),signal:AbortSignal.timeout(60000),redirect:'error'
 });
 if(!upload.ok)throw new Error('Release APK upload failed: '+upload.status);
 const asset=await upload.json();
 console.log(JSON.stringify({release:release.html_url,apk:asset.browser_download_url,size:asset.size,historyCleaned:cleanHistory}));
}
