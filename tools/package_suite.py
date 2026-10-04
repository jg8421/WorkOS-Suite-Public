"""Build the complete offline Windows suite from reviewed source and hash locks."""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import os
import urllib.request
from urllib.parse import urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOP = {'launch.py', 'README.md', 'requirements.txt', 'components.lock.json', 'repositories.json'}
EXTRAS = set()
FORBIDDEN = re.compile(r'(^|/)(?:\.[^/]+|work|outputs?|data|runtime|credentials|secrets|authentication|__pycache__|node_modules|tests)(/|$)', re.I)
WINDOWS_DEVICE = re.compile(r'^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', re.I)
COMPONENTS = {'workos', 'files', 'processes', 'qwen', 'phone', 'ideas', 'memory'}
SOURCE_SUFFIXES = {'.py', '.ps1', '.mjs', '.js', '.cjs', '.html', '.css', '.md', '.txt', '.json', '.yaml', '.yml', '.toml', '.xml', '.java', '.gradle', '.properties', '.bat', '.cmd', '.vbs', '.ico', '.svg', '.png', '.cs', '.lock', '.webmanifest'}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def allowed_source(name):
    name = name.replace('\\', '/')
    path = PurePosixPath(name)
    if path.is_absolute() or ':' in name or '..' in path.parts or FORBIDDEN.search(name): return False
    if path.name.lower() in {'last-addr.txt','last_serial.txt','hardware_sensor_bridge.json','hardware_monitor_path.txt',
                            'state.json','pairing.json','phone-pairing.json','phone-state.json','paired-devices.json','wireless-adb.json'} or path.name.lower().startswith('adbkey'): return False
    if path.suffix.lower()=='.json' and re.search(r'(?:phone[-_]?pairing[-_]?state|pairing[-_]?state)',path.name,re.I):return False
    if path.name.lower() in {'config.json', 'runtime.json', 'custom-models.json', 'project-artifacts.json', 'settings.json'} or path.suffix.lower() in {'.db', '.sqlite', '.sqlite3', '.log', '.pid', '.pem', '.key', '.pfx', '.jks'}: return False
    if path.suffix.lower()=='.json' and not re.search(r'(?:example|sample|schema)',path.name,re.I) and re.search(r'(?:credentials?|secrets?|api[-_]?keys?|tokens?|session|\.state|settings)',path.name,re.I): return False
    if any(part in {'录音', 'recordings', 'state', 'logs', 'android-smoke', 'gradle', 'build', 'dist'} for part in path.parts): return False
    if name in TOP | EXTRAS: return True
    if name.startswith('suite/'): return path.suffix in ('.py', '.ps1', '.mjs')
    if name.startswith('web/'): return path.suffix in ('.js', '.html', '.css', '.svg')
    if name.startswith('docs/'): return path.suffix == '.md'
    if name.startswith('deployment/'): return path.suffix in ('.py', '.json', '.cmd', '.ps1', '.md')
    if name in {'tools/deployment_preflight.py', 'tools/verify_suite.py'}: return True
    if len(path.parts)>2 and path.parts[0]=='components' and path.parts[1] in COMPONENTS:
        if name.startswith('components/phone/bin/'): return False  # Fresh, official native inputs go under runtime/phone.
        if path.name in {'LICENSE', 'LICENSE-MIT', 'LICENSE-APACHE', 'NOTICE', 'NOTICE.txt', 'COPYING'}: return True
        if name == 'components/memory/releases/PersonalMemory-0.4.0.apk': return True
        if path.parts[1]=='files' and path.name in {'native_pdf_preview.exe', 'Microsoft.Web.WebView2.Core.dll', 'Microsoft.Web.WebView2.WinForms.dll', 'WebView2Loader.dll'}: return True
        return path.suffix in SOURCE_SUFFIXES
    return False


