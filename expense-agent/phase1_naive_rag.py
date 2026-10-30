#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
================================================================================
 报销助手智能体系统 · 阶段1「裸问答」最小原型（单文件，无 Web）
================================================================================
目标：用最少的代码把「制度文档 → 切片 → 向量化 → 混合检索 → LLM 回答」这条主链路跑通，
      用来在第一周证伪 RAG 准确率，而不是做一个能上线的系统。

流程：
    本地 Markdown 制度文件
        → ① 切片（默认 500 字，带标题结构与条款号）
        → ② 向量化（可配置供应商；无网络时用 hash 降级做冒烟）
        → ③ 存入 FAISS（IndexFlatIP + 余弦）
        → ④ 提问：BM25 + 向量 混合检索 top-k
        → ⑤ 拼提示词 → ⑥ 调用 LLM → 输出带条款引用的答案

--------------------------------------------------------------------------------
 ★ 本文件中「后续阶段会直接复用」的核心模块（换实现不换调用方）
--------------------------------------------------------------------------------
 序号 模块                     位置                     在后续架构中的落点
 [1] Settings                 第 2 节                  src/core/config.py
 [2] MarkdownSplitter         第 3 节                  src/services/knowledge_service.py
     └ 切片策略是 RAG 成败的关键，阶段2 只换成「按条款切 + 结构化元数据」，接口不变
 [3] EmbeddingClient          第 4 节                  src/infra/embedding.py
     └ 面向接口设计，阶段2 换模型只改这一层
 [4] VectorStore（抽象）      第 5 节                  src/infra/vectorstore/base.py
     ├ FaissVectorStore       第 5 节                  src/infra/vectorstore/faiss_store.py
     └ NumpyVectorStore       第 5 节                  仅本机冒烟用的兜底实现
     阶段2 换成 PgVectorStore（pgvector），只要实现 add/search/save/load，上层零改动
 [5] SimpleBM25               第 6 节                  src/services/retrieval_service.py
 [6] hybrid_search            第 7 节                  src/services/retrieval_service.py
 [7] build_prompt             第 8 节                  src/agent/prompts/v1/qa.md（内容外置）
 [8] call_llm                 第 9 节                  src/agent/llm/base.py + provider_openai.py
     └ 已经是「供应商可配置」的形态，阶段2 加重试/降级/留痕即可

 非核心（阶段2 会被丢弃）：main() 的命令行交互、NumpyVectorStore、hash 向量。

--------------------------------------------------------------------------------
 安装与运行
--------------------------------------------------------------------------------
 pip install faiss-cpu numpy jieba openai        # faiss/jieba/openai 缺失时脚本会自动降级，不阻塞冒烟

 # 方式 A：离线冒烟（不需要任何 API Key，只看检索链路对不对）
 python phase1_naive_rag.py --embed-backend hash --dry-run

 # 方式 B：真实跑通（以 DeepSeek 为例，OpenAI 兼容协议）
 set LLM_API_KEY=sk-xxxx
 set LLM_BASE_URL=https://api.deepseek.com/v1
 set LLM_MODEL=deepseek-chat
 set EMBED_BASE_URL=https://api.deepseek.com/v1        # 不支持 embeddings 的供应商见下方说明
 python phase1_naive_rag.py

 # 常用参数
 --file        指定制度 Markdown 文件（默认 data/差旅报销制度.md）
 --rebuild     强制重建索引
 --topk 3     召回条数
 --fusion rrf 融合策略（rrf=倒数排名融合，推荐；weighted=加权归一化）
 --dry-run    只打印提示词，不调用 LLM（省钱、便于评审提示词）
 --question   单条提问后退出；不传则进入交互式问答

 注意：DeepSeek 等部分供应商不提供 /embeddings 接口。此时请换成提供 embedding 的供应商
 （如通义 text-embedding-v3、OpenAI text-embedding-3-small），或临时用 --embed-backend hash 跑通链路。
================================================================================
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

# ----------------------------------------------------------------------------
# 可选依赖：缺失时降级，保证脚本在本机一定能跑起来（阶段2 会改为强制依赖）
# ----------------------------------------------------------------------------
try:
    import faiss  # type: ignore
    _HAS_FAISS = True
except Exception:
    _HAS_FAISS = False

