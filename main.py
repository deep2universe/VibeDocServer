"""
VibeDoc - FastAPI server for tutorial generation with SSE progress streaming
"""
# Suppress warnings from dependencies
import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="imageio_ffmpeg._utils")
warnings.filterwarnings("ignore", message=".*pkg_resources is deprecated.*")

# Fix PIL compatibility issue with MoviePy
import PIL.Image
if not hasattr(PIL.Image, 'ANTIALIAS'):
    PIL.Image.ANTIALIAS = PIL.Image.Resampling.LANCZOS

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from contextlib import asynccontextmanager
import os
import sys
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, HttpUrl

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# Set up logging
logger = logging.getLogger(__name__)

from flow import create_tutorial_flow
from podcast_flow_v2 import create_podcast_flow_v2
from nodes_podcast_script.character_config import CharacterConfig
from services.video_generation.models import VideoGenerationRequest, VideoGenerationResponse
from services.video_generation.video_generator import VideoGenerator
from utils.progress_observer import progress_observer


# Request/Response models
class TutorialGenerationRequest(BaseModel):
    """Request model for tutorial generation"""
    repo_url: Optional[HttpUrl] = Field(None, description="GitHub repository URL")
    local_dir: Optional[str] = Field(None, description="Local directory path (if not using repo_url)")
    include_patterns: List[str] = Field(
        default=["*.py", "*.js", "*.java", "*.cpp", "*.ts", "*.go", "*.rs", "*.kt"],
        description="File patterns to include"
    )
    exclude_patterns: List[str] = Field(
        default=["__pycache__", ".git", "node_modules", ".env", "dist", "build"],
        description="File patterns to exclude"
    )
    max_file_size: int = Field(default=100000, description="Maximum file size in bytes")
    language: str = Field(default="english", description="Output language for the tutorial")
    use_cache: bool = Field(default=True, description="Whether to use LLM cache")
    max_abstraction_num: int = Field(default=10, description="Maximum number of abstractions to identify")
    output_dir: str = Field(default="output", description="Output directory for generated tutorial")
    github_token: Optional[str] = Field(None, description="GitHub token for private repositories")
    
    class Config:
        json_schema_extra = {
            "example": {
                "repo_url": "https://github.com/example/repo",
                "include_patterns": ["*.py", "*.js"],
                "exclude_patterns": ["__pycache__", ".git"],
                "language": "english"
            }
        }


class TutorialGenerationResponse(BaseModel):
    """Response model for tutorial generation"""
    task_id: str = Field(description="Unique task ID for tracking progress")
    status: str = Field(description="Initial status of the task")
    message: str = Field(description="Status message")


class GenerationConfigV2(BaseModel):
    """Configuration for podcast generation v2"""
    preset: str = Field(
        default="overview",
        description="Preset template: overview, deep_dive, comprehensive, custom",
        pattern="^(overview|deep_dive|comprehensive|custom)$"
    )
    custom_prompt: Optional[str] = Field(None, description="Custom instructions for generation")
    focus_areas: Optional[List[str]] = Field(None, description="Specific areas to focus on")
    max_dialogues_per_cluster: int = Field(default=4, ge=1, le=10, description="Maximum dialogues per cluster")
    language: str = Field(default="english", description="Language for the podcast generation")


class PodcastGenerationRequestV2(BaseModel):
    """Request model for podcast generation v2"""
    tutorial_path: str = Field(description="Path to tutorial output directory")
    generation_config: GenerationConfigV2 = Field(description="Configuration for podcast generation")
    character_1: Optional[CharacterConfig] = Field(default=None, description="Optional character 1 configuration")
    character_2: Optional[CharacterConfig] = Field(default=None, description="Optional character 2 configuration")
    
    class Config:
        json_schema_extra = {
            "example": {
                "tutorial_path": "output/project-name",
                "generation_config": {
                    "preset": "deep_dive",
                    "language": "german",
                    "focus_areas": ["architecture", "patterns"],
                    "custom_prompt": "Fokussiere auf praktische Beispiele",
                    "max_dialogues_per_cluster": 4
                },
                "character_1": {
                    "name": "Lisa",
                    "role": "Doktorandin",
                    "personality": "neugierig, analytisch, begeisterungsfähig",
                    "background": "Arbeitet an ihrer Dissertation über verteilte Systeme",
                    "speaking_style": "stellt tiefgehende Fragen, verbindet Konzepte mit ihrer Forschung"
                }
            }
        }


