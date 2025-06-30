# ![](assets/Logo_white_bg.png) 

**Transform any codebase into an AI-powered video podcast in minutes**

Visit online at [https://vibedoc.online/](https://vibedoc.online/)

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   GitHub    │     │     AI      │     │   Podcast   │     │    Video    │
│    Repo     │ --> │  Analysis   │ --> │   Script    │ --> │  + Audio    │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
      📁                   🧠                  📝                 🎥
```

## Problem

Reading code is hard. Documentation is boring. **VibeDoc makes codebases come alive** through engaging AI conversations.

## Solution

```
INPUT:  https://github.com/stackblitz/bolt.new
OUTPUT: 1) Interactive tutorial website (markdown)
        2) AI podcast script with dialogues
        3) Video with visualizations

┌─────────────────────────────────────────────────────────┐
│  📖 Tutorial Website  ->  🎙️ Podcast Script  ->  🎥 Video │
│                                                         │
│  Emma: "So Alex, what makes bolt.new special?"│
│  Alex: "Great question! Let me show you..."             │
│  [Displays architecture diagram from tutorial]          │
└─────────────────────────────────────────────────────────┘
```

## Quick Start

### Option 1: Run with Python (Development)

```bash
# 1. Clone & Setup
git clone https://github.com/yourusername/vibedoc-server
cd vibedoc-server

# 2. Install dependencies
pip install -r requirements.txt
playwright install chromium

# 3. Configure environment
cp .env.sample .env
# Edit .env with your API keys (ANTHROPIC_API_KEY and ELEVENLABS_API_KEY required)

# 4. Start the server
python main.py
# Server runs at http://localhost:8000
# Swagger UI at http://localhost:8000/docs
```

### Option 2: Run with Docker (Production)

```bash
# 1. Build & Run with Docker (data persists outside container)
docker build -t vibedoc .
docker run -d \
  --name vibedoc \
  -p 8000:8000 \
  --env-file .env \
  -v $(pwd)/output:/app/output \
  -v $(pwd)/temp:/app/temp \
  -v $(pwd)/logs:/app/logs \
  vibedoc

# 2. Generate video (example)
curl -X POST localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/facebook/react"}'
```

### Volume Mounting Explained

```
Host Machine              Docker Container
────────────             ─────────────────
./output/        <--->   /app/output/       # Generated tutorials & videos
./temp/          <--->   /app/temp/         # Cache (audio/video/assets)  
./logs/          <--->   /app/logs/         # Application logs

✅ Files persist after container restart
✅ Direct access from host machine
✅ Easy backup and sharing
```


## API Endpoints

### 📚 Tutorial Generation
- **POST `/generate`** - Analyze codebase and create tutorial
  ```json
  {
    "repo_url": "https://github.com/facebook/react",
    "language": "english",
    "max_abstraction_num": 10,
    "include_patterns": ["*.py", "*.js"],
    "exclude_patterns": ["node_modules", ".git"]
  }
  ```

### 🎙️ Podcast Generation  
- **POST `/generate-podcast-script`** - Transform tutorial into AI dialogue
  ```json
  {
    "tutorial_path": "output/react_en",
    "generation_config": {
      "preset": "deep_dive",
      "language": "english",
      "max_dialogues_per_cluster": 4
    }
  }
  ```

### 🎥 Video Generation
- **POST `/generate-video`** - Create video with AI voices
  ```json
  {
    "podcast_json_path": "output/react_en/podcast_abc123.json",
    "quality": "balanced",
    "generate_audio_podcast": true
  }
  ```

### 📊 Progress & Status
- **GET `/progress/{task_id}`** - Real-time SSE progress stream
- **GET `/progress/video/{task_id}`** - Video generation progress
- **GET `/status/{task_id}`** - Current task status
- **GET `/tasks`** - List all tasks with filtering

### 📥 Downloads
- **GET `/video/{task_id}/download`** - Download generated video (MP4)
- **GET `/video/{task_id}/audio/download`** - Download audio podcast (MP3)

### 🔧 System
- **GET `/`** - Service info and endpoint list
- **GET `/health`** - Health check
- **GET `/docs`** - Swagger UI (interactive API docs)
- **GET `/redoc`** - ReDoc API documentation

## API Flow

```
POST /generate                    # Step 1: Analyze code & create tutorial
     │
     v
[Tutorial Gen] ──SSE──> Progress: ████████░░ 80%
     │
     v                           ┌─ index.md (overview)
   output/react_en/ ─────────────┤─ 01_core_concepts.md
                                 └─ 02_virtual_dom.md
     │
     v
POST /generate-podcast-script     # Step 2: Transform tutorial to dialogue
     │
     v
[Podcast Gen] ──SSE──> Progress: ██████████ 100%
     │
     v
   podcast_abc123.json (with emotions & visualizations)
     │
     v
POST /generate-video              # Step 3: Render video with AI voices and mp3
     │
     v
[Video Gen] ──SSE──> Complete! Download: /video/{id}/download
```

## Configuration

### .env Setup

```bash
# Create .env file
cp .env.sample .env

# Edit with your keys
nano .env
```

```yaml
# .env
ANTHROPIC_API_KEY=sk-ant-api03-xxx      # Required
ELEVENLABS_API_KEY=sk_xxx               # Required

AWS_ACCESS_KEY_ID=AKIAXXXXXXX           # Optional (S3 upload)
AWS_SECRET_ACCESS_KEY=xxx               # Optional (S3 upload)
AWS_S3_BUCKET=my-vibedoc-bucket         # Optional (default: vibedoc)
AWS_REGION=us-east-1                    # Optional (default: eu-central-1)
```

### Docker Compose Alternative

```yaml
# docker-compose.yml (easier for .env files)
docker-compose up -d

# This automatically:
# - Loads .env file
# - Mounts volumes
# - Handles restarts
```


## Tech Stack

- **Backend**: FastAPI + PocketFlow
- **AI**: Claude + ElevenLabs  
- **Video**: MoviePy + FFmpeg
- **Deploy**: Docker + S3

## Example Output

```
output/                       # Mounted from host, survives restarts
└── react_en/
    ├── index.md              # 📖 Tutorial website (view in browser!)
    ├── 01_core_concepts.md   # 📖 Chapter: Core React concepts
    ├── 02_virtual_dom.md     # 📖 Chapter: Virtual DOM explained
    ├── 03_hooks.md           # 📖 Chapter: React Hooks deep dive
    ├── podcast_abc123.json   # 🎙️ AI-generated dialogue script
    └── video/
        ├── podcast_video.mp4 # 🎥 Final video podcast 
        └── podcast_video.mp3 # 🎙 Final audio podcast
        

# View tutorial website:
open ./output/react_en/index.md  # Read the tutorial first!

# Then watch the video:
open ./output/react_en/video/podcast_video.mp4
```

### Managing Docker Container

```bash
# View logs
docker logs -ft vibedoc

# Stop container
docker stop vibedoc

# Start again (data persists)
docker start vibedoc

# Remove container (data still persists on host)
docker rm vibedoc
```

---

**Built for [bolt.new](https://bolt.new) Hackathon** | [Demo](https://vibedoc.online)