try:
    import jieba  # type: ignore  # 中文分词，BM25 效果好很多
    _HAS_JIEBA = True
except Exception:
    _HAS_JIEBA = False


# ============================================================================
# 2. 配置（★核心复用 [1]）—— 对应 src/core/config.py
#     约定：所有外部依赖（模型地址、密钥、维度）只在这里出现，业务代码不读环境变量
# ============================================================================
@dataclass
class Settings:
    """全局配置。阶段2 换成 pydantic-settings，字段基本不变。"""

    # ---- LLM 生成 ----
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"))
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "deepseek-chat"))

    # ---- Embedding ----
    # 默认跟随 LLM 的 base_url；若供应商不提供 embedding，可单独指向别家
    embed_base_url: str = field(default_factory=lambda: os.getenv("EMBED_BASE_URL", "") or os.getenv("LLM_BASE_URL", ""))
    embed_api_key: str = field(default_factory=lambda: os.getenv("EMBED_API_KEY", "") or os.getenv("LLM_API_KEY", ""))
    embed_model: str = field(default_factory=lambda: os.getenv("EMBED_MODEL", "text-embedding-v3"))
    embed_dim: int = field(default_factory=lambda: int(os.getenv("EMBED_DIM", "1024")))
    # api = 调真实 embedding 接口；hash = 本地哈希伪向量（仅离线冒烟，检索质量不可用）
    embed_backend: str = field(default_factory=lambda: os.getenv("EMBED_BACKEND", "api"))

    # ---- 检索 ----
    chunk_size: int = 500          # 切片字数上限（需求约定 500 字）
    chunk_overlap: int = 80        # 滑窗重叠，避免一句话被切断
    top_k: int = 3                 # 最终送入提示词的条数
    candidate_pool: int = 20       # 两路召回的候选池大小（先宽召回，再融合截断）
    fusion: str = "rrf"            # rrf | weighted

    # ---- 生成参数 ----
    temperature: float = 0.2       # 制度问答要稳，温度必须低
    max_tokens: int = 800
    timeout: int = 60

    # ---- 索引落盘 ----
    index_dir: str = "./.rag_index"


# ============================================================================
# 3. 切片器（★核心复用 [2]）—— 对应 src/services/knowledge_service.py
#     需求是「按 500 字切片」，但纯按字数切会把条款拦腰截断。
#     因此实现为「结构感知的 500 字切片」：
#       先按 Markdown 标题切成「条款块」，块内超过 500 字再滑窗，并保留标题路径与条款号。
#     这一步是 RAG 准确率的第一杠杆，阶段2 会继续强化元数据（适用职级 / 生效区间 / 制度版本）。
# ============================================================================
@dataclass
class Chunk:
    """一个知识分片。字段与阶段2 的 policy_chunks 表一一对应。"""
    chunk_id: int                 # 分片序号，同时作为向量库中的行号（FAISS 用自增 id）
    text: str                     # 分片原文
    source: str                   # 来源文件
    heading_path: str             # 章节路径，如「三、差旅标准 > 3.2 住宿费」
    clause_no: str                # 条款号，如「3.2.1」，用于答案引用溯源
    char_start: int = 0
    char_end: int = 0

    def as_ref(self) -> str:
        """给提示词用的引用标签，让模型能指名道姓地引用。"""
        parts = [self.source]
        if self.clause_no:
            parts.append(f"条款 {self.clause_no}")
        if self.heading_path:
            parts.append(self.heading_path)
        return " | ".join(parts)


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
# 形如「3.2.1 住宿标准」「第 5 条 发票要求」「（二）交通费」开头的编号
_CLAUSE_RE = re.compile(r"^\s*(?:第\s*)?([0-9]{1,3}(?:[.．][0-9]{1,3}){0,3})\s*[、.．]?\s")


def _extract_clause_no(heading: str) -> str:
    """从标题里抽条款号，抽不到就返回空串（不影响检索，只影响引用美观）。"""
    m = _CLAUSE_RE.match(heading)
    return m.group(1).replace("．", ".") if m else ""


