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


def native_tcltk_runtime_path(name):
    """Select runtime files only; reject unsafe MSI target paths before selection."""
    name = name.replace('\\', '/')
    parts = PurePosixPath(name).parts
    if (not parts or name.startswith('/') or '..' in parts or ':' in name or
        any(part.rstrip(' .') != part or WINDOWS_DEVICE.match(part) or re.search(r'[<>"|?*]', part) for part in parts)):
        raise ValueError('Unsafe Tcl/Tk payload path')
    selected = name.startswith(('Lib/tkinter/', 'tcl/')) or name in {
        'DLLs/_tkinter.pyd', 'DLLs/tcl86t.dll', 'DLLs/tk86t.dll', 'DLLs/zlib1.dll'}
    return selected and not (name.endswith('.lib') or '/demos/' in name or name.startswith('tcl/nmake/'))


def extract_tcltk_msi(archive, target, temporary):
    """Read an official MSI's CAB streams; never execute Windows Installer."""
    if os.name != 'nt': raise ValueError('Official Tcl/Tk MSI extraction requires a Windows build host')
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL('msi'); handle = wintypes.UINT
    api.MsiOpenDatabaseW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(handle)]
    api.MsiDatabaseOpenViewW.argtypes = [handle, wintypes.LPCWSTR, ctypes.POINTER(handle)]
    api.MsiViewExecute.argtypes = [handle, handle]
    api.MsiViewFetch.argtypes = [handle, ctypes.POINTER(handle)]
    api.MsiRecordGetStringW.argtypes = [handle, wintypes.UINT, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    api.MsiRecordReadStream.argtypes = [handle, wintypes.UINT, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    api.MsiCloseHandle.argtypes = [handle]
    def string(record, field):
        length = wintypes.DWORD(32767); text = ctypes.create_unicode_buffer(length.value + 1)
        if api.MsiRecordGetStringW(record, field, text, ctypes.byref(length)):
            raise ValueError('Official Tcl/Tk MSI string could not be read')
        return text.value
    database = handle()
    if api.MsiOpenDatabaseW(str(archive), None, ctypes.byref(database)):
        raise ValueError('Official Tcl/Tk MSI could not be opened read-only')
    def rows(sql, fields):
        view = handle()
        if api.MsiDatabaseOpenViewW(database, sql, ctypes.byref(view)):
            raise ValueError('Official Tcl/Tk MSI schema is unexpected')
        try:
            if api.MsiViewExecute(view, 0): raise ValueError('Official Tcl/Tk MSI query failed')
            while True:
                record = handle(); result = api.MsiViewFetch(view, ctypes.byref(record))
                if result == 259: break
                if result: raise ValueError('Official Tcl/Tk MSI record could not be read')
                try: yield [string(record, n) for n in range(1, fields + 1)]
                finally: api.MsiCloseHandle(record)
        finally: api.MsiCloseHandle(view)
    temporary.mkdir(parents=True, exist_ok=False)
    expanded = temporary/'expanded'; expanded.mkdir()
    try:
        files = list(rows('SELECT `File`, `FileName`, `FileSize`, `Component_` FROM `File`', 4))
        components = dict(rows('SELECT `Component`, `Directory_` FROM `Component`', 2))
        directories = {row[0]: row[1:] for row in rows('SELECT `Directory`, `Directory_Parent`, `DefaultDir` FROM `Directory`', 3)}
        if sum(int(row[2]) for row in files) > 50_000_000: raise ValueError('Tcl/Tk expansion exceeds package budget')
        def directory(identifier, seen=frozenset()):
            if identifier in ('TARGETDIR', 'InstallDirectory'): return PurePosixPath()
            if identifier in seen or identifier not in directories: raise ValueError('Unsafe Tcl/Tk directory graph')
            parent, default = directories[identifier]
            name = default.split(':')[0].split('|')[-1]
            return directory(parent, seen | {identifier}) / ('' if name in ('.', 'SourceDir') else name)
        for cabinet in rows('SELECT `Cabinet` FROM `Media`', 1):
            if not cabinet[0].startswith('#'): raise ValueError('Tcl/Tk MSI must contain its CAB payload')
            name = cabinet[0][1:]
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,160}', name): raise ValueError('Unsafe Tcl/Tk CAB name')
            view = handle(); record = handle()
            if api.MsiDatabaseOpenViewW(database, "SELECT `Data` FROM `_Streams` WHERE `Name`='" + name + "'", ctypes.byref(view)):
                raise ValueError('Official Tcl/Tk CAB stream is absent')
            try:
                if api.MsiViewExecute(view, 0) or api.MsiViewFetch(view, ctypes.byref(record)):
                    raise ValueError('Official Tcl/Tk CAB stream could not be read')
                cabinet_path = temporary/(name+'.cab'); total = 0
                with cabinet_path.open('wb') as output:
                    while True:
                        buffer = ctypes.create_string_buffer(1024*1024); length = wintypes.DWORD(len(buffer))
                        if api.MsiRecordReadStream(record, 1, buffer, ctypes.byref(length)):
                            raise ValueError('Official Tcl/Tk CAB stream is invalid')
                        if not length.value: break
                        total += length.value
                        if total > 50_000_000: raise ValueError('Tcl/Tk CAB exceeds package budget')
                        output.write(buffer.raw[:length.value])
            finally:
                if record.value: api.MsiCloseHandle(record)
                api.MsiCloseHandle(view)
            result = subprocess.run([str(Path(os.environ.get('SystemRoot', r'C:\Windows'))/'System32/expand.exe'), '-F:*', str(cabinet_path), str(expanded)],
                                    capture_output=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode: raise ValueError('Official Tcl/Tk CAB extraction failed')
        installed = []
        for identifier, raw_name, raw_size, component in files:
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,240}', identifier): raise ValueError('Unsafe Tcl/Tk file identifier')
            relative = directory(components[component]) / raw_name.split('|')[-1]
            if not native_tcltk_runtime_path(relative.as_posix()): continue
            source = expanded/identifier
            if source.is_symlink() or source.stat().st_size != int(raw_size): raise ValueError('Tcl/Tk extracted payload size mismatch')
            destination = target.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, destination)
            installed.append(relative.as_posix())
        required = {'DLLs/_tkinter.pyd', 'DLLs/tcl86t.dll', 'DLLs/tk86t.dll', 'DLLs/zlib1.dll', 'Lib/tkinter/__init__.py', 'tcl/tcl8.6/init.tcl', 'tcl/tk8.6/tk.tcl', 'tcl/tk8.6/license.terms'}
        if not required <= set(installed): raise ValueError('Official Tcl/Tk runtime payload is incomplete')
    finally:
        api.MsiCloseHandle(database)
        shutil.rmtree(temporary)


