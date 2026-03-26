# Technology Stack

**Analysis Date:** 2026-03-11

## Languages

**Primary:**
- Python 3.x - All application code (no version pin detected; no `.python-version` file)

**Secondary:**
- JSON - Student record storage format inside MySQL `jsontext` column
- SQL - MySQL queries in `db_marks.py`, `db_connection.py`, `sql_executor.py`

## Runtime

**Environment:**
- Python (CPython) - No version lockfile present

**Package Manager:**
- pip
- Lockfile: `requirements.txt` present (unpinned — no version specifiers on any dependency)

## Frameworks

**Core:**
- Streamlit (unpinned) - Web UI framework for the chatbot interface (`app.py`)

**ML/Embedding:**
- sentence-transformers (unpinned) - Loads `BAAI/bge-base-en-v1.5` embedding model for FAISS vector search
- faiss-cpu (unpinned) - In-memory vector index for semantic similarity search over career knowledge base

**Data:**
- pandas (unpinned) - All tabular data manipulation: student DataFrames, semester/subject rows

**Database Driver:**
- mysql-connector-python (unpinned) - Direct MySQL connectivity; no ORM

**Build/Dev:**
- Docker / docker-compose - MySQL container defined in `docker-compose.yml`

## Key Dependencies

**Critical:**
- `streamlit` - Entire production UI depends on it; entry point is `streamlit run app.py`
- `mysql-connector-python` - Only database driver; all DB access in `db_marks.py` uses `mysql.connector.connect()`
- `sentence-transformers` - Loads HuggingFace model `BAAI/bge-base-en-v1.5` at startup via `@st.cache_resource`
- `faiss-cpu` - In-memory vector index; rebuilt each run from 4 hardcoded career counseling sentences

**Infrastructure:**
- `pandas` - Used for all result data structures passed between layers and rendered via `st.dataframe()`

**Prototype-only (not used by `app.py`):**
- `ollama` CLI (external binary, not in requirements.txt) - Called via `subprocess` in `main.py` to run `phi3` model locally
- `numpy` - Imported in `main.py` prototype only

## Configuration

**Environment:**
- No `.env` file detected; no environment variable loading code found
- MySQL credentials are hardcoded directly in source files (see INTEGRATIONS.md)
- No configuration management library (no `python-dotenv`, `pydantic-settings`, etc.)

**Build:**
- `docker-compose.yml` - Defines MySQL service `mysql_kccitm` with image `mysql:latest`, port `3306:3306`, and a named volume `mysql_data`
- `mysql-custom.cnf/` - Directory present (not a file); mounted into MySQL container at `/etc/mysql/conf.d/custom.cnf`

**Persistent State:**
- `chat_memory.json` - Written at runtime by `app.py` to persist `last_roll` and `past_query_history` across Streamlit restarts

## Platform Requirements

**Development:**
- Python 3.x with pip
- Docker + Docker Compose for MySQL
- OR a local MySQL 8.x instance on `localhost:3306`
- Internet access on first run (to download `BAAI/bge-base-en-v1.5` from HuggingFace)

**Production:**
- Streamlit server (local or hosted)
- MySQL instance accessible at `localhost:3306` with database `kccitm`
- Sufficient RAM for FAISS index + sentence-transformer model in memory

---

*Stack analysis: 2026-03-11*
