# ClipZ

> **You talk. It cuts.**  
> An agentic reel editor: upload raw vertical video, talk to it in natural language, and let the AI edit and render your finished reel.

[![App Download](https://img.shields.io/badge/Download_App-Google_Drive-2430FF?style=for-the-badge&logo=google-drive)](https://drive.google.com/file/d/17wO4myTHt4ttLd7YvGGsdshOId11Plg3/view?usp=sharing)
[![Landing Page](https://img.shields.io/badge/Landing_Page-ClipZ-FFD400?style=for-the-badge)](./landing/index.html)

---

## 📱 App Download

Download the latest version of the ClipZ app:
- **Download Link**: [ClipZ on Google Drive](https://drive.google.com/file/d/17wO4myTHt4ttLd7YvGGsdshOId11Plg3/view?usp=sharing)

---

## 🌟 What is ClipZ?

ClipZ is an AI-powered short-form video editor designed for 9:16 reels up to 90 seconds. Instead of manipulating complex timeline tracks manually, you describe what you want in plain words:

> *"Cut the dead air, add yellow word-by-word captions, throw in memes on the punchlines, and add a chill lofi music bed."*

### Key Features
- **Deterministic Rendering**: The LLM plans edits into a structured Timeline JSON (EDL); a deterministic FFmpeg renderer compiles the video with zero unvalidated filter injections.
- **Word-Level Editing**: Edits snap to exact word boundaries from forced-alignment transcripts (80–150 ms padding) rather than arbitrary seconds.
- **Smart Cuts & Pacing**: Automatic silence removal (Silero VAD) and filler word elimination (`"um"`, `"uh"`, `"matlab"`).
- **Dynamic Captions**: Word-by-word highlighted captions burned in with `libass` (English & Hindi).
- **Auto Visuals & Memes**: Transcript keyword matching against 430+ licensed assets (memes, logos, GIFs, B-roll).
- **Face-Tracked 9:16 Framing**: OpenCV YuNet face detection keeps the speaker centered and smoothly pans.
- **Adaptive Sound Design**: Auto-ducked music beds, cut-synced whooshes, pop SFX, and ElevenLabs voiceovers/dubbing.
- **Immutable Timeline & Undo**: Every mutation creates a new version. Undo and redo are instantaneous pointer operations.
- **My Style Engine**: Analyzes your editing habits across past reels and drafts tailored edit prompts in your style.

---

## 🏗️ Architecture & Stack

```
User / App Chat ─▶ Director (Gemini 3.8 Flash) ─▶ Timeline JSON (EDL) ─▶ Renderer ─▶ FFmpeg ─▶ 1080x1920 MP4
                         ▲                              │                     │
                     Analysis                      StyleProfile          Preview Frame
                         ▲                                                    │
                         └──────────────── Critic Fix Loop ◀──────────────────┘
```

- **Brain**: Google Gemini (Director planning, tool calling, and multimodal video critique)
- **Voice**: ElevenLabs (sound effects, voiceover synthesis, English ⇄ Hindi dubbing)
- **Memory**: Tiger Data (PostgreSQL + TimescaleDB for telemetry & timeline versions) + Redis + Arq
- **Hands**: FFmpeg + `libass` + OpenCV (deterministic video compilation)
- **Backend**: FastAPI (Python 3.12) with WebSocket/SSE support

---

## 🚀 Getting Started

### Prerequisites
- Docker & Docker Compose
- Python 3.12+ & [`uv`](https://github.com/astral-sh/uv)
- FFmpeg & ffprobe

### Running Locally

1. **Clone the repository:**
   ```bash
   git clone https://github.com/vaibhavgupta5/ClipZ.git
   cd ClipZ
   ```

2. **Configure environment variables:**
   ```bash
   cp .env.example .env
   # Add your GEMINI_API_KEY and other provider keys in .env
   ```

3. **Start services with Docker Compose:**
   ```bash
   make dev
   ```

4. **Run tests & quality checks:**
   ```bash
   make check
   ```

5. **Access Studio Web App:**
   Navigate to `http://localhost:8000/app` to open the studio UI.

---

## 👥 Builders

- **Vaibhav Gupta**
- **Shivank Tyagi**
- **Prathmesh Singh**
- **Sanya Rai**

---

## 📄 License & Notes
- Built for agentic reel editing.
- Asset licensing rules enforced: monetized exports refuse personal-use-only templates.
