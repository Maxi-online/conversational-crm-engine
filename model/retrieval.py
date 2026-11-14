"""Explicit lexical or dense retrieval over an operator-selected catalog."""

from pathlib import Path
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


class CatalogIndex:
    def __init__(self, path, backend="tfidf"):
        text = Path(path).read_text(encoding="utf-8")
        self.documents = [
            Document(page_content=s, metadata={"chunk": i})
            for i, s in enumerate(
                RecursiveCharacterTextSplitter(chunk_size=1200, chunk_overlap=100).split_text(text)
            )
        ]
        if not self.documents:
            raise ValueError("The catalog contains no usable text")
        self.backend = backend
        if backend == "tfidf":
            self.vectorizer = TfidfVectorizer()
            self.matrix = self.vectorizer.fit_transform([d.page_content for d in self.documents])
            self.dense = None
        elif backend == "faiss":
            from langchain_community.vectorstores import FAISS
            from langchain_openai import OpenAIEmbeddings

            self.dense = FAISS.from_documents(
                self.documents, OpenAIEmbeddings(model="text-embedding-3-small")
            )
        else:
            raise ValueError("CRM_RETRIEVAL_BACKEND must be tfidf or faiss")

    def search(self, query, k=3):
        if self.backend == "faiss":
            return self.dense.similarity_search(query, k=k)
        scores = cosine_similarity(self.vectorizer.transform([query]), self.matrix)[0]
        return [self.documents[i] for i in scores.argsort()[::-1][:k] if scores[i] > 0]