class PodcastGenerationResponse(BaseModel):
    """Response model for podcast generation"""
    task_id: str = Field(description="Unique task ID for tracking progress")
    status: str = Field(description="Initial status of the task")
    message: str = Field(description="Status message")
    applied_config: Dict[str, Any] = Field(description="Configuration that will be applied")


class TaskStatus(BaseModel):
    """Task status model"""
    task_id: str
    status: str  # pending, running, completed, failed
    task_type: str = "tutorial"  # tutorial or podcast
    progress: float  # 0-100
    current_step: Optional[str] = None
    message: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


# Global task storage and SSE queues
tasks: Dict[str, TaskStatus] = {}
sse_queues: Dict[str, asyncio.Queue] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown"""
    # Startup
    print("VibeDoc server starting up...")
    yield
    # Shutdown
    print("VibeDoc server shutting down...")
    # Clean up any remaining queues
    for queue_id in list(sse_queues.keys()):
        queue = sse_queues.pop(queue_id)
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break


# Create FastAPI app
app = FastAPI(
    title="VibeDoc API",
    description="Generate comprehensive codebase tutorials with AI",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def send_sse_event(task_id: str, event_type: str, data: Dict[str, Any]):
    """Send an SSE event to all connected clients for a task"""
    if task_id in sse_queues:
        queue = sse_queues[task_id]
        event_data = {
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data
        }
        await queue.put(json.dumps(event_data))


async def generate_tutorial_task(task_id: str, request: TutorialGenerationRequest):
    """Background task to generate tutorial"""
    try:
        # Update task status
        tasks[task_id].status = "running"
        tasks[task_id].updated_at = datetime.now(timezone.utc)
        
        # Send start event
        if task_id in sse_queues:
            event_data = {
                "type": "task_started",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": {
                    "task_id": task_id,
                    "message": "Tutorial generation started"
                }
            }
            sse_queues[task_id].put_nowait(json.dumps(event_data))
            print("SSE Event queued: task_started")
        
        # Create shared context with SSE callback
        shared_context = {
            "repo_url": str(request.repo_url) if request.repo_url else None,
            "local_dir": request.local_dir,
            "include_patterns": request.include_patterns,
            "exclude_patterns": request.exclude_patterns,
            "max_file_size": request.max_file_size,
            "language": request.language,
            "use_cache": request.use_cache,
            "max_abstraction_num": request.max_abstraction_num,
            "output_dir": request.output_dir,
            "github_token": request.github_token,
            "_task_id": task_id,  # Internal: for SSE updates
            "_sse_callback": lambda event_type, data: asyncio.create_task(
                send_sse_event(task_id, event_type, data)
            )
        }
        
        # Create and run the flow
        flow = create_tutorial_flow()
        
        # Define SSE callback for nodes_code_tutorial to use (sync-friendly version)
        def sse_callback(event_type: str, data: dict):
            """Callback function for nodes_code_tutorial to send SSE events (works from sync context)"""
            # Create the event data
            event_data = {
                "type": event_type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": data
            }
            
            # Put event in queue without awaiting (sync-safe)
            if task_id in sse_queues:
                try:
                    sse_queues[task_id].put_nowait(json.dumps(event_data))
                    print(f"SSE Event queued: {event_type} - {data.get('message', data.get('node', 'unknown'))}")
                except asyncio.QueueFull:
                    print(f"Warning: SSE queue full for task {task_id}")
            else:
                print(f"Warning: No SSE queue found for task {task_id}")
            
            # Update task progress if provided
            if "progress" in data:
                tasks[task_id].progress = data["progress"]
            if "step" in data:
                tasks[task_id].current_step = data["step"]
            tasks[task_id].updated_at = datetime.now(timezone.utc)
        
        # Add SSE callback to shared context so nodes_code_tutorial can use it
        shared_context["sse_callback"] = sse_callback
        shared_context["task_id"] = task_id
        
        # Run the flow in a thread pool since it's synchronous
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            final_output = await asyncio.get_event_loop().run_in_executor(
                executor, flow.run, shared_context
            )
        
        # Clean up temporary repository if it was cloned
        if "_temp_repo_path" in shared_context:
            from src.utils.git_clone import cleanup_temp_repo
            cleanup_temp_repo(shared_context["_temp_repo_path"])
        
        # Get the output directory
        output_dir = shared_context.get("final_output_dir", "output/unknown")
        
        # Update task as completed
        tasks[task_id].status = "completed"
        tasks[task_id].progress = 100
        tasks[task_id].current_step = "Tutorial generation complete"
        tasks[task_id].result = {
            "output_directory": output_dir,
            "project_name": shared_context.get("project_name", "Unknown"),
            "num_chapters": len(shared_context.get("chapters", []))
        }
        tasks[task_id].updated_at = datetime.now(timezone.utc)
        print(f"Task {task_id} marked as completed")
        
        # Send completion event
        if task_id in sse_queues:
            event_data = {
                "type": "task_completed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": {
                    "task_id": task_id,
                    "result": tasks[task_id].result,
                    "message": f"Tutorial successfully generated in {output_dir}"
                }
            }
            sse_queues[task_id].put_nowait(json.dumps(event_data))
            print(f"SSE Event queued: task_completed (queue size: {sse_queues[task_id].qsize()})")
        
    except Exception as e:
        # Clean up temporary repository if it was cloned
        if "_temp_repo_path" in shared_context:
            from src.utils.git_clone import cleanup_temp_repo
            cleanup_temp_repo(shared_context["_temp_repo_path"])
        
        # Update task as failed
        tasks[task_id].status = "failed"
        tasks[task_id].error = str(e)
        tasks[task_id].updated_at = datetime.now(timezone.utc)
        
        # Send error event
        if task_id in sse_queues:
            event_data = {
                "type": "task_failed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": {
                    "task_id": task_id,
                    "error": str(e),
                    "message": "Tutorial generation failed"
                }
            }
            sse_queues[task_id].put_nowait(json.dumps(event_data))
            print(f"SSE Event queued: task_failed - {str(e)}")
        
        raise
    
    finally:
        # Clean up SSE queue after a delay
        await asyncio.sleep(60)  # Keep queue alive for 1 minute after completion
        if task_id in sse_queues:
            sse_queues.pop(task_id, None)


@app.get("/", tags=["General"])
async def root():
    """Root endpoint"""
    return {
        "service": "VibeDoc",
        "version": "1.0.0",
        "description": "AI-powered codebase tutorial and podcast generator",
        "endpoints": {
            "POST /generate": "Start tutorial generation",
            "POST /generate-podcast-script": "Generate podcast script from tutorial",
            "POST /generate-video": "Generate video from podcast",
            "GET /status/{task_id}": "Get task status",
            "GET /progress/{task_id}": "Stream progress updates (SSE)",
            "GET /progress/video/{task_id}": "Stream video generation progress (SSE)",
            "GET /video/{task_id}/status": "Get video generation status",
            "GET /video/{task_id}/download": "Download generated video",
            "GET /video/{task_id}/audio/download": "Download audio podcast (MP3)",
            "GET /tasks": "List all tasks",
            "GET /health": "Health check"
        }
    }


@app.get("/health", tags=["General"])
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "active_tasks": len([t for t in tasks.values() if t.status == "running"])
    }


@app.post("/generate", response_model=TutorialGenerationResponse, tags=["Tutorial Generation"])
async def generate_tutorial(
    request: TutorialGenerationRequest,
    background_tasks: BackgroundTasks
):
    """
    Start tutorial generation for a codebase
    
    This endpoint initiates the tutorial generation process and returns a task ID
    that can be used to track progress via the /progress/{task_id} SSE endpoint.
    """
    # Validate request
    if not request.repo_url and not request.local_dir:
        raise HTTPException(
            status_code=400,
            detail="Either repo_url or local_dir must be provided"
        )
    
    # Create task
    task_id = str(uuid.uuid4())
    task = TaskStatus(
        task_id=task_id,
        status="pending",
        progress=0,
        message="Tutorial generation queued",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc)
    )
    tasks[task_id] = task
    
    # Create SSE queue for this task
    sse_queues[task_id] = asyncio.Queue()
    
    # Add background task
    background_tasks.add_task(generate_tutorial_task, task_id, request)
    
    return TutorialGenerationResponse(
        task_id=task_id,
        status="pending",
        message="Tutorial generation started. Connect to /progress/{task_id} for real-time updates."
    )


@app.get("/status/{task_id}", response_model=TaskStatus, tags=["Tutorial Generation"])
async def get_task_status(task_id: str):
    """Get the current status of a tutorial generation task"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return tasks[task_id]


