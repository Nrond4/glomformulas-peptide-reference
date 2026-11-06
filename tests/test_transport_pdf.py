import importlib.util
import io
import unittest
import urllib.error
from unittest.mock import patch
from gloformulas.http import Client,FetchError
from gloformulas.freedom import extract_pdf


def pdf_fixture(lines):
    escaped=[line.replace('\\','\\\\').replace('(','\\(').replace(')','\\)') for line in lines]
    stream=('BT /F1 12 Tf 30 760 Td '+ ' 0 -16 Td '.join('('+s+') Tj' for s in escaped)+' ET').encode()
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
    data=b'%PDF-1.4\n';offsets=[0]
    for i,obj in enumerate(objects,1):offsets.append(len(data));data+=f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n'
    xref=len(data);data+=f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode()+b''.join(f'{n:010d} 00000 n \n'.encode() for n in offsets[1:])
    return data+f'trailer << /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()

class TransportTests(unittest.TestCase):
    def test_403_not_retried(self):
        client=Client({'example.org'},interval=0)
        with patch.object(client.opener,'open',side_effect=urllib.error.HTTPError('https://example.org',403,'Denied',{},None)) as opened:
            with self.assertRaises(FetchError):client.get('https://example.org')
        self.assertEqual(opened.call_count,1)
    def test_429_retry_is_bounded(self):
        client=Client({'example.org'},interval=0,attempts=2)
        with patch('gloformulas.http.time.sleep'),patch.object(client.opener,'open',side_effect=urllib.error.HTTPError('https://example.org',429,'Later',{'Retry-After':'1'},None)) as opened:
            with self.assertRaises(FetchError):client.get('https://example.org')
        self.assertEqual(opened.call_count,2)
    def test_long_retry_deferred(self):
        client=Client({'example.org'},interval=0)
        with patch.object(client.opener,'open',side_effect=urllib.error.HTTPError('https://example.org',429,'Later',{'Retry-After':'600'},None)) as opened:
            with self.assertRaises(FetchError):client.get('https://example.org')
        self.assertEqual(opened.call_count,1)
    def test_body_limit(self):
        class Reply(io.BytesIO):
            url='https://example.org';headers={};status=200
        client=Client({'example.org'},interval=0,max_bytes=5)
        with patch.object(client.opener,'open',return_value=Reply(b'123456')):
            with self.assertRaises(FetchError):client.get('https://example.org')
    @unittest.skipUnless(importlib.util.find_spec('pypdf'),'Install lab extra to exercise PDF extraction')
    def test_real_pdf_text_extraction(self):
        fields,pages=extract_pdf(pdf_fixture(['Search Code: GLO123','Company: GloFormulas','Identity: Example','Lot: B1','Purity: 99.1%','Net Content: 10.5mg','Report Date: 2020-01-01']))
        self.assertEqual(fields['search_code'],'GLO123');self.assertEqual(fields['batch'],'B1');self.assertEqual(fields['purity'],'99.1%');self.assertEqual(len(pages),1)
    def test_html_not_accepted_as_pdf(self):
        with self.assertRaises(ValueError):extract_pdf(b'<html>Access denied</html>')