def _smart_cut(text: str, limit: int) -> int:
    """
    在 limit 附近找一个「好看」的切点：优先句号/分号/换行，避免把句子拦腰截断。
    返回实际切分位置（字符下标）。
    """
    if len(text) <= limit:
        return len(text)
    window = text[max(0, limit - 60): limit]
    for punct in ("\n\n", "\n", "。", "；", "！", "？", "，", " "):
        pos = window.rfind(punct)
        if pos != -1:
            return max(0, limit - 60) + pos + len(punct)
    return limit


class MarkdownSplitter:
    """Markdown 结构感知切片器：标题分块 → 块内 500 字滑窗。"""

    def __init__(self, chunk_size: int = 500, overlap: int = 80):
        self.chunk_size = chunk_size
        self.overlap = overlap

    def split(self, md_text: str, source: str) -> list[Chunk]:
        blocks = self._split_by_heading(md_text)       # [(heading_path, clause_no, body)]
        chunks: list[Chunk] = []
        cursor = 0

        for heading_path, clause_no, body in blocks:
            body = body.strip()
            if not body:
                continue
            # 块内字数未超限 → 整块作为一个分片（最理想：一个条款一片）
            if len(body) <= self.chunk_size:
                chunks.append(Chunk(
                    chunk_id=len(chunks), text=body, source=source,
                    heading_path=heading_path, clause_no=clause_no,
                    char_start=cursor, char_end=cursor + len(body),
                ))
                cursor += len(body)
                continue

            # 超限 → 滑窗切分，窗口之间保留 overlap 重叠，防止语义断裂
            start = 0
            while start < len(body):
                end = start + _smart_cut(body[start:start + self.chunk_size], self.chunk_size)
                piece = body[start:end].strip()
                if piece:
                    chunks.append(Chunk(
                        chunk_id=len(chunks), text=piece, source=source,
                        heading_path=heading_path, clause_no=clause_no,
                        char_start=cursor + start, char_end=cursor + end,
                    ))
                if end >= len(body):
                    break
                start = max(start + 1, end - self.overlap)   # 回退 overlap 个字符形成重叠
            cursor += len(body)

        return chunks

    @staticmethod
    def _split_by_heading(md_text: str) -> list[tuple[str, str, str]]:
        """
        按 ATX 标题把文档切成块。返回 (标题路径, 条款号, 正文)。
        标题层级用栈维护：遇到 n 级标题时，把栈中 >= n 级的标题弹出。
        """
        stack: list[tuple[int, str]] = []
        blocks: list[tuple[str, str, str]] = []
        buf: list[str] = []

        def flush():
            if buf:
                body = "\n".join(buf).strip()
                if body:
                    path = " > ".join(t for _, t in stack)
                    clause = _extract_clause_no(stack[-1][1]) if stack else ""
                    blocks.append((path, clause, body))
                buf.clear()

        for line in md_text.splitlines():
            m = _HEADING_RE.match(line)
            if m:
                flush()                                  # 遇到新标题，先收束上一段
                level, title = len(m.group(1)), m.group(2).strip()
                stack = [item for item in stack if item[0] < level]
                stack.append((level, title))
            else:
                buf.append(line)
        flush()
        return blocks


