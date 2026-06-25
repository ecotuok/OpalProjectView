"""Scan the T24 projects workspace into the data Opal reads.

Reuses t24-tools/ctx_sync.py for the *live* env/artifacts/last-deploy derivation
(so Opal and INDEX.md never disagree) and PyYAML for the human manifest fields.
Falls back gracefully if ctx_sync isn't importable.
"""

import json
import os
import sys
from pathlib import Path

import yaml


# ── reuse ctx_sync for the env/artifact derivation ────────────────────────
def _load_ctx_sync():
    devtools = Path(__file__).resolve().parents[2]      # opal/app -> opal -> DevTools
    t24 = devtools / "t24-tools"
    if t24.is_dir() and str(t24) not in sys.path:
        sys.path.insert(0, str(t24))
    try:
        import ctx_sync
        return ctx_sync
    except Exception:
        return None


CTX = _load_ctx_sync()


CONFIG_FILE = Path(__file__).resolve().parents[1] / ".opal.json"   # opal/.opal.json
WORDS_PER_MIN = 210


def _config() -> dict:
    try:
        import json
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_config(d: dict):
    import json
    CONFIG_FILE.write_text(json.dumps(d, indent=2), encoding="utf-8")


def detect_root() -> Path | None:
    """Auto-detect the projects root — env var, then ctx_sync, then known locations."""
    env = os.environ.get("OPAL_PROJECTS_ROOT")
    if env and Path(env).is_dir():
        return Path(env)
    if CTX:
        try:
            return Path(CTX.find_projects_root())
        except SystemExit:
            pass
    import glob
    desktop = Path(__file__).resolve().parents[3]        # DevTools -> Desktop
    home = Path.home()
    # search for a Codittle <bank>/<stream>/projects dir (no hardcoded names)
    patterns = [str(desktop / "Codittle" / "*" / "*" / "projects"),
                str(home / "*" / "Desktop" / "Codittle" / "*" / "*" / "projects"),
                str(home / "Desktop" / "Codittle" / "*" / "*" / "projects")]
    for pat in patterns:
        hits = sorted(p for p in glob.glob(pat) if Path(p).is_dir())
        if hits:
            return Path(hits[0])
    return None


def get_root() -> Path | None:
    """A user-set root (from .opal.json) wins; otherwise auto-detect."""
    saved = _config().get("root")
    if saved and Path(saved).is_dir():
        return Path(saved)
    return detect_root()


