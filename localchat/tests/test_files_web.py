"""Tests for reading uploaded files, keyword search, and the web helpers (no internet needed)."""
import importlib.util
import io
import json
import shutil
import tempfile
import unittest
import urllib.request
import zipfile
from pathlib import Path

from localchat import chat, config, files, web

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def make_zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in entries.items():
            zf.writestr(name, text)
    return buf.getvalue()


def make_pdf(lines):
    """A tiny valid PDF with one page of text (built by hand)."""
    stream = "BT /F1 12 Tf 72 720 Td " + " ".join("(%s) Tj 0 -16 Td" % l for l in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        "<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = "%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += "%d 0 obj\n%s\nendobj\n" % (i, o)
    xref = len(out)
    out += "xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += "".join("%010d 00000 n \n" % off for off in offsets)
    out += "trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out.encode("latin-1")


class TextExtraction(unittest.TestCase):
    def test_plain_text_and_encodings(self):
        self.assertEqual(files.decode_text("héllo".encode("utf-8")), "héllo")
        self.assertEqual(files.decode_text("中文".encode("big5")), "中文")
        self.assertEqual(files.decode_text(b"\xef\xbb\xbfbom"), "bom")
        self.assertIsNone(files.decode_text(b"\x00\x01binary"))
        text, _ = files.extract_text("a.py", b"print('hi')\n")
        self.assertIn("print", text)
        with self.assertRaises(files.UploadError):
            files.extract_text("a.bin", b"\x00\x00\x00")

    def test_docx(self):
        doc = ('<w:document %s><w:body>'
               '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Title</w:t></w:r></w:p>'
               '<w:p><w:r><w:t>Hello</w:t></w:r><w:r><w:tab/><w:t>world</w:t></w:r></w:p>'
               '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc>'
               '<w:tc><w:p><w:r><w:t>B</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
               '</w:body></w:document>') % W
        text, _ = files.extract_text("r.docx", make_zip({"word/document.xml": doc}))
        self.assertEqual(text, "## Title\nHello\tworld\n| A | B |")

    def test_xlsx(self):
        ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        rns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
        data = make_zip({
            "xl/workbook.xml": '<workbook %s %s><sheets><sheet name="Grades" sheetId="1" r:id="rId1"/></sheets></workbook>' % (ns, rns),
            "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                          '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
            "xl/sharedStrings.xml": '<sst %s><si><t>Name</t></si><si><t>Score</t></si><si><t>Ann, Lee</t></si></sst>' % ns,
            "xl/worksheets/sheet1.xml": '<worksheet %s><sheetData>'
                                        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                                        '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="C2"><v>97</v></c></row>'
                                        '</sheetData></worksheet>' % ns,
        })
        text, _ = files.extract_text("g.xlsx", data)
        self.assertEqual(text, '## Sheet: Grades\nName,Score\n"Ann, Lee",,97')

    def test_pptx_and_ipynb(self):
        a = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
        slide = '<p:sld xmlns:p="x" %s><a:p><a:r><a:t>Intro</a:t></a:r></a:p><a:p><a:r><a:t>Point</a:t></a:r></a:p></p:sld>' % a
        text, _ = files.extract_text("s.pptx", make_zip({"ppt/slides/slide2.xml": slide, "ppt/slides/slide10.xml": slide}))
        self.assertTrue(text.startswith("## Slide 1\nIntro\nPoint"))
        nb = {"cells": [{"cell_type": "markdown", "source": ["# Hi"]},
                        {"cell_type": "code", "source": ["1+1"], "outputs": [{"text": ["2"]}]}],
              "metadata": {"kernelspec": {"language": "python"}}}
        text, _ = files.extract_text("n.ipynb", json.dumps(nb).encode())
        self.assertIn("```python\n1+1\n```", text)
        self.assertIn("Output:\n```\n2\n```", text)

    def test_pdf(self):
        if importlib.util.find_spec("pypdf") is None:
            self.skipTest("pypdf not installed")
        text, note = files.extract_text("paper.pdf", make_pdf(["Attention is all you need", "Section 2"]))
        self.assertIn("[Page 1]", text)
        self.assertIn("Attention is all you need", text)
        self.assertEqual(note, "1 pages")
        text, note = files.extract_text("scan.pdf", make_pdf([]))
        self.assertIn("scanned", note)
        with self.assertRaises(files.UploadError):
            files.extract_text("bad.pdf", b"%PDF-1.4 garbage")


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = files.FileStore(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_save_read_claim_delete(self):
        meta = self.store.save("../../etc/notes.txt", b"hello world")
        self.assertEqual(meta["kind"], "text")
        self.assertEqual(meta["name"], "notes.txt")          # path parts are removed
        self.assertEqual(self.store.text(meta["id"]), "hello world")
        img = self.store.save("pic.png", b"\x89PNG fake")
        self.assertEqual(img["kind"], "image")
        self.store.claim(meta["id"], "chat1")
        self.store.delete_for_chat("chat1")
        with self.assertRaises(files.UploadError):
            self.store.meta(meta["id"])
        self.store.meta(img["id"])                            # other files are kept
        with self.assertRaises(files.UploadError):
            self.store.meta("../../settings")
        with self.assertRaises(files.UploadError):
            self.store.save("empty.txt", b"")


class Retrieval(unittest.TestCase):
    def test_tokenize(self):
        self.assertEqual(files.tokenize("Hello, World! a 7"), ["hello", "world", "7"])
        self.assertEqual(files.tokenize("機器學習"), ["機器", "器學", "學習"])

    def test_chunks_cover_text(self):
        text = "\n\n".join("Paragraph %d " % i + "word " * 60 for i in range(40))
        chunks = files.chunk_text(text, size=1000, overlap=100)
        self.assertEqual(chunks[0][0], 0)
        self.assertTrue(all(len(c) <= 1000 for _, c in chunks))
        self.assertTrue(text.endswith(chunks[-1][1]))

    def test_bm25_and_excerpt(self):
        docs = ["the cat sat", "dogs bark loudly", "a cat and a dog"]
        ranked = files.bm25_rank(docs, "cat")
        self.assertEqual({i for _, i in ranked}, {0, 2})
        long = "Intro section.\n\n" + ("filler text here. " * 100 + "\n\n") * 20 + "The secret code is 4417.\n\n" + ("more filler. " * 100 + "\n\n") * 20
        cut, was_cut = chat.excerpt(long, "what is the secret code", 4000)
        self.assertTrue(was_cut)
        self.assertTrue(cut.startswith("Intro section."))
        self.assertIn("secret code is 4417", cut)
        self.assertLessEqual(len(cut), 4200)

    def test_token_estimate(self):
        self.assertEqual(chat.estimate_tokens(""), 0)
        self.assertAlmostEqual(chat.estimate_tokens("a" * 3600), 1001, delta=2)
        self.assertEqual(chat.estimate_tokens("你好"), 3)


DDG_LITE = """<table><tr><td><a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fx%3D1&amp;rut=z" class='result-link'>Example &amp; Co</a></td></tr>
<tr><td class='result-snippet'>Snippet <b>one</b></td></tr>
<tr><td><a rel="nofollow" href="https://example.org/b" class='result-link'>Second</a></td></tr>
<tr><td class='result-snippet'>Snippet two</td></tr></table>"""


class Web(unittest.TestCase):
    def test_parse_duckduckgo_html_page(self):
        import tests.fake_ollama as fake
        results = web.parse_duckduckgo(fake.DDG_PAGE.replace("__BASE__", "https%3A%2F%2Fa.example"))
        self.assertEqual([r["url"] for r in results], ["https://a.example/article", "https://qwen.example/blog/qwen3.5"])
        self.assertEqual(results[0]["title"], "Ollama 0.12 release notes")
        self.assertEqual(results[0]["snippet"], "Ollama 0.12 adds web search and faster model loading.")

    def test_parse_duckduckgo_lite_page(self):
        results = web.parse_duckduckgo(DDG_LITE)
        self.assertEqual(results[0], {"title": "Example & Co", "url": "https://example.com/a?x=1", "snippet": "Snippet one"})
        self.assertEqual(results[1]["url"], "https://example.org/b")

    def test_html_to_text(self):
        import tests.fake_ollama as fake
        title, text = web.html_to_text(fake.ARTICLE)
        self.assertEqual(title, "Ollama 0.12 release notes")
        self.assertIn("This release adds a web search API", text)
        for junk in ("Home", "cookies", "Copyright", "tracking"):
            self.assertNotIn(junk, text)
        _, text = web.html_to_text('<div class="has-sidebar"><p>%s</p></div><div class="sidebar-box">ads</div>' % ("kept " * 100))
        self.assertIn("kept", text)
        self.assertNotIn("ads", text)

    def test_private_addresses_blocked(self):
        self.assertFalse(web.ALLOW_PRIVATE)
        for url in ("http://127.0.0.1:11434/api/tags", "http://localhost/", "http://192.168.1.1/",
                    "http://10.0.0.5/", "http://[::1]/", "http://169.254.169.254/latest/meta-data"):
            with self.assertRaises(web.WebError, msg=url):
                web.check_public_url(url)
        with self.assertRaises(web.WebError):
            web.check_public_url("file:///etc/passwd")
        web.check_public_url("https://93.184.215.14/")        # a public address is fine
        req = urllib.request.Request("https://93.184.215.14/")
        with self.assertRaises(web.WebError):                  # a redirect to this computer is refused
            web._SafeRedirect().redirect_request(req, None, 302, "Found", {}, "http://127.0.0.1/admin")

    def test_backend_choice(self):
        calls = []
        orig = (web.search_ollama, web.search_duckduckgo)
        web.search_ollama = lambda q, k, n=6: calls.append("ollama") or [{"title": "t", "url": "u", "snippet": ""}]
        web.search_duckduckgo = lambda q, n=6: calls.append("ddg") or []
        try:
            self.assertEqual(web.web_search("q", "auto", "")[1], "duckduckgo")
            self.assertEqual(web.web_search("q", "auto", "key")[1], "ollama")
            with self.assertRaises(web.WebError):
                web.web_search("q", "ollama", "")
        finally:
            web.search_ollama, web.search_duckduckgo = orig
        self.assertEqual(calls, ["ddg", "ollama"])


class Config(unittest.TestCase):
    def test_normalize_url(self):
        n = config.normalize_url
        self.assertEqual(n("0.0.0.0"), "http://127.0.0.1:11434")
        self.assertEqual(n("127.0.0.1:11500"), "http://127.0.0.1:11500")
        self.assertEqual(n("https://ollama.example/"), "https://ollama.example:443")
        self.assertEqual(n("http://[::1]:11434"), "http://[::1]:11434")

    def test_settings_hide_key(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            s = config.Settings(tmp)
            s.update({"ollama_api_key": "secret-123", "num_ctx": 999999, "temperature": "", "bogus": 1})
            pub = s.public()
            self.assertEqual(pub["ollama_api_key"], "")
            self.assertTrue(pub["ollama_api_key_set"])
            self.assertEqual(pub["num_ctx"], 262144)
            self.assertIsNone(pub["temperature"])
            self.assertNotIn("bogus", pub)
            s.update({"ollama_api_key": ""})                     # empty field keeps the key
            self.assertEqual(config.Settings(tmp).api_key(), "secret-123")
            s.update({"ollama_api_key_clear": True})
            self.assertEqual(config.Settings(tmp).get("ollama_api_key"), "")
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