# ============================================================================
# 4. 向量化（★核心复用 [3]）—— 对应 src/infra/embedding.py
#     对外只暴露 embed(texts) -> np.ndarray，阶段2 换模型/换供应商只改实现类。
# ============================================================================
class EmbeddingClient:
    """文本向量化客户端。backend=api 走 OpenAI 兼容的 /embeddings；backend=hash 用于离线冒烟。"""

    def __init__(self, settings: Settings):
        self.s = settings
        self._client = None

    # ---------- backend = api ----------
    def _api_embed(self, texts: list[str]) -> np.ndarray:
        """调用 OpenAI 兼容的 embeddings 接口，分批请求，最后做 L2 归一化。"""
        try:
            from openai import OpenAI  # 延迟导入：没装也能跑 hash 模式
        except ImportError as exc:
            raise RuntimeError("未安装 openai SDK，请执行 pip install openai，或改用 --embed-backend hash") from exc

        if self._client is None:
            self._client = OpenAI(api_key=self.embed_api_key, base_url=self.embed_base_url, timeout=self.timeout)

        vectors: list[list[float]] = []
        batch = 16                                        # 小批量，降低单次请求失败的影响面
        for i in range(0, len(texts), batch):
            resp = self._client.embeddings.create(model=self.embed_model, input=texts[i:i + batch])
            # 接口不保证返回顺序与输入一致，务必用 index 回填
            for item in resp.data:
                vectors.append(item.embedding)
        if len(vectors) != len(texts):
            raise RuntimeError(f"embedding 数量不匹配：期望 {len(texts)}，实际 {len(vectors)}")
        return _l2_normalize(np.asarray(vectors, dtype="float32"))

    # ---------- backend = hash（仅冒烟） ----------
    @staticmethod
    def _hash_embed(texts: list[str], dim: int) -> np.ndarray:
        """
        本地哈希伪向量：把字符 unigram + bigram 哈希到固定维度。
        用 hashlib 而非内置 hash()（后者带随机种子，跨进程不稳定）。
        语义检索能力很弱，仅用于「无 Key 时验证链路是否跑通」，不要用于评估准确率。
        """
        mat = np.zeros((len(texts), dim), dtype="float32")
        for r, t in enumerate(texts):
            grams = list(t) + [t[i:i + 2] for i in range(len(t) - 1)]
            for g in grams:
                h = int(hashlib.blake2b(g.encode("utf-8"), digest_size=8).hexdigest(), 16)
                mat[r, h % dim] += 1.0
        return _l2_normalize(mat)

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """统一入口：返回 (n, dim) 的 float32 矩阵，已 L2 归一化（点积即余弦相似度）。"""
        texts = list(texts)
        if not texts:
            return np.zeros((0, self.s.embed_dim), dtype="float32")
        if self.s.embed_backend == "hash":
            return self._hash_embed(texts, self.s.embed_dim)
        if self.s.embed_backend != "api":
            raise ValueError(f"未知 embed_backend: {self.s.embed_backend}")
        return self._api_embed(texts)

    def embed_one(self, text: str) -> np.ndarray:
        return self.embed([text])[0]


def _l2_normalize(mat: np.ndarray) -> np.ndarray:
    """L2 归一化：归一化后「内积 == 余弦相似度」，FAISS 的 IndexFlatIP 才能当余弦用。"""
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


# ============================================================================
# 5. 向量库（★核心复用 [4]）—— 对应 src/infra/vectorstore/
#     抽象出 add / search / save / load 四个方法。
#     阶段2 换成 pgvector 时，只要实现同样四个方法，检索与业务代码一行都不用改。
# ============================================================================
class VectorStore:
    """向量库抽象基类（接口契约）。"""

    def add(self, vectors: np.ndarray) -> None: ...
    def search(self, query_vec: np.ndarray, top_k: int) -> list[tuple[int, float]]: ...
    def save(self, path: Path) -> None: ...
    def load(self, path: Path) -> bool: ...


class FaissVectorStore(VectorStore):
    """FAISS 实现：IndexFlatIP（暴力内积，精确检索；千级分片足够，无需 IVF/HNSW）。"""

    def __init__(self, dim: int):
        self.dim = dim
        self.index = faiss.IndexFlatIP(dim) if _HAS_FAISS else None

    def add(self, vectors: np.ndarray) -> None:
        self.index.add(np.ascontiguousarray(vectors, dtype="float32"))

    def search(self, query_vec: np.ndarray, top_k: int) -> list[tuple[int, float]]:
        q = np.ascontiguousarray(query_vec.reshape(1, -1), dtype="float32")
        scores, ids = self.index.search(q, top_k)
        return [(int(i), float(s)) for i, s in zip(ids[0], scores[0]) if int(i) >= 0]

    def save(self, path: Path) -> None:
        faiss.write_index(self.index, str(path / "index.faiss"))

    def load(self, path: Path) -> bool:
        f = path / "index.faiss"
        if not f.exists():
            return False
        self.index = faiss.read_index(str(f))
        return True


