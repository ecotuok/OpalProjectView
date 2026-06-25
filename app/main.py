"""Opal — FastAPI server for the T24 project reader.

Serves the static reader UI plus a small JSON API:
  /api/library              every project + workspace doc, with manifest summary
  /api/manifest?project=    one project's structured manifest (env/artifacts/deps)
  /api/doc?project=&path=   a markdown file rendered to HTML (+ TOC, reading time)
  /api/asset?project=&path= a relative asset (image, pdf) referenced by a doc
"""

from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import render, scan, win

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="Opal", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

BUILD = "2026-06-24-v2"


@app.middleware("http")
async def no_cache(request: Request, call_next):
    """Never let the --app window serve a stale build after an update."""
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/version")
def version():
    return {"build": BUILD, "app": "Opal"}


@app.post("/api/shutdown")
def shutdown():
    """Let a fresh launch retire a stale instance holding the port."""
    import os
    import threading
    threading.Timer(0.3, lambda: os._exit(0)).start()
    return {"ok": True}


@app.post("/api/open")
def open_window(request: Request, project: str = Query(""), doc: str = Query("")):
    """Open another standalone window, optionally deep-linked to a project/doc."""
    from urllib.parse import urlencode
    base = str(request.base_url).rstrip("/")
    q = {k: v for k, v in (("project", project), ("doc", doc)) if v}
    url = base + "/" + (("?" + urlencode(q)) if q else "")
    ok = win.open_app_window(url)
    return {"ok": ok, "url": url}


@app.get("/api/config")
def get_config():
    return scan.config_info()


@app.post("/api/config")
def set_config(root: str = Body(..., embed=True)):
    try:
        p = scan.set_root(root)
    except NotADirectoryError:
        raise HTTPException(400, "not a directory")
    return {"ok": True, "root": str(p)}


@app.post("/api/env")
def set_env(project: str = Body(...), ip: str = Body(...),
            label: str = Body(""), bnk_run: str = Body(""), note: str = Body("")):
    """Declare/override a project's environment from the UI. Persists to the project's
    _ctx/project.yml (env_override), refreshes the auto block + INDEX.md — fully in context."""
    root = scan.get_root()
    if root is None:
        raise HTTPException(400, "no projects root configured")
    if scan.CTX is None:
        raise HTTPException(500, "ctx_sync not available")
    yml = root / project / "_ctx" / "project.yml"
    if not yml.is_file():
        raise HTTPException(400, f"{project} has no _ctx/project.yml — add a context layer first")
    try:
        scan.CTX.set_env_override(str(yml), label or ip, ip, bnk_run or None, note or None)
        scan.CTX.sync_project(str(root), project)   # refresh the CTX_AUTO block
        scan.CTX.build_index(str(root))             # refresh INDEX.md
    except SystemExit as e:
        raise HTTPException(500, f"set env failed: {e}")
    except Exception as e:
        raise HTTPException(500, f"set env failed: {e}")
    return scan.manifest(project)


@app.get("/api/library")
def library():
    try:
        return scan.library()
    except Exception as e:
        raise HTTPException(500, f"scan failed: {e}")


@app.get("/api/manifest")
def manifest(project: str = Query(...)):
    try:
        return scan.manifest(project)
    except Exception as e:
        raise HTTPException(404, f"no manifest for {project}: {e}")


@app.get("/api/doc")
def doc(project: str = Query(...), path: str = Query(...)):
    try:
        fp = scan.safe_doc_path(project, path)
    except FileNotFoundError:
        raise HTTPException(404, f"no such doc: {path}")
    except PermissionError:
        raise HTTPException(403, "forbidden")
    text = fp.read_text(encoding="utf-8", errors="replace")
    if fp.suffix.lower() == ".md":
        out = render.render(project, path, text)
    else:
        out = render.render_source(path, text)
    out["project"] = project
    out["path"] = path
    out["mtime"] = fp.stat().st_mtime
    return JSONResponse(out)


@app.get("/api/mtime")
def mtime(project: str = Query(...), path: str = Query(...)):
    """Cheap freshness check so the open doc can live-reload when it changes."""
    try:
        fp = scan.safe_doc_path(project, path)
    except (FileNotFoundError, PermissionError):
        raise HTTPException(404, "no such doc")
    return {"mtime": fp.stat().st_mtime}


@app.get("/api/asset")
def asset(project: str = Query(...), path: str = Query(...)):
    try:
        fp = scan.safe_doc_path(project, path)
    except FileNotFoundError:
        raise HTTPException(404, "no such asset")
    except PermissionError:
        raise HTTPException(403, "forbidden")
    return FileResponse(fp)
