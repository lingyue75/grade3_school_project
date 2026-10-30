# -*- coding: utf-8 -*-
"""
检索索引管理（★核心复用：对应 src/services/knowledge_service.py + src/infra/vectorstore/）

本模块【直接复用阶段1 的四个核心模块】，一行切片/向量/BM25 代码都不重写：
    from phase1_naive_rag import MarkdownSplitter, EmbeddingClient,
                                FaissVectorStore, NumpyVectorStore, SimpleBM25, hybrid_search

关键设计（对应接口设计 §0 坑 1）：
    FAISS 不支持真正删除 → 采用"软删 + 检索后过滤 + 后台重建索引"策略。
    文档与分片的权威状态在 SQLite，向量库只是【可重建的缓存】，随时可全量重灌。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

# 把项目根目录与 scripts/ 加入路径，以便复用阶段1 脚本中的核心模块
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_PROJECT_ROOT), str(_PROJECT_ROOT / "scripts"), str(_PROJECT_ROOT / "sample")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from phase1_naive_rag import (  # noqa: E402  ★阶段1 复用
    EmbeddingClient,
    FaissVectorStore,
    MarkdownSplitter,
    NumpyVectorStore,
    SimpleBM25,
    _HAS_FAISS,
    hybrid_search,
)

from . import db  # noqa: E402
from .security import now_str  # noqa: E402
from .settings import settings  # noqa: E402


class RAGIndex:
    """
    单例索引：持有向量库、BM25、行号↔数据库 ID 的映射。

    约定：
      · self.chunk_ids[i] = 第 i 行向量对应的 policy_chunks.id
      · self.texts[i]     = 第 i 行的原文（BM25 与向量共用同一顺序）
      · self.deleted      = 已软删的 chunk id 集合，检索后过滤掉
    """

    def __init__(self):
        self.embedder = EmbeddingClient(settings)
        self.store = None
        self.bm25: SimpleBM25 | None = None
        self.chunk_ids: list[int] = []
        self.texts: list[str] = []
        self.deleted: set[int] = set()
        self.ready = False
        self.meta_path = Path(settings.index_dir) / "meta.json"

    # ---------------------------------------------------------------- 生命周期
    def load_or_build(self) -> None:
        """启动时调用：能复用磁盘索引就复用（省 embedding 费用），否则全量重建。"""
        Path(settings.index_dir).mkdir(parents=True, exist_ok=True)
        chunks = db.query_all(
            "SELECT id, text FROM policy_chunks WHERE status='active' ORDER BY id"
        )
        self._reset_state(chunks)
        if not chunks:
            self.ready = False
            print("[RAG] 知识库为空，等待管理员上传制度文档")
            return

        fingerprint = self._fingerprint()
        cached_meta = self._read_meta()
        if cached_meta and cached_meta.get("fingerprint") == fingerprint and self._load_store():
            # 磁盘索引与当前分片集合一致 → 只重建 BM25（无需重新 embedding）
            self.bm25 = SimpleBM25(self.texts)
            self.ready = True
            print(f"[RAG] 复用磁盘索引：{len(self.texts)} 个分片")
            return
        self.rebuild()

    def rebuild(self) -> None:
        """全量重建：重新向量化所有 active 分片，并清掉已软删的残留（对应后台"重建索引"按钮）。"""
        chunks = db.query_all(
            "SELECT id, text FROM policy_chunks WHERE status='active' ORDER BY id"
        )
        self._reset_state(chunks)
        if not chunks:
            self.store = None
            self.bm25 = None
            self.ready = False
            self._write_meta({"fingerprint": "", "chunk_ids": [], "deleted": []})
            return

        vectors = self.embedder.embed(self.texts)
        self.store = self._new_store(vectors.shape[1])
        self.store.add(vectors)
        self._save_store()
        self.bm25 = SimpleBM25(self.texts)
        self.ready = True
        self._write_meta(self._meta())
        print(f"[RAG] 已重建索引：{len(self.texts)} 个分片，dim={vectors.shape[1]}")

    # ---------------------------------------------------------------- 写入
    def add_chunks(self, chunks: list[dict]) -> int:
        """
        把一个文档的分片写入 SQLite 与向量库。
        chunks: [{chunk_index, clause_no, heading_path, text, char_start, char_end, document_id}]
        返回新增分片数。
        """
        if not chunks:
            return 0
        # ① 先写数据库（权威状态）
        new_ids: list[int] = []
        for c in chunks:
            cur = db.execute(
                "INSERT INTO policy_chunks(document_id,chunk_index,clause_no,heading_path,text,"
                "char_start,char_end,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (c["document_id"], c["chunk_index"], c.get("clause_no", ""), c.get("heading_path", ""),
                 c["text"], c.get("char_start", 0), c.get("char_end", 0), "active", now_str()),
            )
            new_ids.append(int(cur.lastrowid))

        # ② 再写向量库（缓存）
        texts = [c["text"] for c in chunks]
        vectors = self.embedder.embed(texts)
        if self.store is None or not self.ready:
            self.rebuild()                       # 首次：全量重建，保证行号与文本严格对齐
            return len(chunks)

        self.store.add(vectors)
        self.chunk_ids.extend(new_ids)
        self.texts.extend(texts)
        self.bm25 = SimpleBM25(self.texts)       # BM25 无增量接口，全量重建（千级分片毫秒级）
        self._write_meta(self._meta())
        return len(chunks)

    def soft_delete_document(self, document_id: str) -> int:
        """软删：数据库标 deleted + 内存标记过滤；向量库留待重建时清理。"""
        rows = db.query_all(
            "SELECT id FROM policy_chunks WHERE document_id=? AND status='active'", (document_id,)
        )
        ids = [int(r["id"]) for r in rows]
        if not ids:
            return 0
        db.execute(
            f"UPDATE policy_chunks SET status='deleted' WHERE id IN ({','.join('?' * len(ids))})", ids
        )
        self.deleted.update(ids)
        self._write_meta(self._meta())
        return len(ids)

    # ---------------------------------------------------------------- 检索
    def search(self, query: str, top_k: int | None = None) -> list[dict]:
        """
        混合检索，返回 [{chunk_id, text, file, clause_no, heading_path, score, low_confidence}]。
        已软删分片在此处被过滤（FAISS 无法真正删除，故检索后过滤）。
        """
        top_k = top_k or settings.top_k
        if not self.ready or not self.texts:
            return []

        # 候选池放大 3 倍：过滤掉软删分片后仍有足够候选
        pool = max(settings.candidate_pool, top_k * 3)
        raw = hybrid_search(
            query, self.embedder, self.store, self.bm25,
            top_k=pool, pool=pool, fusion=settings.fusion,
        )

        results: list[dict] = []
        for row, score, _detail in raw:
            if row < 0 or row >= len(self.chunk_ids):
                continue
            cid = self.chunk_ids[row]
            if cid in self.deleted:
                continue
            row_data = db.query_one(
                "SELECT c.id, c.text, c.clause_no, c.heading_path, d.title AS file "
                "FROM policy_chunks c LEFT JOIN knowledge_documents d ON d.id=c.document_id "
                "WHERE c.id=?", (cid,)
            )
            if not row_data:
                continue
            results.append({
                "chunk_id": cid,
                "text": row_data["text"],
                "file": row_data["file"] or "未知文件",
                "clause_no": row_data["clause_no"] or "",
                "heading_path": row_data["heading_path"] or "",
                "score": round(float(score), 4),
                # 低置信度：融合分过低时提示前端"依据较弱"
                "low_confidence": float(score) < settings.low_score_threshold,
            })
            if len(results) >= top_k:
                break
        return results

    # ---------------------------------------------------------------- 内部工具
    def _reset_state(self, chunks) -> None:
        self.chunk_ids = [int(c["id"]) for c in chunks]
        self.texts = [c["text"] for c in chunks]
        cached = self._read_meta()
        self.deleted = set(cached.get("deleted", [])) if cached else set()

    def _new_store(self, dim: int):
        """工厂方法：优先 FAISS，缺失时降级 NumPy（上层只依赖统一接口）。"""
        return FaissVectorStore(dim) if _HAS_FAISS else NumpyVectorStore(dim)

    def _load_store(self) -> bool:
        """从磁盘加载向量库；失败返回 False（调用方会触发重建）。"""
        dim = settings.embed_dim
        try:
            if _HAS_FAISS:
                f = Path(settings.index_dir) / "index.faiss"
                if not f.exists():
                    return False
                import faiss
                import numpy as _np
                # ★ 用 Python 读字节再反序列化：faiss.write_index 的 C++ 文件 IO
                #   在【中文路径】下会失败（本项目路径含"作业"二字），必须绕过
                self.store = FaissVectorStore(dim)
                self.store.index = faiss.deserialize_index(
                    _np.frombuffer(f.read_bytes(), dtype=_np.uint8))
                return True
            self.store = NumpyVectorStore(dim)
            return self.store.load(Path(settings.index_dir))
        except Exception as exc:
            print(f"[RAG] 索引加载失败，将重建：{exc}")
            return False

    def _save_store(self) -> None:
        """保存向量库到磁盘（同样绕过 faiss 的 C++ 文件 IO，兼容中文路径）。"""
        p = Path(settings.index_dir)
        p.mkdir(parents=True, exist_ok=True)
        if _HAS_FAISS:
            import faiss
            (p / "index.faiss").write_bytes(
                faiss.serialize_index(self.store.index).tobytes())
        else:
            self.store.save(p)

    def _fingerprint(self) -> str:
        """分片集合指纹：用于判断磁盘索引是否仍然有效（避免无谓的重新 embedding）。"""
        raw = "|".join(str(i) for i in self.chunk_ids) + f"|{settings.embed_backend}|{settings.embed_model}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _meta(self) -> dict:
        return {
            "fingerprint": self._fingerprint(),
            "chunk_ids": self.chunk_ids,
            "deleted": sorted(self.deleted),
        }

    def _read_meta(self) -> dict | None:
        if not self.meta_path.exists():
            return None
        try:
            return json.loads(self.meta_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _write_meta(self, meta: dict) -> None:
        self.meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


# ============================================================================
# 单例
# ============================================================================
_index: RAGIndex | None = None


def get_index() -> RAGIndex:
    global _index
    if _index is None:
        _index = RAGIndex()
        _index.load_or_build()
    return _index


# ============================================================================
# 文档摄取：Markdown / PDF → 分片
# ============================================================================
def parse_file(path: Path, source_type: str) -> str:
    """
    读取文件内容为纯文本。
      · md  ：直接读取
      · pdf ：用 pypdf 提取文本层；扫描件（无文本层）明确报错，不支持 OCR
    """
    if source_type == "md":
        return path.read_text(encoding="utf-8", errors="ignore")
    if source_type == "pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("未安装 pypdf，无法解析 PDF") from exc
        reader = PdfReader(str(path))
        if getattr(reader, "is_encrypted", False):
            raise RuntimeError("PDF 已加密，无法提取文本")
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        if len(text.strip()) < 20:
            raise RuntimeError("PDF 无可提取文本层（可能为扫描件），请上传可复制文本的版本")
        return text
    raise RuntimeError(f"不支持的文件类型：{source_type}")


def split_text(text: str, source: str, source_type: str) -> list[dict]:
    """
    统一切片入口。
      · md  ：复用阶段1 的 MarkdownSplitter（按 # 标题分块 + 500 字滑窗 + 重叠）
      · pdf ：无 Markdown 标题，先按空行分段，再走同一套 500 字滑窗
      · 两种格式共用【行首编号识别】（3.2.1 / 第 5 条），修复阶段1 发现的
        "条款号写正文里导致多个标准被并进同一片"的问题。
    """
    splitter = MarkdownSplitter(settings.chunk_size, settings.chunk_overlap)
    if source_type == "md":
        chunks = splitter.split(text, source)
    else:
        # PDF：把连续空行视作标题边界，构造成伪 Markdown 再交给同一套切片器
        pseudo = _pdf_to_pseudo_markdown(text)
        chunks = splitter.split(pseudo, source)

    return [{
        "chunk_index": i,
        "clause_no": c.clause_no,
        "heading_path": c.heading_path,
        "text": c.text,
        "char_start": c.char_start,
        "char_end": c.char_end,
    } for i, c in enumerate(chunks)]


_CLAUSE_LINE = __import__("re").compile(r"^\s*(?:第\s*)?[0-9]{1,3}(?:[.．][0-9]{1,3}){0,3}\s*[、.．]?\s+\S")


def _pdf_to_pseudo_markdown(text: str) -> str:
    """
    PDF 文本 → 伪 Markdown：
      · 行首带编号的行（如 "3.2.1 住宿标准"）提升为三级标题，保证按条款切分
      · 其余内容按连续空行分段
    """
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            out.append("")
        elif _CLAUSE_LINE.match(s):
            out.append(f"### {s}")
        else:
            out.append(s)
    return "\n".join(out)