@app.get("/progress/{task_id}", tags=["Tutorial Generation"])
async def stream_progress(task_id: str):
    """
    Stream real-time progress updates for a tutorial generation task
    
    This endpoint uses Server-Sent Events (SSE) to stream progress updates.
    Connect to this endpoint after starting a generation task to receive real-time updates.
    """
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Create queue if it doesn't exist
    if task_id not in sse_queues:
        sse_queues[task_id] = asyncio.Queue()
    
    async def event_generator():
        """Generate SSE events"""
        queue = sse_queues[task_id]
        
        # Send initial status
        initial_status = {
            "type": "connection_established",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": {
                "task_id": task_id,
                "status": tasks[task_id].status,
                "progress": tasks[task_id].progress
            }
        }
        yield f"data: {json.dumps(initial_status)}\n\n"
        
        # Stream events from queue
        while True:
            try:
                # Wait for new events with timeout
                event_data = await asyncio.wait_for(queue.get(), timeout=30.0)
                yield f"data: {event_data}\n\n"
                
                # Check if task is completed
                if task_id in tasks and tasks[task_id].status in ["completed", "failed"]:
                    # Send final event and close
                    final_event = {
                        "type": "stream_end",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "data": {"task_id": task_id}
                    }
                    yield f"data: {json.dumps(final_event)}\n\n"
                    break
                    
            except asyncio.TimeoutError:
                # Check if task is completed before sending keepalive
                if task_id in tasks and tasks[task_id].status in ["completed", "failed"]:
                    # Send final event and close
                    final_event = {
                        "type": "stream_end",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "data": {"task_id": task_id}
                    }
                    yield f"data: {json.dumps(final_event)}\n\n"
                    break
                
                # Send keepalive
                keepalive = {
                    "type": "keepalive",
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                yield f"data: {json.dumps(keepalive)}\n\n"
            except Exception as e:
                # Send error and close
                error_event = {
                    "type": "error",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "data": {"error": str(e)}
                }
                yield f"data: {json.dumps(error_event)}\n\n"
                break
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Disable proxy buffering
        }
    )




