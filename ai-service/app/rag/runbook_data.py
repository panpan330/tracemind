"""Runbook 解析与分块:frontmatter 元数据 + 按节切分 + 稳定 Point ID(uuid5)。"""
import hashlib
import uuid
from pathlib import Path

RUNBOOKS_DIR = Path(__file__).resolve().parents[3] / "knowledge" / "runbooks"
NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # DNS namespace,固定


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    meta = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, text[end + 5:]


def parse_runbook(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(raw)
    sections = []
    current_title, current_lines = "", []
    for line in body.splitlines():
        if line.startswith("## "):
            if current_title:
                sections.append({"section": current_title,
                                 "text": "\n".join(current_lines).strip()})
            current_title, current_lines = line[3:].strip(), []
        else:
            current_lines.append(line)
    if current_title:
        sections.append({"section": current_title, "text": "\n".join(current_lines).strip()})
    return {
        "doc_id": meta.get("doc_id", path.stem),
        "title": meta.get("title", path.stem),
        "fault_category": meta.get("doc_fault_category", ""),
        "service": meta.get("doc_service", ""),
        "scenario_id": meta.get("doc_scenario_id", ""),
        "version": meta.get("doc_version", ""),
        "sections": [s for s in sections if s["text"]],
    }


def chunk_text(text: str, max_chars: int = 400) -> list[str]:
    chunks: list[str] = []
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars:
            chunks.append(para)
            continue
        buf = ""
        for sent in para.replace("\n", "").split("。"):
            piece = (sent + "。") if sent.strip() else ""
            if len(buf) + len(piece) > max_chars and buf:
                chunks.append(buf)
                buf = piece
            else:
                buf += piece
        if buf:
            chunks.append(buf)
    return chunks


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def point_id(doc_id: str, section: str, idx: int) -> int:
    """稳定 Point ID:相同输入幂等(uuid5 取 int)。"""
    return uuid.uuid5(NAMESPACE, f"{doc_id}|{section}|{idx}").int


def load_all_runbooks(directory: Path = RUNBOOKS_DIR) -> list[dict]:
    return [parse_runbook(p) for p in sorted(directory.glob("*.md"))]