def configure_native_gui_python(python):
    """Make pywin32 available under isolated Python without site/postinstall hooks."""
    for name in ('pywintypes313.dll', 'pythoncom313.dll'):
        source = python/'Lib/site-packages/pywin32_system32'/name
        if not source.is_file(): raise ValueError('Pinned native GUI COM runtime is incomplete')
        shutil.copyfile(source, python/name)


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
            elif entry.get('kind')=='tcltk-msi': extract_tcltk_msi(archive,python,folder/'input-tcltk')
            elif entry['name'] in ('node','scrcpy','platform-tools'):
                temporary_target=folder/('input-'+entry['name']); extract(archive,temporary_target)
                children=list(temporary_target.iterdir())
                if len(children)!=1 or not children[0].is_dir(): raise ValueError('Unexpected official native archive layout')
                target=folder/'runtime/node' if entry['name']=='node' else folder/'runtime/phone'/entry['name']
                target.parent.mkdir(parents=True,exist_ok=True); shutil.move(str(children[0]),str(target)); temporary_target.rmdir()
            else: extract(archive,python/'Lib/site-packages',wheel=True)
        native_gui = any(entry.get('kind')=='tcltk-msi' for entry in entries)
        python_paths = ['python313.zip', '.']
        if native_gui:
            configure_native_gui_python(python)
            python_paths += ['Lib', 'DLLs']
        python_paths += ['Lib/site-packages', '../../app', '../../app/components/workos', '../../app/components/workos/vendor']
        if native_gui: python_paths += ['Lib/site-packages/win32', 'Lib/site-packages/win32/lib', 'Lib/site-packages/Pythonwin']
        (python/'python313._pth').write_text('\n'.join(python_paths)+'\n',encoding='utf-8')
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
        manifest={'schema_version':1,'app':'workos-suite','version':version,'platform':'windows-x64','profile':'complete','native_gui':native_gui,
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