class NumpyVectorStore(VectorStore):
    """NumPy 兜底实现：没装 faiss 时的降级方案，接口与 FAISS 完全一致（仅本机冒烟用）。"""

    def __init__(self, dim: int):
        self.dim = dim
        self.mat = np.zeros((0, dim), dtype="float32")

    def add(self, vectors: np.ndarray) -> None:
        self.mat = np.vstack([self.mat, np.asarray(vectors, dtype="float32")]) if self.mat.size else np.asarray(vectors, dtype="float32")

    def search(self, query_vec: np.ndarray, top_k: int) -> list[tuple[int, float]]:
        if self.mat.size == 0:
            return []
        scores = self.mat @ query_vec.reshape(-1)          # 已归一化 → 点积即余弦
        idx = np.argsort(-scores)[:top_k]
        return [(int(i), float(scores[i])) for i in idx]

    def save(self, path: Path) -> None:
        np.save(str(path / "index.npy"), self.mat)

    def load(self, path: Path) -> bool:
        f = path / "index.npy"
        if not f.exists():
            return False
        self.mat = np.load(str(f))
        return True


# ============================================================================
# 6. BM25 检索（★核心复用 [5]）—— 对应 src/services/retrieval_service.py
#     关键词召回，弥补向量召回在「编号 / 金额 / 专有名词」上的弱势。
#     自己实现约 40 行，省掉 rank_bm25 依赖，参数与公式完全可控。
# ============================================================================
def tokenize(text: str) -> list[str]:
    """中文分词：优先 jieba；没装则退化为「字符 bigram」，效果差但仍可用。"""
    text = text.lower()
    if _HAS_JIEBA:
        return [t for t in jieba.lcut(text) if t.strip()]
    # 兜底：中文按单字 + 二元组切分，英文数字按词切分
    words = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text)
    return words + [words[i] + words[i + 1] for i in range(len(words) - 1)] if words else []


