from __future__ import annotations
import ctypes
import http.client
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch
from urllib.parse import urlencode
import zipfile

from suite.files import FileService,FileConflict,_clipboard_payload,_write_system_clipboard
from suite.server import Handler,LocalServer


class FileWorkbenchFeaturesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name).resolve()
        self.folder=self.base/'selected';self.folder.mkdir()
        self.service=FileService(self.base/'private',[])
        self.root=self.service.add_root(self.folder,'合成资料')['id']

    def tearDown(self):self.service.close();self.temp.cleanup()

    def test_recursive_unicode_name_and_content_only_within_selected_root(self):
        nested=self.folder/'子目录';nested.mkdir()
        (nested/'合成研究.txt').write_text('这是资料正文，含有研发驱动。',encoding='utf-8')
        (nested/'研发驱动备忘录.md').write_text('different body',encoding='utf-8')
        outside=self.base/'foreign';outside.mkdir();(outside/'研发驱动.txt').write_text('outside',encoding='utf-8')
        self.service.add_root(outside)
        result=self.service.search(self.root,'','研发驱动')
        self.assertEqual({e['path'] for e in result['entries']},{'子目录/合成研究.txt','子目录/研发驱动备忘录.md'})
        matches={e['name']:e for e in result['entries']}
        self.assertEqual(matches['合成研究.txt']['match'],'content')
        self.assertIn('研发驱动',matches['合成研究.txt']['snippet'])
        self.assertEqual(matches['研发驱动备忘录.md']['match'],'name')
        self.assertFalse(result['truncated'])
        self.assertEqual(self.service.list(self.root,'子目录','研发驱动')['entries'][0]['name'],'研发驱动备忘录.md')
        self.assertEqual(len(self.service.list(self.root,'子目录','研发驱动')['entries']),1)

    def test_actual_docx_content_is_read_by_bounded_disposable_parser(self):
        with zipfile.ZipFile(self.folder/'memo.docx','w') as archive:
            archive.writestr('word/document.xml','<w:document xmlns:w="urn:word"><w:p><w:t>合成产品壁垒</w:t></w:p></w:document>')
        result=self.service.search(self.root,'','产品壁垒')
        self.assertEqual(len(result['entries']),1)
        self.assertEqual(result['entries'][0]['path'],'memo.docx')
        self.assertIn('产品壁垒',result['entries'][0]['snippet'])

    def test_actual_pdf_content_search_uses_shipped_parser_not_user_imports(self):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
        writer=PdfWriter();page=writer.add_blank_page(width=600,height=800)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),
            NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
        stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 50 700 Td (Portable PDF evidence marker) Tj ET')
        page[NameObject('/Contents')]=writer._add_object(stream)
        writer.write(self.folder/'document.pdf');writer.close()
        # A registered directory is evidence, never Python's dependency path.
        (self.folder/'pypdf.py').write_text('raise RuntimeError("untrusted user module")',encoding='utf-8')
        result=self.service.search(self.root,'','Portable PDF evidence marker')
        self.assertEqual([(entry['path'],entry['match']) for entry in result['entries']],[('document.pdf','content')])
        self.assertIn('Portable PDF evidence marker',result['entries'][0]['snippet'])
        self.assertFalse(result['truncated'])

    def test_search_limits_are_visible_and_do_not_read_unbounded_tree(self):
        for index in range(8):(self.folder/f'match-{index}.txt').write_text('synthetic')
        with patch('suite.files.MAX_SEARCH_RESULTS',2):
            result=self.service.search(self.root,'','match')
        self.assertEqual(result['matched'],2);self.assertTrue(result['truncated']);self.assertTrue(result['reason'])
        with patch('suite.files.MAX_SEARCH_ENTRIES',3):
            result=self.service.search(self.root,'','absent')
        self.assertEqual(result['scanned'],3);self.assertTrue(result['truncated'])
        with patch('suite.files.time.monotonic',side_effect=[0,6,6]):
            result=self.service.search(self.root,'','absent')
        self.assertTrue(result['truncated']);self.assertEqual(result['scanned'],0)

    def test_complex_parser_timeout_is_not_successfully_complete_search(self):
        (self.folder/'first.pdf').write_bytes(b'%PDF synthetic')
        with patch('suite.files._search_document_text',side_effect=TimeoutError('fixture')):
            result=self.service.search(self.root,'','evidence')
        self.assertEqual(result['skipped'],1)
        self.assertTrue(result['truncated'])
        self.assertTrue(result['reason'])

    def test_invalid_scope_inputs_and_link_do_not_leak_foreign_content(self):
        for q in ('',None,'x'*201):
            with self.subTest(q=q),self.assertRaises(ValueError):self.service.search(self.root,'',q)
        with self.assertRaises(ValueError):self.service.search('missing','','source')
        for path in ('../outside','C:/outside','/outside'):
            with self.subTest(path=path),self.assertRaises(ValueError):self.service.search(self.root,path,'source')
        outside=self.base/'outside';outside.mkdir();(outside/'foreign.txt').write_text('FORBIDDEN_MARKER')
        link=self.folder/'shortcut'
        try:link.symlink_to(outside,target_is_directory=True)
        except OSError:
            if os.name!='nt':raise
            import subprocess
            result=subprocess.run([str(Path(os.environ['SystemRoot'])/'System32'/'cmd.exe'),'/d','/c','mklink','/J',str(link),str(outside)],capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0)
        result=self.service.search(self.root,'','FORBIDDEN_MARKER')
        self.assertEqual(result['entries'],[]);self.assertGreaterEqual(result['skipped'],1)

    def test_multiselect_clipboard_dedup_validates_entire_selection_before_native_write(self):
        (self.folder/'合成 & 副本.txt').write_text('synthetic');(self.folder/'subfolder').mkdir()
        with patch('suite.files._write_system_clipboard') as native:
            result=self.service.clipboard({'root_id':self.root,'paths':['合成 & 副本.txt','subfolder','合成 & 副本.txt'],'mode':'cut'})
            self.assertTrue(result['system_clipboard']);self.assertEqual(result['count'],2)
            native.assert_called_once_with([str(self.folder/'合成 & 副本.txt'),str(self.folder/'subfolder')],'cut')
        for paths in ([],[''],['合成 & 副本.txt','missing.txt'],['../outside'],[None]):
            with self.subTest(paths=paths),patch('suite.files._write_system_clipboard') as native:
                with self.assertRaises((ValueError,FileNotFoundError)):self.service.clipboard({'root_id':self.root,'paths':paths})
                native.assert_not_called()
        self.assertTrue((self.folder/'合成 & 副本.txt').exists())

    def test_original_app_open_passes_only_canonical_file_not_command_arguments(self):
        target=self.folder/'合成 & (副本).txt';target.write_text('synthetic')
        with patch('suite.files._open_system_path') as native:
            result=self.service.open({'root_id':self.root,'path':target.name})
            self.assertTrue(result['opened']);native.assert_called_once_with(target)
        for body in ({'root_id':self.root},{'root_id':self.root,'path':'../outside'},
                     {'root_id':self.root,'path':target.name,'command':['unexpected']}):
            with patch('suite.files._open_system_path') as native:
                with self.assertRaises(ValueError):self.service.open(body)
                native.assert_not_called()

    def test_native_clipboard_unicode_dropfiles_and_preferred_effect_ownership(self):
        user=SimpleNamespace(**{name:MagicMock(return_value=1) for name in (
            'CreateWindowExW','DestroyWindow','OpenClipboard','EmptyClipboard','CloseClipboard','SetClipboardData','RegisterClipboardFormatW')})
        kernel=SimpleNamespace(**{name:MagicMock() for name in ('GlobalAlloc','GlobalLock','GlobalUnlock','GlobalFree')})
        buffers={};published={}
        def allocate(flags,size):
            handle=len(buffers)+1;buffers[handle]=ctypes.create_string_buffer(size);return handle
        kernel.GlobalAlloc.side_effect=allocate
        kernel.GlobalLock.side_effect=lambda handle:ctypes.addressof(buffers[handle])
        user.RegisterClipboardFormatW.return_value=49321
        user.SetClipboardData.side_effect=lambda kind,handle:published.setdefault(kind,bytes(buffers[handle])) and handle
        paths=['C:\\Synthetic\\中文 文件.txt','C:\\Synthetic\\second.pdf']
        with patch('suite.files.os.name','nt'),patch('ctypes.WinDLL',side_effect=lambda name,**kw:user if name=='user32' else kernel,create=True):
            _write_system_clipboard(paths,'cut')
        self.assertEqual(published[15],_clipboard_payload(paths))
        self.assertEqual(struct.unpack('<IiiII',published[15][:20]),(20,0,0,0,1))
        self.assertEqual(published[15][20:].decode('utf-16le'),'\0'.join(paths)+'\0\0')
        self.assertEqual(struct.unpack('<I',published[49321]),(2,))
        kernel.GlobalFree.assert_not_called();user.CloseClipboard.assert_called_once();user.DestroyWindow.assert_called_once()


class FileWorkbenchHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name).resolve()
        self.folder=self.base/'selected';self.folder.mkdir();(self.folder/'中文.txt').write_text('合成内容搜索',encoding='utf-8')
        self.files=FileService(self.base/'private',[]);self.root=self.files.add_root(self.folder)['id']
        self.server=LocalServer(('127.0.0.1',0),Handler)
        self.server.app=SimpleNamespace(port=self.server.server_port,csrf='synthetic-csrf-token',files=self.files)
        self.origin=f'http://127.0.0.1:{self.server.server_port}'
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.thread.join(3);self.server.server_close();self.files.close();self.temp.cleanup()

    def request(self,method,path,body=None,headers=None):
        values={'Origin':self.origin,'X-CSRF-Token':'synthetic-csrf-token','Content-Type':'application/json',**(headers or {})}
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        try:
            connection.request(method,path,None if body is None else json.dumps(body).encode(),values)
            response=connection.getresponse();return response.status,json.loads(response.read())
        finally:connection.close()

    def test_real_search_route_and_missing_path_validation(self):
        status,result=self.request('GET','/api/suite/files/search?'+urlencode({'root_id':self.root,'path':'','q':'内容搜索'}))
        self.assertEqual(status,200);self.assertEqual(result['entries'][0]['path'],'中文.txt')
        self.assertEqual(self.request('POST','/api/suite/files/open',{'root_id':self.root})[0],400)

    def test_native_routes_are_same_origin_csrf_local_only_and_scope_checked(self):
        with patch('suite.files._write_system_clipboard') as clipboard,patch('suite.files._open_system_path') as opener:
            for action,body in [('clipboard',{'root_id':self.root,'paths':['中文.txt'],'mode':'copy'}),('open',{'root_id':self.root,'path':'中文.txt'})]:
                endpoint='/api/suite/files/'+action
                for headers in ({'Origin':'https://foreign.invalid'},{'X-CSRF-Token':''},{'Host':'foreign.invalid'},{'X-Forwarded-For':'198.51.100.1'}):
                    self.assertEqual(self.request('POST',endpoint,body,headers)[0],403)
                self.assertEqual(self.request('POST',endpoint,body)[0],200)
            clipboard.assert_called_once();opener.assert_called_once()
            self.assertEqual(self.request('POST','/api/suite/files/open',{'root_id':self.root,'path':'../outside'})[0],400)
            self.assertEqual(self.request('POST','/api/suite/files/clipboard',{'root_id':self.root,'paths':['missing.txt']})[0],404)
            self.assertEqual(clipboard.call_count,1);self.assertEqual(opener.call_count,1)


if __name__=='__main__':unittest.main()
