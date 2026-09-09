import os


def extract_text(filepath):
    """Best-effort text extraction from PDF, DOCX, TXT and CSV files."""
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext == ".pdf":
            import pdfplumber
            pages = []
            with pdfplumber.open(filepath) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        pages.append(text)
            return "\n".join(pages)

        if ext == ".docx":
            import docx
            document = docx.Document(filepath)
            return "\n".join(p.text for p in document.paragraphs)

        if ext in {".txt", ".csv"}:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
    except Exception:
        return ""

    return ""
