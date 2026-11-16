import hashlib
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from documents import document_text
from storage import validate_manifest, RequestError
from vision import coverage
from wiki import Wiki


class DocumentVisionTest(unittest.TestCase):
    def test_visual_model_must_return_complete_structured_content(self):
        from PIL import Image
        from vision import FIELDS, review_image
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'figure.png'
            Image.new('RGB', (20, 20)).save(path)
            for reason, content, valid in [('stop', json.dumps({k: '可见内容' for k in FIELDS}), True),
                                           ('length', '{}', False), ('stop', '{}', False)]:
                with patch('vision.urllib.request.build_opener') as opener:
                    opener.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps(
                        {'done_reason': reason, 'message': {'content': content}}).encode()
                    if valid:
                        self.assertEqual(review_image(path)['status'], 'reviewed')
                        request = opener.return_value.open.call_args.args[0]
                        self.assertTrue(json.loads(request.data)['messages'][1]['images'][0])
                    else:
                        with self.assertRaises(ValueError):
                            review_image(path)

    def test_manifest_accepts_documents_but_rejects_wrong_kind(self):
        for name in ('paper.pdf', 'paper.docx', 'paper.doc'):
            item = dict(version=1, id=str(uuid.uuid4()), captured_at='2026-11-16T12:00:00+08:00',
                        trigger='share', attachments=[dict(id=str(uuid.uuid4()), name=name,
                        kind='document', size=4, sha256=hashlib.sha256(b'test').hexdigest())])
            self.assertEqual(validate_manifest(item)['attachments'][0]['kind'], 'document')
            item['attachments'][0]['kind'] = 'video'
            with self.assertRaises(RequestError):
                validate_manifest(item)

    def test_docx_paragraphs_and_tables_have_locations(self):
        from docx import Document
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'paper.docx'
            doc = Document()
            doc.add_paragraph('Measured abrasion')
            doc.add_table(rows=1, cols=1).cell(0, 0).text = '12 mm'
            doc.save(path)
            rows = list(document_text(path))
            self.assertEqual([r[1] for r in rows], ['Measured abrasion', '12 mm'])
            self.assertNotEqual(rows[0][0], rows[1][0])

    def test_scanned_pdf_is_explicit_and_doc_is_preserved_for_conversion(self):
        from pypdf import PdfWriter
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'scan.pdf'
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            writer.write(path)
            self.assertIn('OCR', list(document_text(path))[0][1])
            with self.assertRaisesRegex(ValueError, 'DOCX'):
                list(document_text(Path(folder) / 'old.doc'))

    def test_ocr_does_not_count_as_visual_review(self):
        source = {'assets': {'a': {'kind': 'image'}, 'v': {'kind': 'video'}},
                  'analysis': {'done': True, 'rows': [{'text': 'OCR'}]}}
        self.assertEqual(coverage(source), dict(reviewed=0, total=1, complete=False))
        source['visual_reviews'] = {'a': {'status': 'pending'}}
        self.assertFalse(coverage(source)['complete'])
        source['visual_reviews']['a']['status'] = 'reviewed'
        self.assertTrue(coverage(source)['complete'])

    def test_visual_review_runs_for_old_done_source_and_is_cached(self):
        with tempfile.TemporaryDirectory() as folder:
            wiki = Wiki(Path(folder), Path(folder) / 'state', False, True)
            source = dict(id='S0001', title='科研绘图', category='科研绘图',
                          assets={'abc': dict(kind='image', path='figure.png')}, captures={},
                          analysis=dict(done=True, rows=[]))
            wiki.catalog[source['id']] = source
            result = dict(version=1, status='reviewed', model='fixture', observations={'布局': '两面板'})
            with patch('wiki.review_image', return_value=result) as review:
                updated = wiki.analyze_images(source)
                self.assertTrue(coverage(updated)['complete'])
                updated.pop('visual_retry_after')
                wiki.analyze_images(updated)
                self.assertEqual(review.call_count, 1)

    def test_failed_visual_review_stays_pending(self):
        with tempfile.TemporaryDirectory() as folder:
            wiki = Wiki(Path(folder), Path(folder) / 'state', False, True)
            source = dict(id='S0001', title='科研绘图', captures={}, assets={'abc': dict(kind='image', path='figure.png')})
            with patch('wiki.review_image', side_effect=OSError('offline')):
                result = wiki.analyze_images(source)
            self.assertEqual(result['visual_reviews']['abc']['status'], 'pending')
            self.assertFalse(coverage(result)['complete'])