async def generate_podcast_v2_task(task_id: str, request: PodcastGenerationRequestV2):
    """Background task to generate podcast using v2 workflow"""
    try:
        # Update task status
        tasks[task_id].status = "running"
        tasks[task_id].updated_at = datetime.now(timezone.utc)
        
        # Send start event
        await send_sse_event(task_id, "task_started", {
            "task_id": task_id,
            "message": "Podcast generation v2 started"
        })
        
        # Create podcast flow v2
        flow = create_podcast_flow_v2()
        
        # Create a thread-safe progress callback
        progress_queue = asyncio.Queue()
        
        def progress_callback(node, message):
            try:
                # Put the event in the queue to be handled by the async task
                progress_queue.put_nowait({
                    "node": node,
                    "message": message,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
            except:
                pass  # Ignore if queue is full
        
        # Prepare shared context
        shared_context = {
            "tutorial_path": request.tutorial_path,
            "generation_config": request.generation_config.model_dump(),
            "character_1": request.character_1,
            "character_2": request.character_2,
            "task_id": task_id,
            "logging_enabled": True,
            "progress_callback": progress_callback
        }
        
        # Create a task to handle progress events from the queue
        async def handle_progress_events():
            while True:
                try:
                    event = await asyncio.wait_for(progress_queue.get(), timeout=1.0)
                    await send_sse_event(task_id, "node_progress", event)
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    break
        
        progress_handler = asyncio.create_task(handle_progress_events())
        
        # Run the flow in a thread pool
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            await asyncio.get_event_loop().run_in_executor(
                executor, flow.run, shared_context
            )
        
        # Get the output info
        output_info = shared_context.get("podcast_result", {})
        
        # Get log file path
        from src.utils.podcast_logger import PodcastLogger
        logger = PodcastLogger(task_id)
        log_path = logger.get_log_path()
        
        # Update task as completed
        tasks[task_id].status = "completed"
        tasks[task_id].progress = 100
        tasks[task_id].current_step = "Podcast generation v2 complete"
        tasks[task_id].result = {
            "podcast_id": output_info.get("podcast_id", "unknown"),
            "output_path": output_info.get("output_path", "unknown"),
            "statistics": output_info.get("statistics", {}),
            "log_file": log_path
        }
        tasks[task_id].updated_at = datetime.now(timezone.utc)
        
        # Send completion event
        await send_sse_event(task_id, "task_completed", {
            "task_id": task_id,
            "result": tasks[task_id].result,
            "message": f"Podcast v2 successfully generated: podcast_{output_info.get('podcast_id', 'unknown')}.json"
        })
        
    except Exception as e:
        # Update task as failed
        tasks[task_id].status = "failed"
        tasks[task_id].error = str(e)
        tasks[task_id].updated_at = datetime.now(timezone.utc)
        
        # Send error event
        await send_sse_event(task_id, "task_failed", {
            "task_id": task_id,
            "error": str(e),
            "message": "Podcast generation v2 failed"
        })
        
        raise
    
    finally:
        # Cancel the progress handler task
        progress_handler.cancel()
        try:
            await progress_handler
        except asyncio.CancelledError:
            pass
        
        # Clean up SSE queue after a delay
        await asyncio.sleep(60)  # Keep queue alive for 1 minute
        if task_id in sse_queues:
            sse_queues.pop(task_id, None)


@app.post("/generate-podcast-script", response_model=PodcastGenerationResponse, tags=["Podcast Generation"])
async def generate_podcast_script(
    request: PodcastGenerationRequestV2,
    background_tasks: BackgroundTasks
):
    """
    Generate an interactive podcast from tutorial output using simplified v2 workflow
    
    This endpoint creates a podcast dialogue where each markdown file becomes one cluster.
    The workflow is simplified: each file is processed as a whole, and visualizations
    are generated more efficiently.
    
    Key differences from v1:
    - Each markdown file = one cluster (no content slicing)
    - More efficient token usage for visualization generation
    - Configurable character personalities
    - Simplified configuration (no detail_level or max_clusters)
    """
    # Validate tutorial path exists
    if not os.path.exists(request.tutorial_path):
        raise HTTPException(
            status_code=404,
            detail=f"Tutorial path not found: {request.tutorial_path}"
        )
    
    # Check if it contains markdown files
    import glob
    md_files = glob.glob(os.path.join(request.tutorial_path, "*.md"))
    if not md_files:
        raise HTTPException(
            status_code=400,
            detail="No markdown files found in tutorial path"
        )
    
    # Create task
    task_id = str(uuid.uuid4())
    task = TaskStatus(
        task_id=task_id,
        status="pending",
        task_type="podcast_v2",
        progress=0,
        message="Podcast generation v2 queued",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc)
    )
    tasks[task_id] = task
    
    # Create SSE queue for this task
    sse_queues[task_id] = asyncio.Queue()
    
    # Determine applied configuration
    config = request.generation_config.model_dump()
    applied_config = {
        "version": "v2",
        "preset": config.get('preset', 'overview'),
        "language": config.get('language', 'english'),
        "focus_areas": config.get("focus_areas", []),
        "max_dialogues_per_cluster": config.get("max_dialogues_per_cluster", 4),
        "custom_characters": bool(request.character_1 or request.character_2)
    }
    
    if config.get("custom_prompt"):
        applied_config["custom_prompt_preview"] = config["custom_prompt"][:100] + "..."
    
    # Add background task
    background_tasks.add_task(generate_podcast_v2_task, task_id, request)
    
    return PodcastGenerationResponse(
        task_id=task_id,
        status="pending",
        message="Podcast script generation started. Connect to /progress/{task_id} for real-time updates.",
        applied_config=applied_config
    )


@app.get("/tasks", tags=["Task Management"])
async def list_tasks(
    status: Optional[str] = None,
    task_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
):
    """List all tasks with optional filtering"""
    filtered_tasks = list(tasks.values())
    
    # Filter by status if provided
    if status:
        filtered_tasks = [t for t in filtered_tasks if t.status == status]
    
    # Filter by task type if provided
    if task_type:
        filtered_tasks = [t for t in filtered_tasks if t.task_type == task_type]
    
    # Sort by creation time (newest first)
    filtered_tasks.sort(key=lambda t: t.created_at, reverse=True)
    
    # Apply pagination
    total = len(filtered_tasks)
    paginated = filtered_tasks[offset:offset + limit]
    
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "tasks": paginated
    }


