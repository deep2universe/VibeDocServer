"""
Main video generator orchestrator
"""
import asyncio
import json
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
import logging
import uuid

from .models import VideoGenerationRequest, QUALITY_PRESETS
from .asset_renderer import AssetRenderer
from .audio_processor import AudioProcessor
from .video_composer import VideoComposer
from .fast_video_composer import FastVideoComposer
from utils.progress_observer import progress_observer, SSEEventType

logger = logging.getLogger(__name__)


class VideoGenerator:
    """Main orchestrator for video generation"""
    
    def __init__(self, use_fast_composer=True):
        self.asset_renderer = AssetRenderer()
        self.audio_processor = None  # Initialized with API key
        # Use fast composer by default for massive speed improvement
        if use_fast_composer:
            logger.info("Using FastVideoComposer for optimized video generation")
            self.video_composer = FastVideoComposer(use_animated_renderer=True)
        else:
            logger.info("Using standard VideoComposer")
            self.video_composer = VideoComposer(use_animated_renderer=True)
    
    async def generate_video(self, request: VideoGenerationRequest, task_id: str) -> Dict[str, Any]:
        """
        Generate video from podcast JSON
        Returns: Dict with video metadata
        """
        start_time = datetime.now()
        
        try:
            # Load podcast JSON
            logger.info(f"Loading podcast from {request.podcast_json_path}")
            with open(request.podcast_json_path, 'r', encoding='utf-8') as f:
                podcast_data = json.load(f)
            
            # Initialize audio processor with API key
            self.audio_processor = AudioProcessor(api_key=request.elevenlabs_api_key)
            
            # Get quality preset
            quality = QUALITY_PRESETS[request.quality]
            resolution = quality.resolution
            
            # Count total items for progress
            total_dialogues = sum(len(c['dialogues']) for c in podcast_data.get('clusters', []))
            unique_visualizations = self._extract_unique_visualizations(podcast_data)
            
            # Notify task started
            await progress_observer.notify(task_id, SSEEventType.TASK_STARTED, {
                "task_id": task_id,
                "total_dialogues": total_dialogues,
                "total_visualizations": len(unique_visualizations),
                "phases": ["asset_rendering", "audio_generation", "video_composition"]
            })
            
            # Phase 1: Render visual assets
            logger.info("Phase 1: Rendering visual assets")
            visual_assets = await self._render_assets_phase(
                podcast_data, resolution, task_id
            )
            
            # Phase 2: Generate audio tracks
            logger.info("Phase 2: Generating audio tracks")
            audio_tracks = await self._generate_audio_phase(
                podcast_data, request, task_id
            )
            
            # Phase 3: Compose video
            logger.info("Phase 3: Composing video")
            video_path = await self._compose_video_phase(
                podcast_data, visual_assets, audio_tracks, request, quality, task_id
            )
            
            # Calculate final metrics
            end_time = datetime.now()
            render_time = (end_time - start_time).total_seconds()
            file_size_mb = video_path.stat().st_size / (1024 * 1024)
            
            # Calculate video duration
            total_duration = sum(duration for _, duration in audio_tracks.values())
            
            # Log final video location for debugging
            logger.info(f"Final video saved at: {video_path}")
            logger.info(f"Video file exists: {video_path.exists()}")
            logger.info(f"Video file size: {file_size_mb:.2f} MB")
            
            result = {
                "video_path": str(video_path),
                "filename": video_path.name,
                "duration_seconds": total_duration,
                "file_size_mb": round(file_size_mb, 2),
                "resolution": f"{resolution[0]}x{resolution[1]}",
                "fps": quality.fps,
                "render_time_seconds": round(render_time, 2),
                "quality_preset": request.quality,
                "generated_at": datetime.now().isoformat()
            }
            
            # Notify completion
            await progress_observer.notify(task_id, SSEEventType.TASK_COMPLETED, {
                "task_id": task_id,
                "video_path": str(video_path),
                "file_size_mb": round(file_size_mb, 2),
                "duration_seconds": round(total_duration, 2),
                "resolution": f"{resolution[0]}x{resolution[1]}",
                "total_render_time_seconds": round(render_time, 2),
                "download_url": f"/video/{task_id}/download"
            })
            
            return result
            
        except Exception as e:
            logger.error(f"Video generation failed: {e}", exc_info=True)
            
            # Notify failure
            await progress_observer.notify(task_id, SSEEventType.TASK_FAILED, {
                "task_id": task_id,
                "error": str(e),
                "error_phase": getattr(self, '_current_phase', 'unknown'),
                "error_details": {
                    "type": type(e).__name__,
                    "message": str(e)
                }
            })
            
            raise
        finally:
            # Schedule cleanup
            asyncio.create_task(progress_observer.cleanup_task(task_id))
    
    async def _render_assets_phase(
        self,
        podcast_data: Dict,
        resolution: tuple,
        task_id: str
    ) -> Dict[str, Path]:
        """Phase 1: Render all visual assets"""
        self._current_phase = "asset_rendering"
        
        # Extract unique visualizations
        visualizations = self._extract_unique_visualizations(podcast_data)
        total = len(visualizations)
        
        await progress_observer.notify(task_id, SSEEventType.PHASE_STARTED, {
            "phase": "asset_rendering",
            "phase_number": 1,
            "total_phases": 3,
            "description": f"Rendering {total} unique visualizations to images"
        })
        
        visual_assets = {}
        rendered = 0
        
        # Render in parallel with concurrency limit
        semaphore = asyncio.Semaphore(5)  # Max 5 concurrent renders
        
        async def render_with_progress(viz_id: str, viz_data: Dict):
            async with semaphore:
                start_time = asyncio.get_event_loop().time()
                
                try:
                    if viz_data['type'] == 'mermaid':
                        path = await self.asset_renderer.render_mermaid(
                            viz_data['content'],
                            viz_id,
                            resolution
                        )
                    else:  # markdown
                        path = await self.asset_renderer.render_markdown(
                            viz_data['content'],
                            viz_id,
                            resolution
                        )
                    
                    render_time_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)
                    
                    # Notify asset rendered
                    await progress_observer.notify(task_id, SSEEventType.ASSET_RENDERED, {
                        "asset_id": viz_id,
                        "asset_type": viz_data['type'],
                        "render_time_ms": render_time_ms,
                        "cached": render_time_ms < 100,  # Assume cached if very fast
                        "path": str(path)
                    })
                    
                    return viz_id, path
                    
                except Exception as e:
                    logger.error(f"Failed to render {viz_id}: {e}")
                    # Create fallback
                    path = await self.asset_renderer.render_title_slide(
                        f"Error rendering {viz_data['type']}",
                        str(e)[:100],
                        resolution
                    )
                    return viz_id, path
        
        # Render all assets
        tasks = [
            render_with_progress(viz_id, viz_data)
            for viz_id, viz_data in visualizations.items()
        ]
        
        for task in asyncio.as_completed(tasks):
            viz_id, path = await task
            visual_assets[viz_id] = path
            rendered += 1
            
            # Update progress
            await progress_observer.notify(task_id, SSEEventType.PHASE_PROGRESS, {
                "phase": "asset_rendering",
                "current": rendered,
                "total": total,
                "percentage": (rendered / total) * 100,
                "current_item": viz_id
            })
        
        await progress_observer.notify(task_id, SSEEventType.PHASE_COMPLETED, {
            "phase": "asset_rendering",
            "total_rendered": len(visual_assets)
        })
        
        return visual_assets
    
    async def _generate_audio_phase(
        self,
        podcast_data: Dict,
        request: VideoGenerationRequest,
        task_id: str
    ) -> Dict[str, tuple]:
        """Phase 2: Generate audio tracks"""
        self._current_phase = "audio_generation"
        
        total_dialogues = sum(len(c['dialogues']) for c in podcast_data.get('clusters', []))
        
        await progress_observer.notify(task_id, SSEEventType.PHASE_STARTED, {
            "phase": "audio_generation",
            "phase_number": 2,
            "total_phases": 3,
            "description": f"Generating audio for {total_dialogues} dialogues using ElevenLabs"
        })
        
        # Progress callback for audio generation
        async def audio_progress(completed, total, dialogue_id):
            await progress_observer.notify(task_id, SSEEventType.PHASE_PROGRESS, {
                "phase": "audio_generation",
                "current": completed,
                "total": total,
                "percentage": (completed / total) * 100,
                "current_item": f"dialogue_{dialogue_id}"
            })
            
            # Also send specific audio generated event
            if completed > 0:
                dialogue = self._find_dialogue(podcast_data, dialogue_id)
                if dialogue:
                    # Get text content - check both 'text' and 'content' fields
                    text_content = dialogue.get('text') or dialogue.get('content', '')
                    text_preview = text_content[:50] + "..." if len(text_content) > 50 else text_content
                    
                    await progress_observer.notify(task_id, SSEEventType.AUDIO_GENERATED, {
                        "dialogue_id": dialogue_id,
                        "speaker": dialogue.get('speaker', 'unknown'),
                        "duration_seconds": 0,  # Will be updated when known
                        "text_preview": text_preview,
                        "voice_id": request.speaker_1_voice_id if dialogue.get('speaker') == 'speaker_1' else request.speaker_2_voice_id
                    })
        
        # Generate audio
        audio_tracks = await self.audio_processor.generate_audio_tracks(
            podcast_data,
            request.speaker_1_voice_id,
            request.speaker_2_voice_id,
            progress_callback=audio_progress
        )
        
        await progress_observer.notify(task_id, SSEEventType.PHASE_COMPLETED, {
            "phase": "audio_generation",
            "total_generated": len(audio_tracks)
        })
        
        return audio_tracks
    
    async def _compose_video_phase(
        self,
        podcast_data: Dict,
        visual_assets: Dict[str, Path],
        audio_tracks: Dict[str, tuple],
        request: VideoGenerationRequest,
        quality: Any,
        task_id: str
    ) -> Path:
        """Phase 3: Compose final video"""
        self._current_phase = "video_composition"
        
        total_duration = sum(duration for _, duration in audio_tracks.values())
        
        await progress_observer.notify(task_id, SSEEventType.PHASE_STARTED, {
            "phase": "video_composition",
            "phase_number": 3,
            "total_phases": 3,
            "description": f"Composing {len(audio_tracks)} clips into final video"
        })
        
        # Progress callback for composition
        async def composition_progress(processed, total, current_time, total_time):
            percentage = (current_time / total_time * 100) if total_time > 0 else 0
            
            await progress_observer.notify(task_id, SSEEventType.VIDEO_COMPOSITION_PROGRESS, {
                "current_time_seconds": round(current_time, 2),
                "total_duration_seconds": round(total_time, 2),
                "percentage": round(percentage, 2),
                "current_segment": f"clip_{processed}/{total}",
                "fps_actual": 0  # MoviePy doesn't provide real-time FPS
            })
        
        # Prepare settings
        settings = {
            'resolution': quality.resolution,
            'fps': quality.fps,
            'video_bitrate': quality.video_bitrate,
            'transition_style': request.transition_style,
            'speaker_indicator_style': request.speaker_indicator_style,
            'background_music_volume': request.background_music_volume,
            'output_filename': request.output_filename or f"podcast_{task_id}"
        }
        
        # Compose video
        video_path = await self.video_composer.compose_video(
            podcast_data,
            visual_assets,
            audio_tracks,
            settings,
            progress_callback=composition_progress
        )
        
        await progress_observer.notify(task_id, SSEEventType.PHASE_COMPLETED, {
            "phase": "video_composition",
            "output_path": str(video_path),
            "duration": round(total_duration, 2)
        })
        
        return video_path
    
    def _extract_unique_visualizations(self, podcast_data: Dict) -> Dict[str, Dict]:
        """Extract all unique visualizations from podcast data"""
        visualizations = {}
        
        for cluster_idx, cluster in enumerate(podcast_data.get('clusters', [])):
            # Check cluster visualization
            if 'visualization' in cluster:
                cluster_id = cluster.get('cluster_id') or cluster.get('id', f"cluster_{cluster_idx}")
                viz_id = cluster.get('visualization_id', f"cluster_{cluster_id}")
                visualizations[viz_id] = cluster['visualization']
            
            # Check dialogue visualizations
            for dialogue_idx, dialogue in enumerate(cluster.get('dialogues', [])):
                if 'visualization' in dialogue:
                    dialogue_id = dialogue.get('dialogue_id') or dialogue.get('id', f"dialogue_{cluster_idx}_{dialogue_idx}")
                    viz_id = dialogue.get('visualization_id', f"dialogue_{dialogue_id}")
                    visualizations[viz_id] = dialogue['visualization']
        
        return visualizations
    
    def _find_dialogue(self, podcast_data: Dict, dialogue_id: str) -> Optional[Dict]:
        """Find dialogue by ID"""
        dialogue_counter = 0
        for cluster_idx, cluster in enumerate(podcast_data.get('clusters', [])):
            for dialogue_idx, dialogue in enumerate(cluster.get('dialogues', [])):
                # Generate same ID as in audio_processor
                generated_id = dialogue.get('dialogue_id') or dialogue.get('id', f"dialogue_{cluster_idx}_{dialogue_idx}_{dialogue_counter}")
                generated_id = str(generated_id)
                dialogue_counter += 1
                
                if generated_id == dialogue_id:
                    return dialogue
        return None