class SimpleBM25:
    """经典 BM25（k1=1.5, b=0.75），对分片集合建索引并按查询打分。"""

    def __init__(self, corpus: Iterable[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = [tokenize(d) for d in corpus]
        self.doc_len = np.array([len(d) for d in self.docs], dtype="float32")
        self.avg_len = float(self.doc_len.mean()) if len(self.doc_len) else 0.0
        self.n = len(self.docs)

        # 词频表 + 文档频率
        self.tf: list[dict[str, int]] = []
        df: dict[str, int] = {}
        for toks in self.docs:
            f: dict[str, int] = {}
            for t in toks:
                f[t] = f.get(t, 0) + 1
            self.tf.append(f)
            for t in set(toks):
                df[t] = df.get(t, 0) + 1
        # IDF 用平滑版，避免某词出现在所有文档时得 0 甚至负数
        self.idf = {t: float(np.log(1 + (self.n - c + 0.5) / (c + 0.5))) for t, c in df.items()}

    def search(self, query: str, top_k: int) -> list[tuple[int, float]]:
        q_terms = tokenize(query)
        scores = np.zeros(self.n, dtype="float32")
        for i, tf in enumerate(self.tf):
            dl = self.doc_len[i] if i < len(self.doc_len) else 0
            denom_norm = self.k1 * (1 - self.b + self.b * (dl / (self.avg_len or 1.0)))
            for t in q_terms:
                f = tf.get(t)
                if not f:
                    continue
                idf = self.idf.get(t, 0.0)
                scores[i] += idf * (f * (self.k1 + 1)) / (f + denom_norm)
        idx = np.argsort(-scores)[:top_k]
        return [(int(i), float(scores[i])) for i in idx if scores[i] > 0]


# ============================================================================
# 7. 混合检索（★核心复用 [6]）—— 对应 src/services/retrieval_service.py
#     两路召回 → 融合 → 截断 top_k。这里的两行注释是实战经验：
#       · 向量擅长「语义近似」（问"住酒店能报多少"能命中"住宿费标准"）
#       · BM25 擅长「字面精确」（问"3.2.1"或"800元"必须靠它）
#       · 融合用 RRF：不需要归一化，对两路分数的量纲差异免疫，最省心
# ============================================================================
def hybrid_search(
    query: str,
    embedder: EmbeddingClient,
    store: VectorStore,
    bm25: SimpleBM25,
    top_k: int = 3,
    pool: int = 20,
    fusion: str = "rrf",
    rrf_k: int = 60,
    w_vec: float = 0.5,
    w_bm25: float = 0.5,
) -> list[tuple[int, float, dict]]:
    """
    返回 [(chunk_id, 融合分, {"vector": 分, "bm25": 分})]，按融合分降序，最多 top_k 条。
    """
    # ① 向量路召回
    qv = embedder.embed_one(query)
    vec_hits = store.search(qv, min(pool, max(top_k, 1)))
    # ② 关键词路召回
    bm_hits = bm25.search(query, pool)

    vec_score = {i: s for i, s in vec_hits}
    bm_score = {i: s for i, s in bm_hits}
    candidates = set(vec_score) | set(bm_score)

    if fusion == "rrf":
        # 倒数排名融合：score = Σ 1/(k + rank)，rank 从 1 开始
        vec_rank = {i: r for r, (i, _) in enumerate(vec_hits, start=1)}
        bm_rank = {i: r for r, (i, _) in enumerate(bm_hits, start=1)}
        fused = {i: 1.0 / (rrf_k + vec_rank.get(i, pool + 1)) + 1.0 / (rrf_k + bm_rank.get(i, pool + 1))
                 for i in candidates}
    elif fusion == "weighted":
        # 加权融合：先各自 min-max 归一化到 [0,1] 再加权（量纲不同，必须归一化）
        def norm(d: dict[int, float]) -> dict[int, float]:
            if not d:
                return {}
            lo, hi = min(d.values()), max(d.values())
            return {k: (v - lo) / (hi - lo) if hi > lo else 1.0 for k, v in d.items()}
        nv, nb = norm(vec_score), norm(bm_score)
        fused = {i: w_vec * nv.get(i, 0.0) + w_bm25 * nb.get(i, 0.0) for i in candidates}
    else:
        raise ValueError(f"未知融合策略: {fusion}")

    ranked = sorted(fused.items(), key=lambda x: -x[1])[:top_k]
    return [(i, s, {"vector": vec_score.get(i, 0.0), "bm25": bm_score.get(i, 0.0)}) for i, s in ranked]


# ============================================================================
# 8. 提示词组装（★核心复用 [7]）—— 阶段2 内容外置到 src/agent/prompts/v1/qa.md
#     三条硬约束（来自需求评审定的红线）：
#       ① 只依据给定上下文，不得用外部知识臆断
#       ② 必须标注引用编号，让答案可溯源
#       ③ 找不到就直说找不到 —— 制度问答里「不知道」远好于「编一个」
# ============================================================================
SYSTEM_PROMPT = """你是企业报销制度助手。你的回答会直接影响员工的报销行为，因此必须严谨。

规则：
1. 只依据【制度原文】作答，不得使用原文之外的知识推测金额、比例、流程。
2. 每个结论后必须标注引用编号，格式如 [1]；多个依据可并列 [1][3]。
3. 若制度原文中没有答案，明确回答"制度中未检索到相关规定，建议咨询财务部门"，不要编造。
4. 涉及金额、天数、比例时，原样引用原文数字，不要换算或四舍五入。
5. 若原文存在多个适用标准（如按职级区分），先说明判断依据再给结论。
6. 用简洁的中文作答，控制在 200 字以内，除非用户要求详细展开。"""

USER_TEMPLATE = """【制度原文】
{context}

【员工问题】
{question}

请按规则作答，并在结尾列出引用编号对应的条款出处。"""


def build_prompt(question: str, chunks: list[Chunk]) -> list[dict[str, str]]:
    """把检索结果拼成 messages。上下文块带编号与出处，编号即引用锚点。"""
    blocks = []
    for n, c in enumerate(chunks, start=1):
        blocks.append(f"[{n}] 出处：{c.as_ref()}\n{c.text}")
    context = "\n\n".join(blocks) if blocks else "（本次未检索到任何制度片段）"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TEMPLATE.format(context=context, question=question)},
    ]


