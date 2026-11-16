"""Read document evidence with page/paragraph locations; never execute Office macros."""


def document_text(path):
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(path)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF 已加密，请先解密后重新上传")
        for number, page in enumerate(reader.pages, 1):
            text = (page.extract_text() or "").strip()
            if not text:
                text = "[本页未提取到文字，可能是扫描页；需 OCR 或人工阅读]"
            yield f"第 {number} 页", text
    elif path.suffix.lower() == ".docx":
        from docx import Document
        doc = Document(path)
        for number, paragraph in enumerate(doc.paragraphs, 1):
            if paragraph.text.strip():
                yield f"段落 {number}", paragraph.text.strip()
        for number, table in enumerate(doc.tables, 1):
            for row_number, row in enumerate(table.rows, 1):
                yield f"表 {number} 行 {row_number}", " | ".join(c.text for c in row.cells)
    else:
        raise ValueError("DOC 原文件已保存；请转换为 DOCX 后补传以提取正文")
