import base64
import zlib
import requests
from pathlib import Path

artifact_dir = Path(__file__).resolve().parent
artifact_dir.mkdir(parents=True, exist_ok=True)

def save_mermaid_image(mermaid_text, filename):
    compressed = zlib.compress(mermaid_text.strip().encode('utf-8'), 9)
    b64 = base64.urlsafe_b64encode(compressed).decode('ascii')
    url = f"https://kroki.io/mermaid/png/{b64}"
    
    filepath = artifact_dir / filename
    print(f"Fetching {filename}...")
    try:
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            with open(filepath, "wb") as f:
                f.write(response.content)
            print(f"Saved successfully: {filepath}")
        else:
            print(f"Failed to fetch {filename}: HTTP {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Error fetching {filename}: {e}")

arch_mermaid = """
graph TD
    Client([User / Client])
    subgraph FrontendApp [Frontend Layer]
        NextJS[Next.js 14 App Router]
        Tauri[Tauri Windows Desktop App]
        NextJS -.-> Tauri
    end
    subgraph BackendApp [Backend Layer - Python FastAPI]
        API[API Endpoints: Chat, Upload, Metadata]
        RAGPlugin[RAG Pipeline & Extraction]
        AgentLLM[LLM / GenAI Engine]
        CeleryWorkers[Async Job Queue / Workers]
    end
    subgraph DataStorage [Data & Storage Layer]
        Postgres[(PostgreSQL + pgvector)]
        Redis[(Redis Session & Broker)]
        MinIO[(MinIO Object Storage)]
    end
    Client -->|HTTPS / WebSocket| FrontendApp
    FrontendApp -->|REST / JSON| BackendApp
    API --> RAGPlugin
    API --> AgentLLM
    API -->|Offload heavy jobs| CeleryWorkers
    RAGPlugin -->|Store/Search Vectors| Postgres
    RAGPlugin -->|Read/Write PDFs| MinIO
    AgentLLM -->|Context lookup| Postgres
    CeleryWorkers <-->|Message Broker| Redis
    API <-->|Cache & State| Redis
"""
save_mermaid_image(arch_mermaid, "System_Architecture_Graph.png")


ingest_mermaid = """
flowchart LR
    A[Upload Doc] --> B[MinIO Storage]
    B --> C[Celery Trigger]
    C --> D[PDF Parsing]
    D --> E[Table Extract]
    E --> F[Semantic Chunk]
    F --> G[MiniLM Embeddings]
    G --> H[(PGVector)]
"""
save_mermaid_image(ingest_mermaid, "Ingestion_Pipeline_Graph.png")


query_mermaid = """
flowchart TD
    Q(User Query) --> R1(Vector Search)
    Q --> R2(SQL Match)
    R1 --> RR(FlashRank Reranking)
    R2 --> RR
    RR --> CM(Context Assembly)
    CM --> MS(LLM Streaming)
"""
save_mermaid_image(query_mermaid, "Query_Pipeline_Graph.png")