@app.post("/validate-mermaid", tags=["Testing"])
async def validate_mermaid_diagrams(json_file_path: str):
    """
    Validate Mermaid diagrams in an existing podcast JSON file.
    This endpoint is for testing the ValidateMermaidDiagrams node.
    
    Args:
        json_file_path: Path to the podcast JSON file to validate
    
    Returns:
        Validation results including the path to the validated file
    """
    import os
    from src.nodes_podcast_script.validate_mermaid_diagrams import ValidateMermaidDiagrams
    import uuid
    
    # Check if file exists
    if not os.path.exists(json_file_path):
        raise HTTPException(status_code=404, detail=f"File not found: {json_file_path}")
    
    # Create task ID for logging
    task_id = str(uuid.uuid4())[:8]
    
    # Create shared context
    shared_context = {
        "podcast_result": {
            "output_path": json_file_path,
            "podcast_id": "validation_test"
        },
        "task_id": task_id,
        "logging_enabled": True
    }
    
    # Track progress
    progress_events = []
    def progress_callback(node_name, message):
        progress_events.append({
            "node": node_name,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
    
    shared_context["progress_callback"] = progress_callback
    
    try:
        # Run validation
        validator = ValidateMermaidDiagrams()
        prep_result = validator.prep(shared_context)
        exec_result = validator.exec(prep_result)
        validator.post(shared_context, prep_result, exec_result)
        
        # Return results
        return {
            "success": True,
            "status": exec_result["status"],
            "output_path": exec_result["output_path"],
            "corrections_count": exec_result.get("corrections_count", 0),
            "mermaid_fixed": exec_result.get("mermaid_fixed", 0),
            "converted_to_markdown": exec_result.get("converted_to_markdown", 0),
            "progress_events": progress_events,
            "log_file": f"logs/podcast_{task_id}.log"
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "progress_events": progress_events
        }


# Video Generation Endpoints
@app.post("/generate-video", response_model=VideoGenerationResponse, tags=["Video Generation"])
async def generate_video(
    request: VideoGenerationRequest,
    background_tasks: BackgroundTasks
):
    """
    Generate a video from a validated podcast JSON file.
    
    This endpoint starts the video generation process in the background and returns
    a task ID that can be used to track progress via SSE.
    
    The video generation includes:
    1. Rendering markdown/mermaid visualizations to images
    2. Generating audio for each dialogue using ElevenLabs
    3. Composing the final video with speaker indicators and transitions
    """
    
    # Validate podcast JSON exists
    if not Path(request.podcast_json_path).exists():
        raise HTTPException(
            status_code=404,
            detail=f"Podcast JSON file not found: {request.podcast_json_path}"
        )
    
    # Load podcast data to estimate duration
    try:
        with open(request.podcast_json_path, 'r') as f:
            podcast_data = json.load(f)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid podcast JSON file: {str(e)}"
        )
    
    # Create task
    task_id = str(uuid.uuid4())
    task = TaskStatus(
        task_id=task_id,
        status="pending",
        task_type="video_generation",
        progress=0,
        message="Video generation queued",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc)
    )
    tasks[task_id] = task
    
    # Estimate duration based on dialogue count and quality
    dialogue_count = sum(len(c.get('dialogues', [])) for c in podcast_data.get('clusters', []))
    quality_multiplier = {"fast": 0.5, "balanced": 1.0, "maximum": 2.5}[request.quality]
    estimated_seconds = int(dialogue_count * 5 * quality_multiplier)  # ~5 seconds per dialogue
    
    # Start background task
    background_tasks.add_task(generate_video_task, task_id, request)
    
    return VideoGenerationResponse(
        task_id=task_id,
        status="pending",
        message=f"Video generation started for {dialogue_count} dialogues",
        sse_url=f"/progress/video/{task_id}",
        estimated_duration_seconds=estimated_seconds
    )


