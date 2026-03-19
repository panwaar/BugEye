import os
import shutil
import tempfile
import chromadb
from git import Repo
from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma

CODE_EXTENSIONS = [
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".java", ".cpp", ".c", ".go", ".rs",
    ".html", ".css", ".json", ".yaml", ".yml",
    ".md", ".txt", ".env.example"
]

# Global store — persists during Flask session
_vector_store = None
_current_repo = None


def get_vector_store():
    return _vector_store


def clone_repo(repo_name: str) -> str:
    temp_dir = tempfile.mkdtemp()
    repo_url = f"https://github.com/{repo_name}.git"
    Repo.clone_from(repo_url, temp_dir)
    return temp_dir


def load_codebase(repo_path: str) -> list:
    documents = []
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if not d.startswith('.')
                   and d not in ['node_modules', 'venv', '__pycache__', 'dist', 'build']]
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            if ext in CODE_EXTENSIONS:
                filepath = os.path.join(root, file)
                try:
                    loader = TextLoader(filepath, encoding='utf-8')
                    docs = loader.load()
                    for doc in docs:
                        doc.metadata['source'] = os.path.relpath(filepath, repo_path)
                    documents.extend(docs)
                except Exception:
                    pass
    return documents


def build_vector_store(repo_name: str) -> Chroma:
    global _vector_store, _current_repo

    repo_path = clone_repo(repo_name)
    try:
        documents = load_codebase(repo_path)
        if not documents:
            raise ValueError("No code files found in repository.")

        splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
        chunks = splitter.split_documents(documents)

        embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

        vector_store = Chroma.from_documents(
            documents=chunks,
            embedding=embeddings,
            collection_name=repo_name.replace("/", "_")
        )

        _vector_store = vector_store
        _current_repo = repo_name
        return vector_store

    finally:
        shutil.rmtree(repo_path, ignore_errors=True)


def search_codebase(vector_store: Chroma, query: str, k: int = 4) -> str:
    results = vector_store.similarity_search(query, k=k)
    if not results:
        return "No relevant code found."
    context = []
    for doc in results:
        source = doc.metadata.get('source', 'unknown')
        context.append(f"### `{source}`\n```\n{doc.page_content}\n```")
    return "\n\n".join(context)


def chat_with_codebase(question: str) -> str:
    """Answer a question about the codebase using RAG"""
    if not _vector_store:
        return "No codebase indexed yet. Please analyze a repository first."

    context = search_codebase(_vector_store, question, k=5)

    from langchain_groq import ChatGroq
    from langchain_core.messages import SystemMessage, HumanMessage
    from dotenv import load_dotenv
    load_dotenv()

    llm = ChatGroq(
        model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        temperature=0,
        api_key=os.getenv("GROQ_API_KEY")
    )

    response = llm.invoke([
        SystemMessage(content="""You are an expert software engineer helping a developer understand a codebase.
Answer questions clearly and specifically based on the code context provided.
Reference specific files and line patterns when relevant.
If the answer isn't in the provided context, say so honestly."""),
        HumanMessage(content=f"""## Codebase context:
{context}

## Question:
{question}

Answer based on the actual code above.""")
    ])

    return response.content