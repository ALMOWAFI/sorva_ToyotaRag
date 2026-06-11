"""
retrieve.py — FAISS retrieval for Toyota knowledge base
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import HuggingFaceEmbeddings

load_dotenv()

INDEX_PATH = os.getenv("FAISS_INDEX_PATH", "faiss_index")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
TOP_K = int(os.getenv("TOP_K", "5"))

_embeddings = None
_vectorstore = None


def _load():
    global _embeddings, _vectorstore
    if _vectorstore is None:
        index_file = Path(INDEX_PATH) / "index.faiss"
        if not index_file.exists():
            raise FileNotFoundError(
                f"FAISS index not found at {INDEX_PATH}. Run: python build_index.py"
            )
        _embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)
        _vectorstore = FAISS.load_local(INDEX_PATH, _embeddings, allow_dangerous_deserialization=True)


def retrieve(query: str, top_k: int = TOP_K) -> list[dict]:
    _load()
    results = _vectorstore.similarity_search_with_score(query, k=top_k)
    return [
        {
            "content": doc.page_content,
            "metadata": doc.metadata,
            "score": float(score),
        }
        for doc, score in results
    ]