def set_root(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_dir():
        raise NotADirectoryError(path)
    cfg = _config()
    cfg["root"] = str(p)
    _save_config(cfg)
    return p


def config_info() -> dict:
    saved = _config().get("root")
    cur = get_root()
    return {
        "root": str(cur) if cur else None,
        "saved": saved,
        "detected": str(detect_root()) if detect_root() else None,
        "source": "saved" if (saved and cur and str(cur) == saved) else ("detected" if cur else "none"),
    }


# ── helpers ───────────────────────────────────────────────────────────────
def _words(text: str) -> int:
    return len(text.split())


def _reading_time(words: int) -> int:
    return max(1, round(words / WORDS_PER_MIN))


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _doc_entry(proj_dir: Path, rel: str, label: str, group: str, kind: str = "md") -> dict:
    p = proj_dir / rel
    text = _read(p) if kind == "md" else ""
    w = _words(text)
    return {
        "id": rel.replace("\\", "/"),
        "label": label,
        "group": group,
        "kind": kind,
        "words": w,
        "reading_time": _reading_time(w) if w else 0,
        "mtime": p.stat().st_mtime if p.exists() else 0,
    }


def _pretty(stem: str) -> str:
    return stem.replace("-", " ").replace("_", " ").replace(".", " ").strip()


def _collect_docs(proj_dir: Path) -> list:
    docs = []
    if (proj_dir / "CLAUDE.md").is_file():
        docs.append(_doc_entry(proj_dir, "CLAUDE.md", "Brief", "brief"))
    if (proj_dir / "_ctx" / "project.yml").is_file():
        docs.append(_doc_entry(proj_dir, "_ctx/project.yml", "Manifest", "manifest", kind="manifest"))
    if (proj_dir / "_ctx" / "worklog.md").is_file():
        docs.append(_doc_entry(proj_dir, "_ctx/worklog.md", "Worklog", "log"))
    if (proj_dir / "_ctx" / "runbook.md").is_file():
        docs.append(_doc_entry(proj_dir, "_ctx/runbook.md", "Runbook", "runbook"))
    docs_dir = proj_dir / "docs"
    if docs_dir.is_dir():
        for p in sorted(docs_dir.glob("*.md")):
            label = "README" if p.name.lower() == "readme.md" else _pretty(p.stem)
            docs.append(_doc_entry(proj_dir, f"docs/{p.name}", label, "docs"))
    # top-level markdown that isn't the brief (e.g. a stray README)
    for p in sorted(proj_dir.glob("*.md")):
        if p.name == "CLAUDE.md":
            continue
        docs.append(_doc_entry(proj_dir, p.name, _pretty(p.stem), "other"))
    return docs


def _manifest_human(proj_dir: Path) -> dict:
    yml = proj_dir / "_ctx" / "project.yml"
    if not yml.is_file():
        return {}
    try:
        data = yaml.safe_load(_read(yml)) or {}
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError:
        return {}


def _env_and_artifacts(proj_dir: Path, human: dict) -> tuple:
    """Live env/artifacts/last_deploy via ctx_sync; fall back to the yaml snapshot."""
    if CTX:
        try:
            facts = CTX.derive(str(proj_dir))
            return facts["env"], facts["artifacts"], facts["last_deploy"]
        except Exception:
            pass
    env = human.get("env_override") or human.get("env") or {}
    if "source" not in env:
        env = {**env, "source": "declared" if human.get("env_override") else "codittle"}
    return env, human.get("artifacts", {}), human.get("last_deploy")


def _tracked_keys(proj_dir: Path) -> set:
    try:
        d = json.loads((proj_dir / ".codittle" / "versions.json").read_text(encoding="utf-8"))
        return set((d.get("files") or {}).keys())
    except (OSError, ValueError):
        return set()


def _classify(name: str, allnames: set) -> str:
    low = name.lower()
    if low.endswith((".jar", ".java")):
        return "java"
    if "," in name:
        return "versions"
    if name.startswith("I_"):
        return "includes"
    if name.endswith(".PARAM") or name.endswith(".PARAM.FIELDS"):
        return "params"
    if name.endswith(".FIELDS"):
        return "files"
    if (name + ".FIELDS") in allnames:
        return "files"
    if low.endswith(".md"):
        return "docs"
    return "routines"


def _sources(proj_dir: Path) -> list:
    """Every viewable source file under the project's *.BP folders, classified."""
    bps = [d for d in os.listdir(proj_dir)
           if d.endswith(".BP") and (proj_dir / d).is_dir()]
    collected = []
    allnames = set()
    for bp in sorted(bps):
        for root, _dirs, fnames in os.walk(proj_dir / bp):
            for fn in fnames:
                if fn.endswith((".o", ".obj")):
                    continue
                rel = os.path.relpath(os.path.join(root, fn), proj_dir).replace("\\", "/")
                collected.append((rel, fn))
                allnames.add(fn)
    tracked = _tracked_keys(proj_dir)
    out = [{"path": rel, "name": fn, "group": _classify(fn, allnames),
            "tracked": rel in tracked} for rel, fn in collected]
    out.sort(key=lambda x: (x["group"], x["name"].lower()))
    return out


def _project(proj_dir: Path) -> dict:
    human = _manifest_human(proj_dir)
    env, artifacts, last_deploy = _env_and_artifacts(proj_dir, human)
    docs = _collect_docs(proj_dir)
    sources = _sources(proj_dir)
    has_ctx = (proj_dir / "_ctx" / "project.yml").is_file()
    slug = proj_dir.name
    return {
        "id": slug,
        "title": str(human.get("title") or _pretty(slug)),
        "kind": str(human.get("kind") or ("unknown" if not has_ctx else "unknown")),
        "status": str(human.get("status") or "—"),
        "ticket": str(human.get("ticket") or "").strip(),
        "summary": str(human.get("summary") or "").strip(),
        "owners": human.get("owners") or [],
        "env": env,
        "artifacts": artifacts or {},
        "dependencies": human.get("dependencies") or {},
        "last_deploy": last_deploy,
        "has_ctx": has_ctx,
        "docs": docs,
        "sources": sources,
        "updated": max((d["mtime"] for d in docs), default=0),
        "doc_count": len([d for d in docs if d["kind"] == "md"]),
    }


# ── public API ────────────────────────────────────────────────────────────
def library() -> dict:
    root = get_root()
    base = {"kinds": ["customization", "product", "investigation", "unknown"],
            **config_info()}
    if root is None:
        return {**base, "projects": [], "workspace": [], "error": "no_root"}

    projects = []
    for entry in sorted(os.listdir(root)):
        p = root / entry
        if not p.is_dir() or entry.startswith(".") or entry.startswith("_"):
            continue
        projects.append(_project(p))

    workspace = []
    for name, label in (("PLAYBOOK.md", "Playbook"), ("INDEX.md", "Index")):
        if (root / name).is_file():
            workspace.append({"id": name, "label": label,
                              "mtime": (root / name).stat().st_mtime})

    return {**base, "projects": projects, "workspace": workspace}


def safe_doc_path(project: str, rel: str) -> Path:
    """Resolve <project>/<rel> and refuse anything escaping the workspace."""
    root = get_root()
    if root is None:
        raise FileNotFoundError("no projects root configured")
    base = root if project in ("", "_workspace") else (root / project)
    target = (base / rel).resolve()
    root_resolved = root.resolve()
    if root_resolved not in target.parents and target != root_resolved:
        raise PermissionError("path escapes workspace")
    if not target.is_file():
        raise FileNotFoundError(rel)
    return target


def manifest(project: str) -> dict:
    root = get_root()
    if root is None:
        raise FileNotFoundError("no projects root configured")
    return _project(root / project)
