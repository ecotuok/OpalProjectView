"""Markdown -> HTML for Opal: tables, fenced code (pygments), admonitions, task
lists, a heading TOC, reading time, and rewriting of relative links/images so they
resolve inside the workspace (images via /api/asset, *.md links handled in-app)."""

import html as _html
import os
import posixpath
import re

import markdown
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

EXTENSIONS = [
    "extra",            # tables, fenced_code, footnotes, def lists, abbreviations
    "admonition",
    "sane_lists",
    "codehilite",
    "toc",
    "pymdownx.tasklist",
    "pymdownx.tilde",
]
EXT_CONFIG = {
    "codehilite": {"guess_lang": False, "css_class": "hl"},
    "toc": {"permalink": False, "toc_depth": "2-4"},
    "pymdownx.tasklist": {"custom_checkbox": True},
}

WORDS_PER_MIN = 210
_ATTR = re.compile(r'(?P<attr>src|href)="(?P<val>[^"]+)"')
_EXTERNAL = re.compile(r'^(?:[a-z]+:|//|#|mailto:|data:)', re.I)


def _flatten_toc(tokens, out):
    for t in tokens:
        out.append({"level": t["level"], "id": t["id"], "text": t["name"]})
        if t.get("children"):
            _flatten_toc(t["children"], out)
    return out


def _resolve(project: str, doc_dir: str, rel: str):
    """Map a relative link to (target_project, target_path), both ROOT-relative."""
    rel = rel.split("#")[0].split("?")[0]
    base = doc_dir if project == "_workspace" else posixpath.join(project, doc_dir)
    full = posixpath.normpath(posixpath.join(base, rel)).lstrip("./").lstrip("/")
    parts = full.split("/")
    if len(parts) == 1:
        return "_workspace", parts[0]
    return parts[0], "/".join(parts[1:])


def _rewrite_links(html: str, project: str, doc_rel: str) -> str:
    doc_dir = posixpath.dirname(doc_rel)

    def repl(m):
        attr, val = m.group("attr"), m.group("val")
        if _EXTERNAL.match(val):
            if attr == "href":
                return f'{attr}="{val}" target="_blank" rel="noopener"'
            return m.group(0)
        tp, tpath = _resolve(project, doc_dir, val)
        if attr == "href" and tpath.lower().endswith(".md"):
            # in-app navigation — handled by app.js
            return (f'href="#" class="md-link" '
                    f'data-project="{tp}" data-doc="{tpath}"')
        # images and other relative files -> asset endpoint
        return f'{attr}="/api/asset?project={tp}&path={tpath}"'

    return _ATTR.sub(repl, html)


def render(project: str, doc_rel: str, text: str) -> dict:
    md = markdown.Markdown(extensions=EXTENSIONS, extension_configs=EXT_CONFIG,
                           output_format="html5")
    html = md.convert(text)
    html = _rewrite_links(html, project, doc_rel)
    toc = _flatten_toc(getattr(md, "toc_tokens", []), [])
    words = len(text.split())
    # first H1 as a title hint
    m = re.search(r"^#\s+(.+)$", text, re.M)
    title = m.group(1).strip() if m else doc_rel.rsplit("/", 1)[-1]
    return {
        "html": html,
        "toc": toc,
        "words": words,
        "reading_time": max(1, round(words / WORDS_PER_MIN)) if words else 0,
        "title": title,
        "doctype": "markdown",
    }


# ── source / code rendering ────────────────────────────────────────────────
# Files by extension go through pygments; T24 jBC (no extension, or .b) uses the
# lightweight highlighter below — pygments has no jBASE/jBC lexer.
CODE_EXT = {
    ".py": "python", ".sh": "bash", ".bash": "bash", ".json": "json",
    ".xml": "xml", ".yaml": "yaml", ".yml": "yaml", ".java": "java",
    ".js": "javascript", ".mjs": "javascript", ".sql": "sql", ".ini": "ini",
    ".cmd": "batch", ".bat": "batch", ".ps1": "powershell", ".csv": "text",
    ".txt": "text", ".log": "text",
}
_FORMATTER = HtmlFormatter(cssclass="hl", nowrap=False)

_JBC_KW = (
    "IF THEN ELSE END BEGIN CASE GOSUB RETURN CALL FOR NEXT LOOP REPEAT WHILE UNTIL DO "
    "READ READU READV READVU WRITE WRITEU WRITEV MATREAD MATWRITE MATPARSE OPEN OPENSEQ "
    "CLOSE CLOSESEQ DELETE LOCATE FIND CONVERT CHANGE CRT PRINT PRINTER INPUT EQ NE LT GT "
    "LE GE AND OR NOT MATCHES TO FROM ON IN OUT SETTING BY THEN CONTINUE BREAK STOP ABORT "
    "NULL SUBROUTINE PROGRAM FUNCTION EQUATE COMMON DIM DIMENSION EXECUTE PERFORM CAPTURING "
    "RETURNING REMOVE INS DEL ENTER CHAIN GET SEND RQM LOCK UNLOCK CLEAR ASSIGN INCLUDE INSERT"
).split()
_JBC_TOK = re.compile(
    r"(?P<s>'[^'\n]*'|\"[^\"\n]*\")"
    r"|(?P<nt>\$[A-Za-z.]+)"
    r"|(?P<mi>\b\d+(?:\.\d+)?\b)"
    r"|(?P<k>\b(?:" + "|".join(_JBC_KW) + r")\b)"
)


def _esc(s: str) -> str:
    return _html.escape(s, quote=False)


def _jbc_line(line: str) -> str:
    body = line.lstrip()
    indent = line[: len(line) - len(body)]
    if body.startswith("*"):                                   # full-line comment
        return _esc(indent) + f'<span class="c">{_esc(body)}</span>'
    code, comment = line, ""
    m = re.search(r";\s*\*", line)                             # inline ;* comment
    if m:
        code, comment = line[: m.start()], line[m.start():]
    out, i = [], 0
    for t in _JBC_TOK.finditer(code):
        out.append(_esc(code[i:t.start()]))
        out.append(f'<span class="{t.lastgroup}">{_esc(t.group())}</span>')
        i = t.end()
    out.append(_esc(code[i:]))
    if comment:
        out.append(f'<span class="c">{_esc(comment)}</span>')
    return "".join(out)


def _jbc(text: str) -> str:
    return '<pre class="hl"><code>' + "\n".join(_jbc_line(l) for l in text.split("\n")) + "</code></pre>"


def render_source(name: str, text: str) -> dict:
    ext = os.path.splitext(name)[1].lower()
    base = {
        "toc": [], "words": len(text.split()), "lines": text.count("\n") + 1,
        "reading_time": 0, "title": name.rsplit("/", 1)[-1], "doctype": "code",
    }
    if ext in CODE_EXT:
        try:
            html = highlight(text, get_lexer_by_name(CODE_EXT[ext]), _FORMATTER)
            return {**base, "html": html}
        except ClassNotFound:
            pass
    return {**base, "html": _jbc(text)}
