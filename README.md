# KCCITM AI Academic Assistant 🎓

> Self-improving RAG + LLM system that answers natural language questions about student academic data. Runs entirely on local campus hardware.

**⚠️ This project is actively in development. Training and improving daily.**

## What it does

Faculty ask questions in plain English:
- "top 5 students by SGPA in semester 1" → SQL pipeline → formatted table
- "tell me about Aakash Singh" → student lookup → full academic profile
- "who's struggling in programming?" → RAG pipeline → grounded analysis

The AI learns from every interaction — bad answers get flagged, fixes get proposed automatically, and the system improves itself.

## Architecture
```
kccitm-ai/
├── backend/          ← FastAPI + Ollama (Python)
│   ├── core/         ← Router, SQL pipeline, RAG pipeline, orchestrator
│   ├── db/           ← MySQL, Milvus, SQLite clients
│   ├── adaptive/     ← Feedback, healing, prompt evolution
│   ├── api/          ← REST API routes
│   ├── training/     ← LoRA fine-tuning pipeline
│   ├── jobs/         ← Scheduled batch jobs (healing, FAQ, prompts)
│   └── data/         ← SQLite databases (not in repo)
└── frontend/         ← Next.js + React + Tailwind
    └── src/
        ├── app/      ← Pages (chat, 9 admin panels)
        └── components/
```

## Tech Stack

| Component | Technology |
|-----------|-----------|
| LLM | Qwen 2.5 7B via Ollama (local) |
| Embeddings | nomic-embed-text (768-dim) |
| Vector DB | Milvus (hybrid dense + BM25) |
| SQL DB | MySQL 8 (Docker) |
| Backend | Python FastAPI + Uvicorn |
| Frontend | Next.js 16 + React 19 + Tailwind |
| Cache | SQLite (exact + semantic similarity) |

## Features

- [x] SQL pipeline (rankings, counts, averages)
- [x] RAG pipeline (HyDE + multi-query + reranking)
- [x] Hybrid pipeline (SQL + RAG in parallel)
- [x] Student lookup (name, roll number, batch)
- [x] Two-tier caching (3200ms → 7ms)
- [x] Self-learning (feedback → healing → prompt evolution)
- [x] Admin dashboard (9 panels)
- [x] JWT authentication + role-based access
- [ ] LoRA fine-tuning (19/500 training pairs collected)
- [ ] Response time optimization
- [ ] Multi-institute support

## Quick Start

### Prerequisites
- Python 3.12
- Node.js 18+
- Docker (for MySQL)
- Ollama

### Setup
```bash
# 1. Clone
git clone https://github.com/YOUR_USERNAME/kccitm-ai-assistant.git
cd kccitm-ai-assistant

# 2. Start MySQL
docker-compose up -d

# 3. Setup Ollama model
ollama pull qwen2.5:7b-instruct
ollama pull nomic-embed-text
cat > Modelfile << 'EOF'
FROM qwen2.5:7b-instruct
PARAMETER temperature 0.15
PARAMETER top_p 0.85
PARAMETER repeat_penalty 1.2
PARAMETER num_ctx 32768
EOF
ollama create kccitm-assistant -f Modelfile

# 4. Backend
cd kccitm-ai/backend
python3.12 -m venv venv312
source venv312/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 5. Frontend (new terminal)
cd kccitm-ai/frontend
npm install
npm run dev

# 6. Open browser
# Chat: http://localhost:3001
# Admin: http://localhost:3001/admin
```

## Self-Learning Pipeline
```
User asks question → AI answers → User rates (👍/👎)
  👍 → training candidate (for LoRA)
  👎 → failure classified → fix proposed → admin approves → AI improves
```

- Daily: Healing job (2 AM) + FAQ generation (3 AM)
- Weekly: Prompt evolution (Sunday 3 AM)
- Monthly: LoRA fine-tuning (when 500+ training pairs)

## Database Schema
```sql
students: roll_no, name, course, branch, enrollment, father_name, gender
semester_results: roll_no, semester, session, sgpa, total_marks, result_status
subject_marks: roll_no, semester, subject_code, subject_name, type,
               internal_marks, external_marks, grade, back_paper
```

- 4,967 students | 15,376 semester results | 172,168 subject marks

## Status

🔧 **Actively in development.** Training the model daily. Fixing edge cases. Improving accuracy.

Current metrics:
- SQL accuracy: ~85%
- Training data: 19/500 for LoRA
- Response time: 2-16s (SQL), 16-40s (RAG)

## Built at

**KCC Institute of Technology and Management**, Greater Noida

## License

MIT