def source_files(root, *, preview=False):
    raw = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z'] + (['--cached', '--others', '--exclude-standard'] if preview else []))
    names = set(raw.decode('utf-8').split('\0')) | EXTRAS
    output = []
    for name in sorted(names):
        if not name or not allowed_source(name): continue
        path = root / name
        if not path.is_file(): raise ValueError('Required package source is absent: ' + name)
        if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('Package source cannot be a link/reparse point')
        output.append((name, path))
    return output


def fetch(entry, cache):
    url = urlsplit(entry['url'])
    if url.scheme != 'https' or url.hostname not in ('www.python.org', 'files.pythonhosted.org', 'nodejs.org', 'dl.google.com', 'github.com') or url.username or url.password or url.query or url.fragment:
        raise ValueError('Runtime download is not from an approved official host')
    if url.hostname=='github.com' and not url.path.startswith('/Genymobile/scrcpy/releases/download/v4.1/'): raise ValueError('Unapproved native release repository')
    if not re.fullmatch(r'[a-f0-9]{64}', entry['sha256']): raise ValueError('Runtime SHA256 is invalid')
    filename = entry['filename']
    if not isinstance(filename,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,160}',filename): raise ValueError('Unsafe runtime filename')
    cache.mkdir(parents=True, exist_ok=True); path = cache / filename
    if path.exists():
        raw = path.read_bytes()
    else:
        with urllib.request.urlopen(entry['url'], timeout=60) as response:
            raw = response.read(150_000_001)
        if len(raw) > 150_000_000: raise ValueError('Runtime input exceeds package budget')
    if digest(raw) != entry['sha256']: raise ValueError('Runtime download SHA256 mismatch: ' + filename)
    if not path.exists(): path.write_bytes(raw)
    return path


def extract(archive, target, *, wheel=False):
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zipped:
        if sum(item.file_size for item in zipped.infolist()) > 400_000_000:
            raise ValueError('Archive expansion exceeds budget')
        for item in zipped.infolist():
            name = item.filename.replace('\\', '/')
            parts = PurePosixPath(name).parts
            if (not parts or name.startswith('/') or '..' in parts or ':' in name or
                any(part.rstrip(' .')!=part or WINDOWS_DEVICE.match(part) or re.search(r'[<>"|?*]',part) for part in parts) or
                ((item.external_attr >> 16) & 0o170000) == 0o120000):
                raise ValueError('Unsafe runtime archive member')
            if wheel and parts[0].endswith('.data'):
                if len(parts) < 3 or parts[1] not in ('purelib', 'platlib'):
                    continue
                parts = parts[2:]
            destination = target.joinpath(*parts)
            if item.is_dir(): destination.mkdir(parents=True, exist_ok=True); continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(zipped.read(item))


def validate_npm_lock(lock):
    if lock.get('lockfileVersion') != 3 or not isinstance(lock.get('packages'), dict): raise ValueError('Invalid production npm lock')
    for entry in lock['packages'].values():
        if 'resolved' not in entry: continue
        url=urlsplit(entry['resolved'])
        if url.scheme!='https' or url.hostname!='registry.npmjs.org' or url.username or url.password or url.query or url.fragment:
            raise ValueError('Production npm input is outside the official registry')
        if not re.fullmatch(r'sha512-[A-Za-z0-9+/]+={0,2}', entry.get('integrity','')): raise ValueError('Production npm input lacks integrity')
        if entry.get('hasInstallScript'): raise ValueError('Production npm dependencies cannot require install scripts')