# ============================================================================
# 9. LLM 调用（★核心复用 [8]）—— 对应 src/agent/llm/base.py + provider_openai.py
#     独立函数、供应商可配置：换 DeepSeek / 通义 / 豆包 / 私有 vLLM 只改 Settings，不改调用方。
#     阶段2 在此之上叠加：重试退避、超时熔断、fallback 供应商、token 计费、全量留痕。
# ============================================================================
def call_llm(
    messages: list[dict[str, str]],
    settings: Settings,
    temperature: float | None = None,
    max_tokens: int | None = None,
    stream: bool = False,
) -> str:
    """
    调用大模型生成回答。使用 OpenAI 兼容协议，因此绝大多数供应商都能直接对接。

    参数：
        messages     : 由 build_prompt 生成的对话消息
        settings     : 含 base_url / api_key / model / timeout
        temperature  : 覆盖默认温度（制度问答建议 0.0~0.3）
        max_tokens   : 覆盖默认最大输出
        stream       : True 时打印流式输出（阶段2 改为 SSE 推给前端）

    返回：模型输出的文本；失败时抛出 RuntimeError（调用方负责提示用户）。
    """
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("未安装 openai SDK，请执行 pip install openai") from exc

    if not settings.llm_api_key:
        raise RuntimeError("未配置 LLM_API_KEY（或改用 --dry-run 只检查提示词）")

    client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url, timeout=settings.timeout)

    try:
        resp = client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            temperature=settings.temperature if temperature is None else temperature,
            max_tokens=settings.max_tokens if max_tokens is None else max_tokens,
            stream=stream,
        )
    except Exception as exc:
        # 统一异常口径：调用方只处理 RuntimeError，便于阶段2 直接换成"降级到备用供应商"
        raise RuntimeError(
            f"LLM 调用失败（base_url={settings.llm_base_url}, model={settings.llm_model}）：{exc}"
        ) from exc

    if not stream:
        content = resp.choices[0].message.content or ""
        # 阶段2：这里补 token 统计与调用留痕（prompt / 检索片段 / 输出）
        if getattr(resp, "usage", None):
            print(f"\n[token] prompt={resp.usage.prompt_tokens} completion={resp.usage.completion_tokens}",
                  file=sys.stderr)
        return content.strip()

    # 流式：逐块打印并拼接（阶段2 换成 SSE 逐块推给前端，逻辑不变）
    collected: list[str] = []
    for chunk in resp:
        delta = chunk.choices[0].delta.content or ""
        if delta:
            print(delta, end="", flush=True)
            collected.append(delta)
    print()
    return "".join(collected).strip()


