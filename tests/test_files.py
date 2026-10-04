from __future__ import annotations
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from suite.files import FileService,FileConflict,_linked


class FileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name).resolve()
        self.first=self.folder/'first';self.second=self.folder/'second';self.first.mkdir();self.second.mkdir()
        self.service=FileService(self.folder/'data',[])
        self.a=self.service.add_root(self.first,'合成甲')['id'];self.b=self.service.add_root(self.second,'合成乙')['id']

    def tearDown(self):self.service.close();self.temp.cleanup()

    def operation(self,operation,path='',**values):
        return self.service.operation({'operation':operation,'root_id':self.a,'path':path,**values})

    def test_real_copy_move_trash_restore_and_non_overwrite(self):
        (self.first/'original.txt').write_text('synthetic original',encoding='utf-8')
        copy=self.operation('copy','original.txt',target='copy.txt',request_id='copy-request-001')
        self.assertEqual(copy['status'],'completed');self.assertEqual((self.first/'copy.txt').read_text(),'synthetic original')
        moved=self.operation('move','copy.txt',target='moved.txt',destination_root_id=self.b)
        self.assertFalse((self.first/'copy.txt').exists());self.assertEqual((self.second/'moved.txt').read_text(),'synthetic original')
        with self.assertRaises(FileConflict):self.operation('copy','original.txt',target='moved.txt',destination_root_id=self.b)
        trash=self.operation('trash','original.txt')
        self.assertFalse((self.first/'original.txt').exists());self.assertTrue(trash['restore_available'])
        restored=self.service.operation({'operation':'restore','receipt_id':trash['id'],'request_id':'restore-request-001'})
        self.assertEqual(restored['status'],'completed');self.assertEqual((self.first/'original.txt').read_text(),'synthetic original')
        with self.assertRaises(FileConflict):self.service.operation({'operation':'restore','receipt_id':trash['id']})

    def test_receipts_survive_restart_and_same_id_never_repeats_action(self):
        (self.first/'a.txt').write_text('synthetic',encoding='utf-8')
        body={'operation':'move','root_id':self.a,'path':'a.txt','target':'b.txt','request_id':'move-request-001'}
        initial=self.service.operation(body);self.service.close();self.service=FileService(self.folder/'data',[])
        self.assertEqual(self.service.operation(body),initial)
        self.assertFalse((self.first/'a.txt').exists());self.assertEqual((self.first/'b.txt').read_text(),'synthetic')
        with self.assertRaises(FileConflict):self.service.operation({**body,'target':'c.txt'})
        self.assertFalse((self.first/'c.txt').exists())

    def test_directory_mkdir_rename_copy_recycle_restores_tree(self):
        self.operation('mkdir','newdir');(self.first/'newdir'/'inside.txt').write_text('synthetic nested')
        self.operation('rename','newdir',target='renamed')
        self.operation('copy','renamed',target='copied')
        trash=self.operation('trash','renamed')
        self.service.operation({'operation':'restore','receipt_id':trash['id']})
        self.assertEqual((self.first/'renamed'/'inside.txt').read_text(),'synthetic nested')
        self.assertEqual((self.first/'copied'/'inside.txt').read_text(),'synthetic nested')

    def test_restore_rejects_changed_destination_and_changed_recycled_content(self):
        (self.first/'a.txt').write_text('synthetic first')
        trash=self.operation('trash','a.txt');(self.first/'a.txt').write_text('synthetic replacement')
        with self.assertRaises(FileConflict):self.service.operation({'operation':'restore','receipt_id':trash['id']})
        self.assertEqual((self.first/'a.txt').read_text(),'synthetic replacement')
        (self.first/'a.txt').unlink();recycled=self.service.recycle/trash['recycle_id'];recycled.write_text('changed local copy')
        with self.assertRaises(FileConflict):self.service.operation({'operation':'restore','receipt_id':trash['id']})
        self.assertFalse((self.first/'a.txt').exists())

    def test_path_traversal_reserved_names_and_root_operations_fail_closed(self):
        for path in ('../outside.txt','/absolute.txt','C:\\outside.txt','inside/../../outside','NUL.txt','folder:a','a/./b'):
            with self.subTest(path=path),self.assertRaises(ValueError):self.service.resolve(self.a,path,must_exist=False)
        with self.assertRaises(ValueError):self.operation('trash','')
        with self.assertRaises(ValueError):self.operation('move','',target='inside')
        self.assertFalse((self.folder/'outside.txt').exists())

    def test_linked_paths_do_not_escape_or_enter_operation(self):
        outside=self.folder/'outside';outside.mkdir();(outside/'secret.txt').write_text('synthetic outside')
        link=self.first/'link'
        def make_link(path):
            try:path.symlink_to(outside,target_is_directory=True)
            except OSError:
                if os.name!='nt':raise
                # Windows junction creation does not need Developer Mode/admin.
                result=subprocess.run([str(Path(os.environ['SystemRoot'])/'System32'/'cmd.exe'),'/d','/c','mklink','/J',str(path),str(outside)],capture_output=True,timeout=10)
                if result.returncode:raise OSError('Synthetic junction creation failed')
        make_link(link)
        with self.assertRaises(ValueError):self.service.resolve(self.a,'link/secret.txt')
        self.assertNotIn('link',[e['name'] for e in self.service.list(self.a)['entries']])
        (self.first/'tree').mkdir();make_link(self.first/'tree'/'link')
        with self.assertRaises(ValueError):self.operation('copy','tree',target='copy-tree')
        self.assertFalse((self.first/'copy-tree').exists());self.assertEqual((outside/'secret.txt').read_text(),'synthetic outside')

    def test_known_onedrive_cloud_tags_allowed_unknown_reparse_denied(self):
        point=self.first/'placeholder'
        for tag in (0x9000001A,0x9000F01A):
            with patch.object(Path,'lstat',return_value=SimpleNamespace(st_mode=stat.S_IFDIR,st_file_attributes=0x400,st_reparse_tag=tag)):
                self.assertFalse(_linked(point))
        for tag in (0,0xA0000003,0xA000000C,0x9001001A):
            with patch.object(Path,'lstat',return_value=SimpleNamespace(st_mode=stat.S_IFDIR,st_file_attributes=0x400,st_reparse_tag=tag)):
                self.assertTrue(_linked(point))

    def test_text_html_office_xml_and_archive_previews_do_not_execute(self):
        (self.first/'text.txt').write_text('合成文本',encoding='utf-8')
        self.assertEqual(self.service.preview(self.a,'text.txt')['content'],'合成文本')
        (self.first/'page.html').write_text('<h1>Synthetic heading</h1><script>UNSAFE_SCRIPT_MARKER</script><style>UNSAFE_STYLE</style>safe body')
        html=self.service.preview(self.a,'page.html');self.assertEqual(html['type'],'text')
        self.assertIn('Synthetic heading',html['content']);self.assertNotIn('UNSAFE',html['content']);self.assertNotIn('<script>',html['content'])
        with zipfile.ZipFile(self.first/'memo.docx','w') as archive:archive.writestr('word/document.xml','<w:document xmlns:w="urn:word"><w:p><w:t>Synthetic memo</w:t></w:p></w:document>')
        self.assertIn('Synthetic memo',self.service.preview(self.a,'memo.docx')['content'])
        with zipfile.ZipFile(self.first/'deck.pptx','w') as archive:
            archive.writestr('ppt/slides/slide2.xml','<s xmlns:a="urn:a"><a:t>Second</a:t></s>')
            archive.writestr('ppt/slides/slide1.xml','<s xmlns:a="urn:a"><a:t>First</a:t></s>')
        self.assertEqual(self.service.preview(self.a,'deck.pptx')['content'],'First\nSecond')
        with zipfile.ZipFile(self.first/'archive.zip','w') as archive:archive.writestr('../../not-extracted.txt','synthetic')
        preview=self.service.preview(self.a,'archive.zip');self.assertEqual(preview['type'],'archive')
        self.assertFalse((self.folder/'not-extracted.txt').exists())

    def test_zip_bomb_and_xml_entities_are_rejected(self):
        with zipfile.ZipFile(self.first/'bomb.docx','w',compression=zipfile.ZIP_DEFLATED) as archive:archive.writestr('word/document.xml','A'*200_000)
        with self.assertRaisesRegex(ValueError,'压缩'):self.service.preview(self.a,'bomb.docx')
        with zipfile.ZipFile(self.first/'entities.docx','w') as archive:archive.writestr('word/document.xml','<!DOCTYPE t [<!ENTITY e "expanded">]><t>&e;</t>')
        with self.assertRaisesRegex(ValueError,'XML'):self.service.preview(self.a,'entities.docx')

    def test_xlsx_and_pdf_real_bounded_parsers(self):
        import openpyxl
        book=openpyxl.Workbook();sheet=book.active;sheet.title='Synthetic sheet';sheet.append(['Metric','Value']);sheet.append(['Synthetic count',12]);sheet.cell(250,1,'beyond limit')
        book.save(self.first/'model.xlsx');book.close()
        preview=self.service.preview(self.a,'model.xlsx')
        self.assertEqual(preview['type'],'table');self.assertEqual(preview['rows'][1],['Synthetic count',12]);self.assertLessEqual(len(preview['rows']),200)
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
        writer=PdfWriter();page=writer.add_blank_page(width=600,height=800)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
        stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 50 700 Td (Synthetic PDF text) Tj ET')
        page[NameObject('/Contents')]=writer._add_object(stream)
        writer.write(self.first/'document.pdf');writer.close()
        self.assertIn('Synthetic PDF text',self.service.preview(self.a,'document.pdf')['content'])


if __name__=='__main__':unittest.main()