async def generate_video_task(task_id: str, request: VideoGenerationRequest):
    """Background task for video generation"""
    try:
        # Update task status
        tasks[task_id].status = "running"
        tasks[task_id].updated_at = datetime.now(timezone.utc)
        
        # Create video generator
        video_generator = VideoGenerator()
        
        # Generate video
        result = await video_generator.generate_video(request, task_id)
        
        # Update task as completed
        tasks[task_id].status = "completed"
        tasks[task_id].progress = 100
        tasks[task_id].result = result
        tasks[task_id].updated_at = datetime.now(timezone.utc)
        
        logger.info(f"Video generation completed for task {task_id}")
        
        # Start S3 upload if credentials are available
        logger.info(f"=== Checking S3 upload for task {task_id} ===")
        try:
            logger.info("Attempting to initialize S3 service...")
            s3_service = S3UploadService()
            logger.info(f"✓ S3 service initialized successfully")
            
            # Get project info from result
            video_path = Path(result["video_path"])
            podcast_json_path = Path(request.podcast_json_path)
            
            logger.info(f"Video path: {video_path}")
            logger.info(f"Podcast JSON path: {podcast_json_path}")
            
            # Load podcast data for metadata
            with open(podcast_json_path, 'r') as f:
                podcast_data = json.load(f)
            
            project_name = podcast_data.get('metadata', {}).get('project_name', 'unknown_project')
            podcast_id = podcast_data.get('metadata', {}).get('podcast_id', 'unknown')
            
            logger.info(f"Project name: {project_name}")
            logger.info(f"Podcast ID: {podcast_id}")
            
            # Determine directories
            output_dir = podcast_json_path.parent  # Output directory containing the podcast JSON
            temp_audio_dir = Path("temp/vibedoc_audio_cache")  # Audio cache directory
            
            logger.info(f"Output directory: {output_dir}")
            logger.info(f"Temp audio directory: {temp_audio_dir}")
            logger.info("Starting S3 upload...")
            
            # Upload to S3
            upload_result = await s3_service.upload_project(
                project_name=project_name,
                podcast_id=podcast_id,
                output_dir=output_dir,
                temp_audio_dir=temp_audio_dir,
                max_retries=2
            )
            
            # Log results
            if upload_result.get("manifest_url"):
                logger.info(f"S3 upload completed. Manifest: {upload_result['manifest_url']}")
                # Add S3 URLs to task result
                tasks[task_id].result["s3_upload"] = upload_result
            else:
                logger.error(f"S3 upload failed: {upload_result.get('errors', [])}")
                
        except ValueError as e:
            logger.warning(f"S3 upload skipped - credentials not configured: {str(e)}")
        except Exception as e:
            logger.error(f"S3 upload failed: {str(e)}")
            # Don't fail the task if S3 upload fails - video is still generated locally
        
    except Exception as e:
        # Update task as failed
        tasks[task_id].status = "failed"
        tasks[task_id].error = str(e)
        tasks[task_id].updated_at = datetime.now(timezone.utc)
        
        logger.error(f"Video generation failed for task {task_id}: {e}")
        raise


