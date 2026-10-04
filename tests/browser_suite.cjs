/* Real HTTP/browser checks. Always starts an isolated server unless an explicitly
 * marked isolated test URL is supplied. No real device/model/capture actions. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const {spawn} = require('node:child_process');
function playwright() {
  for (const modulePath of [process.env.SUITE_PLAYWRIGHT_MODULE,'playwright','playwright-core'].filter(Boolean)) { try { return require(modulePath); } catch {} }
  const links = path.join(process.env.LOCALAPPDATA || '', 'ms-playwright', '.links');
  if(fs.existsSync(links))for(const file of fs.readdirSync(links)){try{return require(fs.readFileSync(path.join(links,file),'utf8').trim());}catch{}}
  throw new Error('Playwright is required; install it or set SUITE_PLAYWRIGHT_MODULE to an existing package.');
}
const delay = ms => new Promise(resolve=>setTimeout(resolve,ms));
async function waitFor(page, predicate, value) { const deadline=Date.now()+20000;while(Date.now()<deadline){if(await page.evaluate(predicate,value))return;await delay(80);}throw new Error('Browser condition did not complete within 20 seconds.'); }
async function freePort() { const server=net.createServer();await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});const port=server.address().port;await new Promise(resolve=>server.close(resolve));return port; }
async function ready(url, child) { const deadline=Date.now()+35000;while(Date.now()<deadline){if(child&&child.exitCode!==null)throw new Error('Isolated test server exited before becoming ready.');try{const response=await fetch(`${url}/api/suite/bootstrap`);if(response.ok)return;}catch{}await delay(200);}throw new Error('Isolated test server did not become ready.'); }
async function stopChild(child) { if(!child||child.exitCode!==null)return;child.kill('SIGTERM');await Promise.race([new Promise(resolve=>child.once('exit',resolve)),delay(4000)]);if(child.exitCode===null)child.kill('SIGKILL'); }
async function main() {
  const root=path.resolve(__dirname,'..');const temp=fs.mkdtempSync(path.join(os.tmpdir(),'workos-suite-browser-'));
  const artifactDir=process.env.SUITE_BROWSER_ARTIFACT_DIR||path.join(temp,'artifacts');fs.mkdirSync(artifactDir,{recursive:true});
  const fixture=path.join(temp,'selected-folder');fs.mkdirSync(fixture);fs.writeFileSync(path.join(fixture,'suite-preview.txt'),'SUITE_SCOPED_PREVIEW\nThis is synthetic test content.\n');fs.writeFileSync(path.join(fixture,'suite-table.csv'),'name,value\nsynthetic,7\n');
  fs.writeFileSync(path.join(temp,'outside-selected-folder.txt'),'OUTSIDE_SCOPE_MUST_NOT_BE_READ');
  const serverOutput=fs.openSync(path.join(temp,'server.log'),'a');
  let url=process.env.SUITE_TEST_URL;let server=null,browser=null,sleeper=null;
  if(url&&process.env.SUITE_TEST_ISOLATED!=='1')throw new Error('SUITE_TEST_URL requires SUITE_TEST_ISOLATED=1; never run against a production workspace.');
  if(!url){const port=await freePort();url=`http://127.0.0.1:${port}`;server=spawn(process.env.SUITE_PYTHON||'python',['-m','suite.server','--port',String(port),'--data-dir',path.join(temp,'data'),'--isolated','--empty-roots'],{cwd:root,env:{...process.env,PYTHONUNBUFFERED:'1'},stdio:['ignore',serverOutput,serverOutput],windowsHide:true});server.on('error',()=>{});}
  const checks=[];const mark=name=>{checks.push(name);console.log(`PASS ${name}`);};
  try {
    await ready(url,server);browser=await playwright().chromium.launch({headless:true});
    const context=await browser.newContext({viewport:{width:1440,height:1000}});context.setDefaultTimeout(20000);context.setDefaultNavigationTimeout(20000);const page=await context.newPage();const pageErrors=[];page.on('pageerror',error=>pageErrors.push(error.message));
    // Device inventory would launch ADB. Keep every phone read offline in this fixture.
    await page.route('**/api/suite/phone/devices',route=>route.fulfill({json:{devices:[],total:0}}));
    await page.route('**/api/suite/phone/status',route=>route.fulfill({json:{component:'phone',status:'needs_setup',running:false,dependency_reason:'测试使用离线设备状态，未执行 ADB',qr_supported:false}}));
    const request=context.request;
    async function read(endpoint){const response=await request.get(url+endpoint);assert(response.ok(),`GET ${endpoint}: ${response.status()}`);return response.json();}
    const boot=await read('/api/suite/bootstrap');assert(boot.csrf,'Suite must provide its own CSRF token');
    async function write(endpoint,body={},token=boot.csrf){const response=await request.post(url+endpoint,{data:body,headers:{'X-CSRF-Token':token,'Origin':url}});assert(response.ok(),`POST ${endpoint}: ${response.status()} ${(await response.text()).slice(0,150)}`);return response.json();}
    const missingToken=await request.post(url+'/api/suite/ideas',{data:{text:'must not be saved'},headers:{Origin:url}});assert.equal(missingToken.status(),403);
    const wrongOrigin=await request.post(url+'/api/suite/ideas',{data:{text:'must not be saved'},headers:{'X-CSRF-Token':boot.csrf,Origin:'https://example.invalid'}});assert.equal(wrongOrigin.status(),403);mark('Suite mutation requires CSRF and same origin');
    const coreBoot=await read('/api/bootstrap');const project=await write('/api/projects',{name:'Synthetic browser acceptance project'},coreBoot.csrf);assert(project.id,'Temporary Core project is created through the real proxy');
    const rootResult=await write('/api/suite/roots',{path:fixture,label:'Browser synthetic files'});const rootId=rootResult.id||rootResult.root_id||rootResult.root?.id||rootResult.root?.root_id;
    const updated=await read('/api/suite/bootstrap');const registered=(updated.roots||[]).find(item=>item.label==='Browser synthetic files');const fileRootId=rootId||registered?.id||registered?.root_id;assert(fileRootId,'Selected root was registered');
    await page.goto(url+'/#workos');await page.locator('#workos-frame').waitFor();await page.frameLocator('#workos-frame').locator('#main[aria-busy="false"]').waitFor();mark('Original WorkOS loads through the same-origin iframe');
    await page.goto(url+'/#ideas');await page.locator('#idea-text').waitFor();assert.equal(await page.locator('#primary-nav .nav-item').count(),4);
    await page.locator('#idea-text').fill('Draft survives module navigation');await page.getByRole('button',{name:'文件',exact:false}).first().click();await page.locator('#file-root').waitFor();await page.goto(url+'/#ideas');await page.locator('#idea-text').waitFor();assert.equal(await page.locator('#idea-text').inputValue(),'Draft survives module navigation');
    await page.reload();await page.locator('#idea-text').waitFor();assert.equal(await page.locator('#idea-text').inputValue(),'Draft survives module navigation');mark('Idea draft survives navigation and refresh');
    const ideaText=`Synthetic idea ${Date.now()}\nSecond line`;
    await page.locator('#idea-text').fill(ideaText.split('\n')[0]);await page.locator('#idea-text').press('End');await page.locator('#idea-text').press('Shift+Enter');assert((await page.locator('#idea-text').inputValue()).endsWith('\n'),'Shift+Enter creates a newline');await page.locator('#idea-text').fill(ideaText);await page.locator('#idea-project').selectOption('');
    await page.route('**/api/suite/ideas',route=>route.request().method()==='POST'?route.fulfill({status:503,json:{error:'测试：保存暂不可用，草稿已保留'}}):route.continue());await page.locator('#idea-text').press('Enter');await page.getByText('测试：保存暂不可用，草稿已保留',{exact:true}).waitFor();assert.equal(await page.locator('#idea-text').inputValue(),ideaText);await waitFor(page,()=>!document.querySelector('#idea-save')?.disabled);await page.unroute('**/api/suite/ideas');mark('Failed save retains the draft for retry');
    await page.locator('#idea-text').press('Enter');await waitFor(page,text=>document.querySelector('#idea-list')?.textContent.includes(text),ideaText);
    const ideas=await read('/api/suite/ideas');const idea=ideas.ideas.find(item=>(item.text||item.content)===ideaText);assert(idea,'Enter created an idea');
    await page.locator(`[data-idea-id="${idea.id}"] [data-action="idea-share"]`).click();await page.locator('#action-dialog').waitFor({state:'visible'});await page.locator('#dialog-project').selectOption(project.id);await page.locator('#dialog-confirm').click();
    await waitFor(page,()=>document.querySelector('#toast')?.textContent.includes('项目笔记'));
    const afterShare=await read('/api/state');assert(afterShare.notes.some(note=>note.project_id===project.id&&String(note.body||note.text||'').includes(ideaText)),'Idea created a real project note');await page.reload();await page.locator('#idea-list').waitFor();assert((await read('/api/state')).notes.some(note=>note.project_id===project.id&&String(note.body||'').includes(ideaText)));mark('Idea → project note → refresh persists');
    await page.locator('[data-action="idea-original"]').click();assert.equal(await page.locator('#idea-original-panel iframe').getAttribute('src'),'/idea/');const ideaPage=await request.get(url+'/idea/');assert(ideaPage.ok());mark('Original Idea PWA remains available');
    await page.goto(url+'/#files');await page.locator('#file-root').waitFor();await page.locator('#file-root').selectOption(fileRootId);await page.locator('[aria-label="suite-preview.txt"]').waitFor();await page.locator('[aria-label="suite-preview.txt"]').click();await waitFor(page,()=>document.querySelector('#file-preview')?.textContent.includes('SUITE_SCOPED_PREVIEW'));
    const forbidden=await request.get(url+`/api/suite/file-preview?root_id=${encodeURIComponent(fileRootId)}&path=${encodeURIComponent('../outside-selected-folder.txt')}`);assert([400,403,404].includes(forbidden.status()),'Traversal must be blocked');assert(!(await forbidden.text()).includes('OUTSIDE_SCOPE_MUST_NOT_BE_READ'));mark('File preview is real and root scoped');
    await page.locator('[data-action="file-import"]').click();await page.locator('#action-dialog').waitFor({state:'visible'});await page.locator('#dialog-project').selectOption(project.id);await page.locator('#dialog-confirm').click();await waitFor(page,()=>document.querySelector('#toast')?.textContent.includes('项目资料'));const imported=(await read('/api/state')).documents.find(document=>document.project_id===project.id&&document.filename==='suite-preview.txt');assert(imported,'Project document metadata is linked');assert(String((await read(`/api/documents/${imported.id}`)).content).includes('SUITE_SCOPED_PREVIEW'));mark('Selected file imports into actual project documents');
    await page.locator('[aria-label="suite-table.csv"]').click();await waitFor(page,()=>document.querySelector('#file-preview')?.textContent.includes('synthetic'));mark('CSV preview displays actual rows');
    await page.screenshot({path:path.join(artifactDir,'suite-files-desktop.png'),fullPage:true});
    const nativeMutations=[];page.on('request',request=>{if(request.method()==='POST'&&/\/api\/suite\/(qwen|memory|phone)\//.test(request.url()))nativeMutations.push(request.url());});
    await page.goto(url+'/#memory');await page.locator('#memory-settings-form').waitFor();for(const key of ['codex_import','dsh_import','desktop_capture','cloud_inbox'])assert.equal(await page.locator(`#setting-${key}`).isChecked(),false);
    await page.goto(url+'/#qwen');await page.getByRole('heading',{name:'千问自动化',exact:true}).waitFor();await page.goto(url+'/#phone');await page.getByRole('heading',{name:'连接设备',exact:true}).waitFor();assert.deepEqual(nativeMutations,[]);mark('Navigation never starts model, capture, or device action');
    // Only this GET is replaced: reproducible dependency UX independent of the workstation's installed apps.
    await page.route('**/api/suite/qwen/status',route=>route.fulfill({json:{component:'qwen',status:'needs_setup',running:false,dependency_reason:'测试依赖尚未安装：先配置千问桌面端',control_note:'停止自动化不结束千问内的录音'}}));await page.goto(url+'/#qwen');await page.getByText('测试依赖尚未安装：先配置千问桌面端',{exact:true}).waitFor();assert(await page.getByText('需要配置',{exact:true}).count());await page.unroute('**/api/suite/qwen/status');mark('Missing component shows a friendly setup state');
    await page.goto(url+'/#runtime');await page.locator('#repository-catalogue').waitFor();assert.equal(await page.locator('#repository-catalogue .repo-card').count(),19);assert.equal(await page.locator('[data-component-id]').count(),7);mark('Seven runtime modules and 19 public repositories are catalogued');
    sleeper=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore',windowsHide:true});sleeper.on('error',()=>{});await delay(250);
    const processData=await read('/api/suite/processes');const processRecord=processData.processes.find(item=>item.pid===sleeper.pid);assert(processRecord,'Synthetic owned child appears in actual process data');
    const stale=await request.post(url+'/api/suite/processes/control',{data:{pid:sleeper.pid,creation_time:processRecord.creation_time+1,action:'terminate',confirm:true},headers:{'X-CSRF-Token':boot.csrf,Origin:url}});assert.equal(stale.status(),409);assert.equal(sleeper.exitCode,null,'Stale creation identity never stops the process');mark('Stale/reused process identity is rejected');
    const processes=await read('/api/suite/processes');assert(processes.processes.every(item=>item.cpu===null||Number.isFinite(item.cpu)),'CPU readings are numeric or explicitly unavailable');
    await page.route('**/api/suite/processes',route=>route.fulfill({json:{processes:[{pid:777,name:'synthetic-unavailable-cpu.exe',cpu:null,memory_mb:3,creation_time:123,protected:true}],system:{cpu_percent:null,memory_percent:null}}}));await page.goto(url+'/#processes');await page.getByText('synthetic-unavailable-cpu.exe',{exact:false}).waitFor();const row=page.locator('#process-rows tr').first();assert.equal((await row.locator('td').nth(2).innerText()).trim(),'—');assert(!(await row.innerText()).includes('0.0%'));await page.unroute('**/api/suite/processes');mark('Unavailable CPU is never fabricated as zero');
    await page.goto(url+'/#ideas');await page.locator('#idea-text').waitFor();await page.setViewportSize({width:375,height:812});await page.screenshot({path:path.join(artifactDir,'suite-ideas-mobile-375.png'),fullPage:true});
    for(const view of ['ideas','files','memory','qwen','phone','processes','runtime']){await page.goto(`${url}/#${view}`);await page.locator('#content .loading-state').waitFor({state:'hidden'});const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth+1);assert.equal(overflow,false,`No outer horizontal overflow at 375px: ${view}`);}mark('All modules fit a 375px mobile viewport');
    assert.deepEqual(pageErrors,[],'No browser JavaScript errors');console.log(JSON.stringify({result:'passed',checks:checks.length,artifacts:artifactDir},null,2));
  } catch(error) {
    if(browser){const page=browser.contexts()[0]?.pages()[0];if(page){try{await page.screenshot({path:path.join(artifactDir,'suite-failure.png'),fullPage:true});const diagnostic=await page.evaluate(()=>({view:location.hash,notice:document.querySelector('#toast')?.textContent,save_disabled:document.querySelector('#idea-save')?.disabled}));console.error(JSON.stringify({diagnostic,artifacts:artifactDir}));}catch{}}}
    throw error;
  } finally {
    await stopChild(sleeper);if(browser)await browser.close();
    if(server&&server.exitCode===null){try{const bootstrap=await (await fetch(url+'/api/suite/bootstrap')).json();await fetch(url+'/api/suite/shutdown',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':bootstrap.csrf,Origin:url},body:'{}',signal:AbortSignal.timeout(5000)});await Promise.race([new Promise(resolve=>server.once('exit',resolve)),delay(6000)]);}catch{}}
    await stopChild(server);fs.closeSync(serverOutput);
  }
}
main().catch(error=>{console.error(`FAIL ${error.message}`);process.exitCode=1;});
