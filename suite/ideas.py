"""Durable quick capture and idempotent links into WorkOS project records."""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid


class IdeaStore:
    def __init__(self, data_dir):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(Path(data_dir)/'suite.sqlite3', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS ideas(id TEXT PRIMARY KEY,text TEXT NOT NULL,project_id TEXT NOT NULL,created_at REAL NOT NULL,request_id TEXT UNIQUE);
          CREATE TABLE IF NOT EXISTS shares(idea_id TEXT,project_id TEXT,note_id TEXT,PRIMARY KEY(idea_id,project_id));
          CREATE TABLE IF NOT EXISTS imports(root_id TEXT,path TEXT,hash TEXT,project_id TEXT,document_id TEXT,PRIMARY KEY(root_id,path,hash,project_id));
        ''')

    def list(self):
        with self.lock:
            records = []
            for row in self.db.execute('SELECT * FROM ideas ORDER BY created_at DESC LIMIT 500'):
                item = dict(row);item.pop('request_id',None)
                item['title'] = item['text'].splitlines()[0][:120]
                links = [dict(x) for x in self.db.execute('SELECT project_id,note_id FROM shares WHERE idea_id=?',(item['id'],))]
                item['shares'] = links
                item['shared_note_id'] = next((x['note_id'] for x in links if x['project_id']==item['project_id']), '')
                records.append(item)
            return {'ideas':records}

    def add(self, body, core):
        if set(body)-{'text','project_id','request_id'}: raise ValueError('想法包含不支持的字段')
        text = body.get('text');project = body.get('project_id') or ''
        if not isinstance(text,str) or not text.strip() or len(text)>100_000: raise ValueError('请输入不超过十万字的想法')
        if not isinstance(project,str) or len(project)>200: raise ValueError('请选择有效项目')
        if project and not any(p['id']==project for p in core.json('GET','/api/state')['projects']): raise ValueError('项目已不存在，请重新选择')
        request_id = body.get('request_id')
        if request_id is not None and (not isinstance(request_id,str) or not 1<=len(request_id)<=100): raise ValueError('请求标识无效')
        with self.lock:
            if request_id:
                row = self.db.execute('SELECT * FROM ideas WHERE request_id=?',(request_id,)).fetchone()
                if row:
                    if row['text']!=text.strip() or row['project_id']!=project: raise ValueError('同一请求标识对应了不同想法')
                    return dict(row)
            item = {'id':uuid.uuid4().hex,'text':text.strip(),'project_id':project,'created_at':time.time()}
            self.db.execute('INSERT INTO ideas VALUES(?,?,?,?,?)', (*item.values(), request_id))
            self.db.commit()
            return item

    def delete(self, idea_id):
        with self.lock:
            if not self.db.execute('SELECT 1 FROM ideas WHERE id=?',(idea_id,)).fetchone(): raise FileNotFoundError('想法已不存在')
            self.db.execute('DELETE FROM shares WHERE idea_id=?',(idea_id,))
            self.db.execute('DELETE FROM ideas WHERE id=?',(idea_id,));self.db.commit()
            return {'deleted':True,'project_notes_retained':True}

    def share(self, idea_id, body, core):
        if set(body)-{'project_id'}: raise ValueError('关联包含不支持的字段')
        project = body.get('project_id')
        if not isinstance(project,str) or not project: raise ValueError('请先选择想法所属项目')
        with self.lock:
            idea = self.db.execute('SELECT * FROM ideas WHERE id=?',(idea_id,)).fetchone()
            if not idea: raise FileNotFoundError('想法已不存在')
            state = core.json('GET','/api/state')
            if not any(p['id']==project for p in state['projects']): raise ValueError('项目已不存在，请重新选择')
            marker = 'WorkOS-Suite idea/'+idea_id
            # Recover after a gateway crash between the WorkOS write and local receipt.
            note = next((n for n in state['notes'] if n.get('project_id')==project and n.get('source_quote')==marker),None)
            if not note:
                note = core.json('POST','/api/notes',{'title':idea['text'].splitlines()[0][:200],
                    'body':idea['text'],'project_id':project,'status':'待核实','source_quote':marker})
            self.db.execute('INSERT OR REPLACE INTO shares VALUES(?,?,?)',(idea_id,project,note['id']))
            self.db.execute('UPDATE ideas SET project_id=? WHERE id=?',(project,idea_id));self.db.commit()
            return {'note':note,'note_id':note['id'],'project_id':project,'linked':True}

    def import_files(self, body, files, core):
        if set(body)-{'root_id','paths','project_id'}: raise ValueError('导入包含不支持的字段')
        paths = body.get('paths');root = body.get('root_id');project = body.get('project_id')
        if not isinstance(paths,list) or not 1<=len(paths)<=20 or any(not isinstance(p,str) for p in paths): raise ValueError('每次请选择一至二十份文件')
        if not isinstance(project,str) or not project: raise ValueError('请选择需要关联的项目')
        with self.lock:
            state = core.json('GET','/api/state')
            if not any(p['id']==project for p in state['projects']): raise ValueError('项目已不存在，请重新选择')
            existing_ids = {d['id'] for d in state['documents']}
            results = []
            for relative in paths:
                selected=files.resolve(root,relative)
                if not selected.is_file():raise ValueError('请点选具体文件再送到项目')
                if selected.suffix.lower() not in ('.txt','.md','.pdf','.docx','.pptx','.xlsx','.xlsm'):
                    raise ValueError('此格式可在文件页查看；项目导入支持 PDF、Excel、Word、PPT 和文本，请导出其中一种格式后继续')
            for relative in dict.fromkeys(paths):
                path = files.resolve(root,relative)
                if not path.is_file(): raise ValueError('只能导入文件，请选择文件后重试')
                before = path.stat()
                if before.st_size>20_000_000: raise ValueError('单份资料最多20MB，请拆分后导入')
                raw = path.read_bytes();after=path.stat()
                if len(raw)>20_000_000 or (before.st_mtime_ns,before.st_size)!=(after.st_mtime_ns,after.st_size): raise ValueError('资料正在修改，请保存文件后重试')
                digest = hashlib.sha256(raw).hexdigest()
                row = self.db.execute('SELECT document_id FROM imports WHERE root_id=? AND path=? AND hash=? AND project_id=?',(root,relative,digest,project)).fetchone()
                doc = next((d for d in state['documents'] if row and d['id']==row['document_id']),None)
                source = 'Suite/'+str(root)+'/'+relative.replace('\\','/')
                if not doc:
                    doc = next((d for d in state['documents'] if d.get('project_id')==project and d.get('source_ref')==source and d.get('hash')==digest),None)
                if not doc:
                    doc = core.json('POST','/api/upload',{'name':path.name,'base64':base64.b64encode(raw).decode(),'kind':'research','project_id':project,'source_ref':source})
                self.db.execute('INSERT OR REPLACE INTO imports VALUES(?,?,?,?,?)',(root,relative,digest,project,doc['id']));self.db.commit()
                results.append({'path':relative,'document_id':doc['id'],'reused':doc['id'] in existing_ids})
            return {'results':results,'imported':len(results)}

    def close(self):
        with self.lock:self.db.close()