@app.get("/progress/video/{task_id}", tags=["Video Generation"])
async def stream_video_progress(task_id: str):
    """
    SSE endpoint for real-time video generation progress.
    
    Connect to this endpoint after starting video generation to receive:
    - Phase updates (asset rendering, audio generation, video composition)
    - Progress percentages
    - Individual asset/audio completion events
    - Final video metadata when complete
    """
    
    # Check if task exists
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    async def event_generator():
        """Generate SSE events"""
        queue = await progress_observer.subscribe(task_id)
        
        try:
            while True:
                try:
                    # Wait for events with timeout for keepalive
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {event}\n\n"
                    
                    # Check if this is an end event
                    event_data = json.loads(event)
                    if event_data["type"] in ["task_completed", "task_failed", "stream_end"]:
                        break
                        
                except asyncio.TimeoutError:
                    # Send keepalive
                    keepalive = json.dumps({
                        "type": "keepalive",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "data": {}
                    })
                    yield f"data: {keepalive}\n\n"
                    
        finally:
            await progress_observer.unsubscribe(task_id, queue)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Disable nginx buffering
        }
    )


@app.get("/video/{task_id}/status", response_model=TaskStatus, tags=["Video Generation"])
async def get_video_status(task_id: str):
    """Get the current status of a video generation task"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return tasks[task_id]


@app.get("/video/{task_id}/download", tags=["Video Generation"])
async def download_video(task_id: str):
    """
    Download the generated video file.
    
    Only available after video generation is complete.
    """
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = tasks[task_id]
    
    if task.status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Video not ready. Current status: {task.status}"
        )
    
    if not task.result or 'video_path' not in task.result:
        raise HTTPException(
            status_code=500,
            detail="Video path not found in task result"
        )
    
    video_path = Path(task.result['video_path'])
    
    if not video_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Video file not found on disk"
        )
    
    return FileResponse(
        video_path,
        media_type="video/mp4",
        filename=video_path.name,
        headers={
            "Content-Disposition": f'attachment; filename="{video_path.name}"'
        }
    )


@app.get("/video/{task_id}/audio/download", tags=["Video Generation"])
async def download_audio_podcast(task_id: str):
    """
    Download the audio podcast (MP3) file.
    
    Only available after video generation is complete.
    The audio is extracted from the generated video and saved as a standalone MP3 file.
    """
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = tasks[task_id]
    
    if task.status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Audio not ready. Current status: {task.status}"
        )
    
    if not task.result or 'video_path' not in task.result:
        raise HTTPException(
            status_code=500,
            detail="Video path not found in task result"
        )
    
    # Derive audio path from video path
    video_path = Path(task.result['video_path'])
    audio_path = video_path.with_suffix('.mp3')
    
    if not audio_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Audio podcast file not found. It may not have been generated if 'generate_audio_podcast' was set to false."
        )
    
    return FileResponse(
        audio_path,
        media_type="audio/mpeg",
        filename=audio_path.name,
        headers={
            "Content-Disposition": f'attachment; filename="{audio_path.name}"'
        }
    )


# S3 Upload endpoints
from src.services.s3_upload_service import S3UploadService
from typing import Union


@app.get("/s3/manifest/{project_name}/{date}", tags=["S3 Storage"])
async def get_s3_manifests(
    project_name: str,
    date: str
):
    """
    Get all manifest files for a project on a specific date
    
    Args:
        project_name: Name of the project
        date: Date in YYYY-MM-DD format
        
    Returns:
        List of manifest data if found, or status message
    """
    try:
        # Validate date format
        from datetime import datetime
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid date format. Use YYYY-MM-DD"
            )
        
        # Initialize S3 service
        try:
            s3_service = S3UploadService()
        except ValueError as e:
            logger.error(f"S3 service initialization failed: {str(e)}")
            return {
                "status": "error",
                "message": "S3 service not configured. Please set AWS credentials."
            }
        
        # Get manifests
        manifests = await s3_service.get_manifests(project_name, date)
        
        if not manifests:
            return {
                "status": "not_found",
                "message": f"No manifests found for {project_name} on {date}",
                "project_name": project_name,
                "date": date
            }
        
        return {
            "status": "success",
            "count": len(manifests),
            "project_name": project_name,
            "date": date,
            "manifests": manifests
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving manifests: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving manifests: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000,
        loop="asyncio",
        access_log=True,
        workers=1  # For development, use multiple workers in production
    )