# ============================================================================
# 10. 索引构建与加载（一次性，成本在 embedding；因此落盘复用，避免每次重复花钱）
# ============================================================================
def build_index(md_path: Path, settings: Settings, force: bool = False) -> tuple[VectorStore, SimpleBM25, list[Chunk]]:
    """读取 Markdown → 切片 → 向量化 → 建 FAISS 与 BM25 双索引 → 落盘。"""
    index_dir = Path(settings.index_dir)
    index_dir.mkdir(parents=True, exist_ok=True)
    meta_path = index_dir / "chunks.json"

    splitter = MarkdownSplitter(settings.chunk_size, settings.chunk_overlap)
    embedder = EmbeddingClient(settings)

    # 命中缓存：文件未变（用 md5 判断）且未强制重建 → 直接加载
    raw = md_path.read_text(encoding="utf-8")
    fingerprint = hashlib.md5(f"{md_path}|{settings.chunk_size}|{settings.chunk_overlap}|"
                              f"{settings.embed_backend}|{settings.embed_model}".encode()).hexdigest()
    cached = meta_path.exists() and json.loads(meta_path.read_text(encoding="utf-8")).get("fingerprint") == fingerprint

    if cached and not force:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        chunks = [Chunk(**c) for c in data["chunks"]]
        store = _make_store(settings, len(chunks))
        if store.load(index_dir):
            print(f"[索引] 复用已有索引：{len(chunks)} 个分片（{md_path.name}）")
            return store, SimpleBM25([c.text for c in chunks]), chunks

    # ---- 正式构建 ----
    chunks = splitter.split(raw, md_path.name)
    if not chunks:
        raise RuntimeError("未切出任何分片，请检查 Markdown 文件内容")

    print(f"[切片] {len(chunks)} 个分片（上限 {settings.chunk_size} 字，重叠 {settings.chunk_overlap} 字）")
    vectors = embedder.embed([c.text for c in chunks])       # ← 唯一花钱/耗时的地方
    dim = vectors.shape[1]

    store = _make_store(settings, dim)
    store.add(vectors)
    store.save(index_dir)
    (index_dir / "chunks.json").write_text(
        json.dumps({"fingerprint": fingerprint, "chunks": [c.__dict__ for c in chunks]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[索引] 已构建并落盘：dim={dim}，{len(chunks)} 个分片 → {index_dir}")
    return store, SimpleBM25([c.text for c in chunks]), chunks


def _make_store(settings: Settings, dim_or_count: int) -> VectorStore:
    """工厂方法：优先 FAISS，缺失时降级 NumPy。上层只依赖 VectorStore 接口。"""
    if _HAS_FAISS:
        return FaissVectorStore(dim_or_count)
    print("[提示] 未安装 faiss-cpu，已降级为 NumPy 向量库（仅适合本机冒烟）", file=sys.stderr)
    return NumpyVectorStore(dim_or_count)


# ============================================================================
# 11. 问答主流程（非核心：阶段2 会被 FastAPI 接口与 Agent 编排取代）
# ============================================================================
def answer(question: str, bundle, settings: Settings, show_context: bool = True, dry_run: bool = False,
           stream: bool = False) -> str:
    store, bm25, chunks = bundle
    embedder = EmbeddingClient(settings)

    hits = hybrid_search(question, embedder, store, bm25,
                         top_k=settings.top_k, pool=settings.candidate_pool, fusion=settings.fusion)

    if show_context:
        print("\n--- 检索结果（研发期可见，用于判断召回质量）---")
        for rank, (cid, score, detail) in enumerate(hits, start=1):
            c = chunks[cid]
            print(f"[{rank}] 融合分={score:.4f} (向量={detail['vector']:.3f} BM25={detail['bm25']:.3f}) "
                  f"| {c.as_ref()}")
            print(f"     {c.text[:120]}{'…' if len(c.text) > 120 else ''}")
        print("---------------------------------------------")

    messages = build_prompt(question, [chunks[cid] for cid, _, _ in hits])

    if dry_run:
        print("\n--- 将要发送的提示词（--dry-run，不调用 LLM）---")
        for m in messages:
            print(f"\n<<< {m['role']} >>>\n{m['content']}")
        return ""

    print("\n--- 回答 ---")
    return call_llm(messages, settings, stream=stream)


def main() -> None:
    parser = argparse.ArgumentParser(description="报销助手 · 阶段1 裸问答原型")
    parser.add_argument("--file", default="data/差旅报销制度.md", help="本地制度 Markdown 文件")
    parser.add_argument("--rebuild", action="store_true", help="强制重建索引")
    parser.add_argument("--topk", type=int, default=3, help="送入提示词的片段数")
    parser.add_argument("--fusion", choices=["rrf", "weighted"], default="rrf", help="混合检索融合策略")
    parser.add_argument("--embed-backend", choices=["api", "hash"], default=None, help="hash=离线冒烟，不调接口")
    parser.add_argument("--dry-run", action="store_true", help="只打印提示词，不调用 LLM")
    parser.add_argument("--stream", action="store_true", help="流式输出")
    parser.add_argument("--question", default=None, help="单条提问后退出；不传则进入交互模式")
    args = parser.parse_args()

    settings = Settings()
    if args.embed_backend:
        settings.embed_backend = args.embed_backend
    settings.top_k = args.topk
    settings.fusion = args.fusion

    md_path = Path(args.file)
    if not md_path.exists():
        print(f"[错误] 找不到制度文件：{md_path}", file=sys.stderr)
        sys.exit(1)

    bundle = build_index(md_path, settings, force=args.rebuild)
    print(f"[环境] faiss={_HAS_FAISS} jieba={_HAS_JIEBA} embed={settings.embed_backend} "
          f"model={settings.llm_model} fusion={settings.fusion}")

    if args.question:
        answer(args.question, bundle, settings, dry_run=args.dry_run, stream=args.stream)
        return

    print("\n进入交互问答，输入 exit 退出；:ctx 切换是否显示检索片段。")
    show_ctx = True
    while True:
        try:
            q = input("\nQ> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            continue
        if q.lower() in ("exit", "quit", "q"):
            break
        if q == ":ctx":
            show_ctx = not show_ctx
            print(f"[设置] 显示检索片段 = {show_ctx}")
            continue
        try:
            answer(q, bundle, settings, show_context=show_ctx, dry_run=args.dry_run, stream=args.stream)
        except Exception as exc:                     # 原型阶段：把异常暴露给用户，便于定位
            print(f"[错误] {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
