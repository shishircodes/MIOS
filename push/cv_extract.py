"""Plain text out of a CV file. No LLM, no network.

Handles the two formats the BD team actually sends: Word (.docx) and PDF.

.docx is read with the standard library rather than python-docx. A .docx is a
ZIP of XML, and all we need is the text inside `<w:t>` elements — that is a
dozen lines of stdlib versus another dependency to install and pin.

Legacy .doc (the pre-2007 binary format) is *not* supported and says so
explicitly: it shares an extension family with .docx but is a completely
different container, and silently returning mojibake would be worse than a
clear error.
"""
from __future__ import annotations

import logging
import re
import zipfile
from io import BytesIO
from pathlib import Path

log = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = (".docx", ".pdf")

#: Refuse absurd inputs early. A CV is a few pages; anything far larger is a
#: mistake or an attempt to exhaust memory on the server.
MAX_BYTES = 10 * 1024 * 1024


class CVExtractionError(Exception):
    """The file could not be read as a CV. The message is shown to the user."""


# --------------------------------------------------------------------------
# .docx
# --------------------------------------------------------------------------

_W_TAG = re.compile(r"<w:(t|tab|br|p)\b[^>]*>", re.I)
_TEXT_RUN = re.compile(r"<w:t\b[^>]*>(.*?)</w:t>", re.I | re.S)
_XML_ENTITY = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'"}


def _unescape(s: str) -> str:
    for k, v in _XML_ENTITY.items():
        s = s.replace(k, v)
    return s


def _docx_to_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(data)) as z:
            names = set(z.namelist())
            if "word/document.xml" not in names:
                raise CVExtractionError(
                    "That .docx has no document body. If it is an older .doc file, "
                    "re-save it as .docx or PDF first."
                )
            xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    except zipfile.BadZipFile as exc:
        raise CVExtractionError(
            "That file is not a valid .docx. Word 97–2003 .doc files are not "
            "supported — re-save as .docx or PDF."
        ) from exc

    # Tabs and line breaks inside a paragraph become spaces; the paragraph
    # boundary itself is what separates lines and must survive.
    xml = re.sub(r"<w:(tab|br)\b[^>]*/?>", " ", xml, flags=re.I)

    if not _TEXT_RUN.search(xml):
        raise CVExtractionError("No readable text found in that .docx.")

    # Split on the paragraph close tag and join the runs within each paragraph.
    # Word fragments a single visual line across many <w:r> elements, so the
    # runs must be concatenated per paragraph rather than one-line-per-run —
    # and the paragraph boundary must still be intact at this point, which is
    # why nothing above rewrites </w:p>. Line structure carries meaning in a CV:
    # the name sits alone on the first line.
    out: list[str] = []
    for chunk in re.split(r"(?i)</w:p>", xml):
        line = "".join(_unescape(m.group(1)) for m in _TEXT_RUN.finditer(chunk))
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            out.append(line)
    return "\n".join(out)


# --------------------------------------------------------------------------
# .pdf
# --------------------------------------------------------------------------


def _pdf_to_text(data: bytes) -> str:
    try:
        import pypdf
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise CVExtractionError(
            "PDF support needs the pypdf package (pip install -e \".[api]\")."
        ) from exc

    try:
        reader = pypdf.PdfReader(BytesIO(data))
        if reader.is_encrypted:
            # An empty-password decrypt covers "protected but not really".
            try:
                reader.decrypt("")
            except Exception:
                raise CVExtractionError("That PDF is password protected.")
        pages = [(p.extract_text() or "") for p in reader.pages]
    except CVExtractionError:
        raise
    except Exception as exc:  # noqa: BLE001 - pypdf raises a wide range
        raise CVExtractionError(f"Could not read that PDF ({exc}).") from exc

    text = "\n".join(pages)
    if not text.strip():
        raise CVExtractionError(
            "No text found in that PDF — it may be a scan. Send a text-based PDF "
            "or a .docx, or enter the details on the form."
        )
    return text


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------


def extract_text(data: bytes, filename: str) -> str:
    """Plain text from CV bytes. Raises CVExtractionError with a usable message.

    `filename` selects the reader — the bytes are never sniffed, because a
    mislabelled file should fail loudly rather than be guessed at.
    """
    if not data:
        raise CVExtractionError("That file is empty.")
    if len(data) > MAX_BYTES:
        raise CVExtractionError(
            f"That file is {len(data) // (1024 * 1024)} MB; the limit is "
            f"{MAX_BYTES // (1024 * 1024)} MB."
        )

    suffix = Path(filename or "").suffix.lower()
    if suffix == ".docx":
        text = _docx_to_text(data)
    elif suffix == ".pdf":
        text = _pdf_to_text(data)
    elif suffix == ".doc":
        raise CVExtractionError(
            "Word 97–2003 (.doc) is not supported. Open it in Word and use "
            "Save As → .docx or PDF."
        )
    else:
        raise CVExtractionError(
            f"Unsupported file type '{suffix or filename}'. Upload a .docx or .pdf."
        )

    # Normalise whitespace once here so the parser downstream can assume clean,
    # line-separated text regardless of which reader produced it.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [ln.strip() for ln in text.split("\n")]
    cleaned = "\n".join(ln for ln in lines if ln)

    if len(cleaned) < 40:
        raise CVExtractionError(
            "That file has almost no readable text. Check it is the right file, "
            "or enter the details on the form."
        )
    log.info("cv_extract: %s -> %d characters", filename, len(cleaned))
    return cleaned