def install_memory_dependencies(root, folder, cache):
    """Use bundled Node/npm in a temporary build directory; run no package scripts."""
    lock_path=root/'deployment/memory-package-lock.json'
    lock=json.loads(lock_path.read_text(encoding='utf-8')); validate_npm_lock(lock)
    build=folder/'npm-build'; build.mkdir()
    shutil.copyfile(root/'deployment/memory-package.json',build/'package.json')
    shutil.copyfile(lock_path,build/'package-lock.json')
    for name in ('user.npmrc','global.npmrc'): (build/name).write_text('',encoding='ascii')
    node=folder/'runtime/node/node.exe'; npm=folder/'runtime/node/node_modules/npm/bin/npm-cli.js'
    command=[str(node),str(npm),'ci','--ignore-scripts','--omit=dev','--audit=false','--fund=false','--registry=https://registry.npmjs.org',
             '--userconfig='+str(build/'user.npmrc'),'--globalconfig='+str(build/'global.npmrc'),'--cache='+str(cache/'npm-cache')]
    environment={k:v for k,v in os.environ.items() if not k.upper().startswith('NPM_CONFIG_') and k.upper() not in {'NODE_OPTIONS','NODE_PATH','NPM_TOKEN','NODE_AUTH_TOKEN'}}
    environment['PATH']=str(node.parent)+os.pathsep+environment.get('PATH','')
    result=subprocess.run(command,cwd=build,env=environment,capture_output=True,text=True,timeout=180)
    if result.returncode: raise ValueError('Pinned memory dependencies could not be installed; no host packages were changed')
    target=folder/'app/components/memory/node_modules'
    shutil.copytree(build/'node_modules',target,symlinks=False,ignore=shutil.ignore_patterns('.bin'))
    # npm's internal dependency lock is public software metadata; no npm cache/config/logs enter the delivery.
    shutil.rmtree(build)


def git_state(root):
    result=subprocess.run(['git','-C',str(root),'rev-parse','HEAD'],capture_output=True,text=True)
    revision=result.stdout.strip() if result.returncode==0 else None
    dirty=bool(subprocess.check_output(['git','-C',str(root),'status','--porcelain']))
    return revision,dirty


