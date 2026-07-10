# CREASE Brain 🧠

CREASE Brain is the backend FastAPI application for the **CREASE** cricket scoring platform. It orchestrates user authentication, real-time WebSocket scoreboards, database connections to Supabase (PostgreSQL), and AI services powered by Google Gemini 2.5 Flash.

---

## 🏗️ Architecture & System Design
- **System Design & Architecture**: Read the full system design document in the [System Design Guide](file:///v:/workspace/business/crease/docs/system-design.md).
- **Interview Preparation**: If you're preparing for interviews, check out our [Interview Prep Guide](file:///v:/workspace/business/crease/docs/interview-prep.md) to understand design decisions, databases, real-time aspects, and AI integrations.

---

## 🛠️ Tech Stack & Key Services
- **FastAPI**: Asynchronous Python web framework for REST API endpoints and WebSockets.
- **Supabase (PostgreSQL)**: Handles relational database operations, connection pooling, and client-level transactions.
- **WebSocket connection manager**: Per-match WebSocket room management for streaming live scorecards to active fans.
- **Google Gemini 2.5 Flash**: Processes match-telemetry logs for summary/commentary generation and powers the scouting analytical RAG pipeline.

---

## 🚀 Run Locally

### Prerequisites
- Python 3.10+
- Pip (Python Package Manager)

### Step-by-Step Setup

1. **Navigate to the brain directory**:
   ```bash
   cd crease-brain
   ```

2. **Create and activate a virtual environment**:
   - On Windows (PowerShell):
     ```powershell
     python -m venv venv
     .\venv\Scripts\Activate.ps1
     ```
   - On macOS/Linux:
     ```bash
     python3 -m venv venv
     source venv/bin/activate
     ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure Environment Variables**:
   Create a `.env` file in the root of `crease-brain/` and add the following keys:
   ```env
   SUPABASE_URL=your_supabase_project_url
   SUPABASE_KEY=your_supabase_anon_or_service_key
   GEMINI_API_KEY=your_google_gemini_api_key
   ```

5. **Start the FastAPI Development Server**:
   ```bash
   uvicorn main:app --reload --port 8000
   ```
   The backend API will be available at `http://localhost:8000`. You can access the interactive Swagger documentation at `http://localhost:8000/docs`.
