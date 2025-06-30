"""
Fast video composer using FFmpeg directly for concatenation
Avoids re-encoding when possible for massive speed improvements
"""
import asyncio
import subprocess
import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import tempfile
import shutil
import logging
from datetime import datetime

from .video_composer import VideoComposer
from moviepy.editor import VideoFileClip, AudioFileClip

logger = logging.getLogger(__name__)


class FastVideoComposer(VideoComposer):
    """Optimized video composer that uses FFmpeg directly"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.temp_dir = None
        
    async def compose_video(
        self,
        podcast_data: Dict,
        visual_assets: Dict[str, Path],
        audio_tracks: Dict[str, Tuple[Path, float]],
        settings: Dict,
        progress_callback=None
    ) -> Path:
        """
        Compose final video using FFmpeg directly for 10-100x speed improvement
        """
        logger.info("Starting FAST video composition with FFmpeg")
        
        # Create temporary directory for intermediate files
        self.temp_dir = Path(tempfile.mkdtemp(prefix="vibedoc_video_"))
        logger.info(f"Using temp directory: {self.temp_dir}")
        
        try:
            # Get settings
            resolution = settings['resolution']
            fps = settings['fps']
            bitrate = settings.get('video_bitrate', '8000k')
            quality = settings.get('quality', 'balanced')
            
            # Prepare clips with audio
            clip_paths = []
            total_clips = sum(len(c['dialogues']) for c in podcast_data['clusters'])
            processed = 0
            
            dialogue_counter = 0
            for cluster_idx, cluster in enumerate(podcast_data.get('clusters', [])):
                for dialogue_idx, dialogue in enumerate(cluster.get('dialogues', [])):
                    # Get dialogue ID
                    dialogue_id = dialogue.get('dialogue_id') or dialogue.get('id', f"dialogue_{cluster_idx}_{dialogue_idx}_{dialogue_counter}")
                    dialogue_id = str(dialogue_id)
                    dialogue_counter += 1
                    
                    # Skip if no audio
                    if dialogue_id not in audio_tracks:
                        logger.warning(f"No audio for dialogue {dialogue_id}, skipping")
                        continue
                    
                    # Get audio
                    audio_path, duration = audio_tracks[dialogue_id]
                    
                    # Get visual path
                    visual_path = await self._get_visual_path(
                        dialogue, cluster, cluster_idx, dialogue_idx, 
                        visual_assets, resolution, duration, dialogue_id
                    )
                    
                    # Create clip with audio using FFmpeg
                    clip_path = await self._create_clip_with_audio(
                        visual_path, audio_path, duration, 
                        dialogue_id, resolution, fps
                    )
                    
                    if clip_path:
                        clip_paths.append(clip_path)
                    
                    processed += 1
                    if progress_callback:
                        await progress_callback(processed, total_clips, processed * duration, total_clips * duration)
            
            if not clip_paths:
                raise ValueError("No clips to compose")
            
            # Determine output path
            project_name = podcast_data.get('metadata', {}).get('project_name', 'unknown_project')
            podcast_id = podcast_data.get('metadata', {}).get('podcast_id', 'unknown')
            
            output_filename = settings.get('output_filename', f"podcast_video_{podcast_id}.mp4")
            if not output_filename.endswith('.mp4'):
                output_filename += '.mp4'
            
            output_dir = Path(f"output/{project_name}/video")
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / output_filename
            
            # Concatenate using FFmpeg concat demuxer
            logger.info(f"Concatenating {len(clip_paths)} clips using FFmpeg concat")
            
            # Choose concatenation method based on quality setting
            if quality == 'fast':
                # Use stream copy for maximum speed
                await self._concatenate_stream_copy(clip_paths, output_path, settings)
            else:
                # Use smart concatenation with minimal re-encoding
                await self._concatenate_smart(clip_paths, output_path, settings)
            
            logger.info(f"Video composition complete: {output_path}")
            
            # Extract audio podcast if requested
            if settings.get('generate_audio_podcast', True):
                audio_path = output_path.with_suffix('.mp3')
                try:
                    await self._extract_audio_podcast(output_path, audio_path)
                    logger.info(f"Audio podcast successfully extracted: {audio_path}")
                except Exception as e:
                    # Log error but don't fail the entire video generation
                    logger.error(f"Failed to extract audio podcast: {e}")
                    logger.error(f"Video generation succeeded but MP3 extraction failed. Video is still available at: {output_path}")
                    # Optionally, we could add a flag to the result indicating MP3 extraction failed
                    # But for now, we just log and continue
            
            return output_path
            
        finally:
            # Clean up temp directory
            if self.temp_dir and self.temp_dir.exists():
                shutil.rmtree(self.temp_dir)
                logger.info("Cleaned up temporary files")
    
    async def _get_visual_path(self, dialogue, cluster, cluster_idx, dialogue_idx, 
                              visual_assets, resolution, duration, dialogue_id):
        """Get or create visual asset for dialogue"""
        if self.use_animated_renderer and 'visualization' in dialogue:
            # Determine speaker
            speaker_name = dialogue.get('speaker', 'speaker_1').lower()
            if speaker_name in ['lisa', 'emma', 'student', 'learner']:
                speaker = 'speaker_1'
                speaker_position = 'left'
            else:
                speaker = 'speaker_2'
                speaker_position = 'right'
            
            # Use animated renderer
            viz_data = dialogue['visualization']
            return await self.animated_renderer.render_animated_content(
                content=viz_data['content'],
                content_type=viz_data['type'],
                duration_seconds=duration,
                asset_id=f"dialogue_{dialogue_id}",
                resolution=resolution,
                speaker=speaker,
                speaker_position=speaker_position
            )
        elif 'visualization' in dialogue:
            # Use static image
            dialogue_id_str = dialogue.get('dialogue_id', f"dialogue_{cluster_idx}_{dialogue_idx}")
            viz_id = f"dialogue_{dialogue_id_str}"
            
            if viz_id in visual_assets:
                return visual_assets[viz_id]
            else:
                # Render on-the-fly
                logger.warning(f"Visualization not found for {viz_id}, rendering on-the-fly")
                viz_data = dialogue['visualization']
                if viz_data['type'] == 'mermaid':
                    return await self.asset_renderer.render_mermaid(
                        viz_data['content'], viz_id, resolution
                    )
                else:
                    return await self.asset_renderer.render_markdown(
                        viz_data['content'], viz_id, resolution
                    )
        else:
            # Create fallback slide
            return await self._create_text_slide(
                "No visualization available",
                cluster.get('cluster_title', 'Chapter'),
                resolution
            )
    
    async def _create_clip_with_audio(self, visual_path: Path, audio_path: Path, 
                                     duration: float, clip_id: str, 
                                     resolution: Tuple[int, int], fps: int) -> Optional[Path]:
        """Create a clip with synchronized audio using FFmpeg"""
        output_path = self.temp_dir / f"clip_{clip_id}.mp4"
        
        try:
            # Check if visual is video or image
            is_video = visual_path.suffix.lower() in ['.mp4', '.webm', '.mov', '.avi']
            
            if is_video:
                # Video input - may need to adjust duration
                cmd = [
                    'ffmpeg', '-y',
                    '-i', str(visual_path),
                    '-i', str(audio_path),
                    '-t', str(duration),  # Limit to audio duration
                    '-c:v', 'copy',  # Try to copy video stream
                    '-c:a', 'aac', '-b:a', '256k',  # Encode audio to AAC
                    '-map', '0:v:0', '-map', '1:a:0',  # Map video from first input, audio from second
                    '-shortest',  # Use shortest stream duration
                    str(output_path)
                ]
            else:
                # Image input - create video from still image
                cmd = [
                    'ffmpeg', '-y',
                    '-loop', '1',  # Loop the image
                    '-framerate', str(fps),
                    '-i', str(visual_path),
                    '-i', str(audio_path),
                    '-c:v', 'libx264',
                    '-preset', 'veryfast',  # Fast encoding for images
                    '-crf', '23',  # Good quality
                    '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-b:a', '256k',
                    '-t', str(duration),  # Duration from audio
                    '-map', '0:v:0', '-map', '1:a:0',
                    str(output_path)
                ]
            
            # Run FFmpeg
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                logger.error(f"FFmpeg failed for clip {clip_id}: {stderr.decode()}")
                return None
            
            return output_path
            
        except Exception as e:
            logger.error(f"Error creating clip {clip_id}: {e}")
            return None
    
    async def _concatenate_stream_copy(self, clip_paths: List[Path], output_path: Path, settings: Dict):
        """Concatenate clips using stream copy (no re-encoding) for maximum speed"""
        # Create concat file
        concat_file = self.temp_dir / "concat_list.txt"
        with open(concat_file, 'w') as f:
            for clip_path in clip_paths:
                f.write(f"file '{clip_path.absolute()}'\n")
        
        # FFmpeg command for stream copy concatenation
        cmd = [
            'ffmpeg', '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', str(concat_file),
            '-c', 'copy',  # Copy all streams without re-encoding
            '-movflags', '+faststart',  # Optimize for streaming
            str(output_path)
        ]
        
        logger.info("Running FFmpeg concat with stream copy (fastest method)")
        
        # Run FFmpeg
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            logger.error(f"FFmpeg concat failed: {stderr.decode()}")
            # Fall back to smart concatenation
            logger.info("Falling back to smart concatenation")
            await self._concatenate_smart(clip_paths, output_path, settings)
    
    async def _concatenate_smart(self, clip_paths: List[Path], output_path: Path, settings: Dict):
        """Smart concatenation with minimal re-encoding only when necessary"""
        # First, check if all clips have compatible formats
        formats_compatible = await self._check_format_compatibility(clip_paths)
        
        if formats_compatible:
            # Use stream copy
            logger.info("All clips compatible, using stream copy")
            await self._concatenate_stream_copy(clip_paths, output_path, settings)
        else:
            # Need to re-encode for compatibility
            logger.info("Clips have different formats, using fast re-encode")
            
            # Create concat file
            concat_file = self.temp_dir / "concat_list.txt"
            with open(concat_file, 'w') as f:
                for clip_path in clip_paths:
                    f.write(f"file '{clip_path.absolute()}'\n")
            
            # Determine hardware acceleration
            hw_accel = await self._detect_hardware_acceleration()
            
            # Build FFmpeg command with optimized settings
            cmd = [
                'ffmpeg', '-y',
                '-f', 'concat',
                '-safe', '0',
                '-i', str(concat_file),
            ]
            
            # Add hardware acceleration if available
            if hw_accel == 'nvidia':
                cmd.extend([
                    '-c:v', 'h264_nvenc',
                    '-preset', 'p4',  # Good balance for NVENC
                    '-rc', 'vbr',
                    '-cq', '23',
                ])
            elif hw_accel == 'videotoolbox':
                cmd.extend([
                    '-c:v', 'h264_videotoolbox',
                    '-b:v', settings.get('video_bitrate', '8000k'),
                ])
            elif hw_accel == 'qsv':
                cmd.extend([
                    '-c:v', 'h264_qsv',
                    '-preset', 'fast',
                ])
            else:
                # CPU encoding with optimized settings
                cmd.extend([
                    '-c:v', 'libx264',
                    '-preset', 'veryfast',  # Much faster than 'fast'
                    '-crf', '23',  # Good quality
                    '-tune', 'fastdecode',  # Optimize for fast decoding
                ])
            
            # Common settings
            cmd.extend([
                '-c:a', 'copy',  # Copy audio without re-encoding
                '-pix_fmt', 'yuv420p',
                '-movflags', '+faststart',
                str(output_path)
            ])
            
            # Run FFmpeg
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Monitor progress
            start_time = datetime.now()
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                
                # Log progress periodically
                line_str = line.decode().strip()
                if 'time=' in line_str:
                    elapsed = (datetime.now() - start_time).total_seconds()
                    logger.debug(f"Encoding progress: {line_str} (elapsed: {elapsed:.1f}s)")
            
            await process.wait()
            
            if process.returncode != 0:
                stderr = await process.stderr.read()
                raise RuntimeError(f"FFmpeg failed: {stderr.decode()}")
    
    async def _check_format_compatibility(self, clip_paths: List[Path]) -> bool:
        """Check if all clips have compatible formats for stream copy"""
        if len(clip_paths) <= 1:
            return True
        
        formats = []
        for clip_path in clip_paths[:3]:  # Check first few clips
            cmd = [
                'ffprobe', '-v', 'quiet',
                '-print_format', 'json',
                '-show_streams',
                str(clip_path)
            ]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, _ = await process.communicate()
            
            try:
                data = json.loads(stdout)
                video_stream = next((s for s in data['streams'] if s['codec_type'] == 'video'), None)
                if video_stream:
                    format_info = {
                        'codec': video_stream.get('codec_name'),
                        'width': video_stream.get('width'),
                        'height': video_stream.get('height'),
                        'pix_fmt': video_stream.get('pix_fmt'),
                        'fps': video_stream.get('r_frame_rate')
                    }
                    formats.append(format_info)
            except:
                return False
        
        # Check if all formats match
        if not formats:
            return False
        
        first_format = formats[0]
        for fmt in formats[1:]:
            if fmt != first_format:
                logger.info(f"Format mismatch detected: {first_format} vs {fmt}")
                return False
        
        return True
    
    async def _detect_hardware_acceleration(self) -> Optional[str]:
        """Detect available hardware acceleration"""
        # Check for NVIDIA GPU
        try:
            cmd = ['ffmpeg', '-hide_banner', '-encoders']
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            output = stdout.decode()
            
            if 'h264_nvenc' in output:
                logger.info("NVIDIA hardware acceleration available")
                return 'nvidia'
            elif 'h264_videotoolbox' in output:
                logger.info("Apple VideoToolbox acceleration available")
                return 'videotoolbox'
            elif 'h264_qsv' in output:
                logger.info("Intel QuickSync acceleration available")
                return 'qsv'
        except:
            pass
        
        logger.info("No hardware acceleration detected, using CPU")
        return None
    
    async def _extract_audio_podcast(self, video_path: Path, audio_path: Path) -> None:
        """Extract audio from video and save as MP3 podcast"""
        cmd = [
            'ffmpeg', '-y',
            '-i', str(video_path),
            '-vn',  # No video
            '-acodec', 'mp3',
            '-ab', '256k',  # High quality audio bitrate
            '-ar', '44100',  # Sample rate
            '-ac', '2',  # Stereo
            str(audio_path)
        ]
        
        logger.info(f"Extracting audio podcast from video")
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            raise RuntimeError(f"Failed to extract audio podcast: {error_msg}")
        
        logger.info(f"Audio podcast extracted successfully: {audio_path}")