def build(root, output, cache, *, allow_dirty=False):
    root=Path(root).resolve(); output=Path(output).resolve(); cache=Path(cache).resolve()
    lock=json.loads((root/'deployment/runtime-lock.json').read_text(encoding='utf-8'))
    entries=lock['entries']
    version=ast.literal_eval(next(line.split('=',1)[1].strip() for line in (root/'suite/__init__.py').read_text(encoding='utf-8').splitlines() if line.startswith('__version__')))
    revision,dirty=git_state(root)
    if (dirty or revision is None) and not allow_dirty: raise ValueError('Release package requires a committed, clean Git HEAD')
    preview=dirty or revision is None
    output.mkdir(parents=True,exist_ok=True); cache.mkdir(parents=True,exist_ok=True)
    sources=source_files(root,preview=allow_dirty)
    required={'launch.py','suite/__init__.py','suite/server.py','suite/runtime.py','web/index.html','components.lock.json','tools/deployment_preflight.py'}
    present={name for name,_ in sources}
    if not required<=present: raise ValueError('Suite runtime source is incomplete: '+', '.join(sorted(required-present)))
    if not COMPONENTS <= {PurePosixPath(name).parts[1] for name in present if name.startswith('components/')}: raise ValueError('All seven component sources are required')
    component_lock=json.loads((root/'components.lock.json').read_text(encoding='utf-8'))
    if {e['id'] for e in component_lock['components']} != COMPONENTS: raise ValueError('Seven-component provenance lock is incomplete')
    with tempfile.TemporaryDirectory(prefix='suite-package-') as temporary:
        folder=Path(temporary); app=folder/'app'; app.mkdir()
        source_hashes={}
        for name,path in sources:
            destination=app/name; destination.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(path,destination)
            source_hashes[name]=digest(destination.read_bytes())
        python=folder/'runtime/python'
        for entry in entries:
            archive=fetch(entry,cache)
            if entry['name']=='python': extract(archive,python)
            elif entry['name'] in ('node','scrcpy','platform-tools'):
                temporary_target=folder/('input-'+entry['name']); extract(archive,temporary_target)
                children=list(temporary_target.iterdir())
                if len(children)!=1 or not children[0].is_dir(): raise ValueError('Unexpected official native archive layout')
                target=folder/'runtime/node' if entry['name']=='node' else folder/'runtime/phone'/entry['name']
                target.parent.mkdir(parents=True,exist_ok=True); shutil.move(str(children[0]),str(target)); temporary_target.rmdir()
            else: extract(archive,python/'Lib/site-packages',wheel=True)
        (python/'python313._pth').write_text('python313.zip\n.\nLib/site-packages\n../../app\n../../app/components/workos\n../../app/components/workos/vendor\n',encoding='utf-8')
        install_memory_dependencies(root,folder,cache)
        for name in ('Start-Suite.cmd','Stop-Suite.cmd','Check-Environment.cmd'):
            shutil.copyfile(root/'deployment'/name,folder/name)
        shutil.copyfile(root/'deployment/Start-Suite.cmd',folder/'启动.cmd')
        shutil.copyfile(root/'deployment/Stop-Suite.cmd',folder/'停止.cmd')
        shutil.copyfile(root/'deployment/Check-Environment.cmd',folder/'环境检查.cmd')
        shutil.copyfile(root/'docs/DEPLOYMENT.md',folder/'README-DEPLOYMENT.md')
        shutil.copyfile(root/'deployment/runtime-lock.json',folder/'runtime-lock.json')
        notices=root/'deployment/THIRD-PARTY-NOTICES.md'
        if notices.is_file():shutil.copyfile(notices,folder/'THIRD-PARTY-NOTICES.md')
        else:(folder/'THIRD-PARTY-NOTICES.md').write_text('# Bundled third-party software\n\nPython, Node.js, Google Android platform-tools and Genymobile scrcpy are distributed with their original license/notice files. Python wheel licenses remain in their .dist-info directories; Node/npm and memory dependencies retain package license files. Versions, official URLs and SHA256 values are listed in runtime-lock.json.\n\nMicrosoft Office, WebView2, Qianwen client, model accounts and DSH are external integrations; their software/accounts/credentials are not copied from the build machine.\n',encoding='utf-8')
        current_revision,current_dirty=git_state(root)
        if current_revision!=revision: raise ValueError('Git HEAD changed during build')
        if any(digest((root/name).read_bytes())!=sha for name,sha in source_hashes.items()): raise ValueError('Source changed during build; freeze it and rebuild')
        preview=preview or current_dirty
        if preview and not allow_dirty: raise ValueError('Release source changed during build')
        manifest={'schema_version':1,'app':'workos-suite','version':version,'platform':'windows-x64','profile':'complete',
                  'source_revision':revision,'working_tree_changes':preview,'build_status':'preview' if preview else 'release',
                  'components':component_lock['components'],'dependencies':entries,'npm_lock_sha256':digest((root/'deployment/memory-package-lock.json').read_bytes()),
                  'private_data_included':False,'account_credentials_included':False,'host_site_packages_included':False,'files':[]}
        for path in sorted(folder.rglob('*')):
            if path.is_symlink() or getattr(path.lstat(),'st_file_attributes',0)&0x400: raise ValueError('Package cannot contain reparse points')
            if path.is_file(): manifest['files'].append({'path':path.relative_to(folder).as_posix(),'bytes':path.stat().st_size,'sha256':digest(path.read_bytes())})
        (folder/'package-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        name=f'WorkOS-Suite-{version}-windows-x64-complete'+('-preview' if preview else '')
        destination=output/(name+'.zip')
        if destination.exists(): raise FileExistsError('Package already exists; use a fresh build output directory')
        with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as zipped:
            for path in sorted(folder.rglob('*')):
                if path.is_file(): zipped.write(path,name+'/'+path.relative_to(folder).as_posix())
        checksum=digest(destination.read_bytes())
        (output/(name+'.sha256')).write_text(checksum+'  '+destination.name+'\n',encoding='ascii')
        return {'path':str(destination),'bytes':destination.stat().st_size,'sha256':checksum,'files':len(manifest['files']),
                'version':version,'source_revision':revision,'build_status':manifest['build_status']}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--cache',type=Path,default=ROOT/'work/package-cache')
    parser.add_argument('--allow-dirty',action='store_true',help='Build clearly labelled public-source preview')
    args=parser.parse_args();print(json.dumps(build(ROOT,args.output,args.cache,allow_dirty=args.allow_dirty)))



