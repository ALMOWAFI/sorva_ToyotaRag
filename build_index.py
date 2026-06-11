"""
build_index.py — Build FAISS index from Toyota Sequoia knowledge base

Usage:
    python build_index.py
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import HuggingFaceEmbeddings

load_dotenv()

KB_DIR = Path(os.getenv("KNOWLEDGE_BASE_DIR", "knowledge_base"))
INDEX_PATH = os.getenv("FAISS_INDEX_PATH", "faiss_index")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "400"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "50"))


def load_documents():
    docs = []
    for path in KB_DIR.rglob("*"):
        if path.suffix == ".pdf":
            print(f"  Loading PDF: {path.name}")
            loader = PyPDFLoader(str(path))
            docs.extend(loader.load())
        elif path.suffix == ".txt":
            print(f"  Loading TXT: {path.name}")
            loader = TextLoader(str(path), encoding="utf-8")
            docs.extend(loader.load())

    for doc in docs:
        doc.metadata["source_file"] = Path(doc.metadata.get("source", "")).name

    return docs


def main():
    print(f"Loading documents from {KB_DIR}...")
    docs = load_documents()
    if not docs:
        print("No documents found. Add PDFs or TXT files to knowledge_base/ subfolders.")
        return

    print(f"Loaded {len(docs)} pages/documents. Splitting into chunks...")
    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    chunks = splitter.split_documents(docs)
    print(f"Created {len(chunks)} chunks.")

    print(f"Loading embedding model: {EMBEDDING_MODEL}...")
    embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)

    print("Building FAISS index...")
    vectorstore = FAISS.from_documents(chunks, embeddings)
    vectorstore.save_local(INDEX_PATH)
    print(f"Index saved to {INDEX_PATH}/")


if __name__ == "__main__":
    main()
