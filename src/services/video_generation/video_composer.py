"""
Video composer using MoviePy
"""
import asyncio
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import logging

from moviepy.editor import (
    ImageClip, AudioFileClip, CompositeVideoClip,
    CompositeAudioClip, concatenate_videoclips,
    VideoFileClip
)
from moviepy.video.fx import resize
from moviepy.video.fx.fadein import fadein
from moviepy.video.fx.fadeout import fadeout
from moviepy.audio.fx import audio_fadein, audio_fadeout

from .asset_renderer import AssetRenderer
from .animated_video_renderer import AnimatedVideoRenderer

logger = logging.getLogger(__name__)


class VideoComposer:
    """Compose video from assets and audio"""
    
    def __init__(self, output_dir: Optional[Path] = None, use_animated_renderer: bool = True):
        # Output dir will be set per project in compose_video method
        self.base_output_dir = output_dir
        self.asset_renderer = AssetRenderer()
        self.animated_renderer = AnimatedVideoRenderer() if use_animated_renderer else None
        self.use_animated_renderer = use_animated_renderer
        
        # Layout configuration
        self.speaker_indicator_size = (100, 100)  # Bigger for better visibility
        self.speaker_padding = 80
        self.transition_duration = 0.5
        
        # Colors - professional learning theme
        self.speaker_colors = {
            "speaker_1": (0, 102, 204),    # Deep professional blue
            "speaker_2": (0, 153, 204)     # Lighter blue
        }
    
    async def compose_video(
        self,
        podcast_data: Dict,
        visual_assets: Dict[str, Path],
        audio_tracks: Dict[str, Tuple[Path, float]],
        settings: Dict,
        progress_callback=None
    ) -> Path:
        """
        Compose final video from assets and audio
        Returns: Path to output video
        """
        logger.info("Starting video composition")
        
        # Get quality settings
        resolution = settings['resolution']
        fps = settings['fps']
        bitrate = settings['video_bitrate']
        
        # Create video clips for each dialogue
        clips = []
        total_duration = 0
        current_time = 0
        
        dialogue_count = sum(len(c['dialogues']) for c in podcast_data['clusters'])
        processed = 0
        
        dialogue_counter = 0
        for cluster_idx, cluster in enumerate(podcast_data.get('clusters', [])):
            for dialogue_idx, dialogue in enumerate(cluster.get('dialogues', [])):
                # Get ID - must match audio_processor logic
                dialogue_id = dialogue.get('dialogue_id') or dialogue.get('id', f"dialogue_{cluster_idx}_{dialogue_idx}_{dialogue_counter}")
                dialogue_id = str(dialogue_id)  # Ensure it's a string
                dialogue_counter += 1
                
                # Skip if no audio
                if dialogue_id not in audio_tracks:
                    logger.warning(f"No audio for dialogue {dialogue_id}, skipping")
                    continue
                
                # Get audio
                audio_path, duration = audio_tracks[dialogue_id]
                
                # Determine speaker for positioning
                speaker_name = dialogue.get('speaker', 'speaker_1').lower()
                if speaker_name in ['lisa', 'emma', 'student', 'learner']:
                    speaker = 'speaker_1'
                    speaker_position = 'left'
                elif speaker_name in ['alex', 'teacher', 'expert', 'senior']:
                    speaker = 'speaker_2'
                    speaker_position = 'right'
                else:
                    speaker = 'speaker_1' if dialogue_idx % 2 == 0 else 'speaker_2'
                    speaker_position = 'left' if speaker == 'speaker_1' else 'right'
                
                # Get visual asset - either video or image
                if self.use_animated_renderer and 'visualization' in dialogue:
                    # Use animated video renderer
                    viz_data = dialogue['visualization']
                    video_path = await self.animated_renderer.render_animated_content(
                        content=viz_data['content'],
                        content_type=viz_data['type'],
                        duration_seconds=duration,
                        asset_id=f"dialogue_{dialogue_id}",
                        resolution=resolution,
                        speaker=speaker,
                        speaker_position=speaker_position
                    )
                    visual_path = video_path
                    is_video = True
                elif 'visualization' in dialogue:
                    # Fall back to static image rendering
                    dialogue_id_str = dialogue.get('dialogue_id') or dialogue.get('id', f"dialogue_{cluster_idx}_{dialogue_idx}")
                    viz_id = f"dialogue_{dialogue_id_str}"
                    
                    if viz_id in visual_assets:
                        visual_path = visual_assets[viz_id]
                    else:
                        # Fallback: render visualization on-the-fly if not pre-rendered
                        logger.warning(f"Visualization not found for {viz_id}, rendering on-the-fly")
                        viz_data = dialogue['visualization']
                        if viz_data['type'] == 'mermaid':
                            visual_path = await self.asset_renderer.render_mermaid(
                                viz_data['content'], viz_id, resolution
                            )
                        else:
                            visual_path = await self.asset_renderer.render_markdown(
                                viz_data['content'], viz_id, resolution
                            )
                    is_video = False
                else:
                    # No visualization - create error slide
                    logger.warning(f"No visualization for dialogue {dialogue_idx} in cluster {cluster_idx}")
                    visual_path = await self._create_text_slide(
                        "No visualization available",
                        cluster.get('cluster_title') or cluster.get('title', 'Chapter'),
                        resolution
                    )
                    is_video = False
                
                # Create clip for this dialogue
                clip = await self._create_dialogue_clip(
                    visual_path=visual_path,
                    audio_path=audio_path,
                    duration=duration,
                    speaker=speaker,
                    resolution=resolution,
                    speaker_style=settings.get('speaker_indicator_style', 'pulse'),
                    is_video=is_video,
                    show_speaker_indicator=not self.use_animated_renderer  # Only show for static images
                )
                
                clips.append(clip)
                current_time += duration
                total_duration += duration
                
                processed += 1
                if progress_callback:
                    await progress_callback(processed, dialogue_count, current_time, total_duration)
        
        if not clips:
            raise ValueError("No clips to compose")
        
        # Concatenate with transitions
        logger.info(f"Concatenating {len(clips)} clips with {settings['transition_style']} transitions")
        final_video = self._concatenate_with_transitions(
            clips,
            settings['transition_style']
        )
        
        # Add background music if provided
        if 'background_music_path' in settings and settings['background_music_path']:
            final_video = self._add_background_music(
                final_video,
                settings['background_music_path'],
                settings.get('background_music_volume', 0.1)
            )
        
        # Determine project output directory from podcast data
        project_name = podcast_data.get('metadata', {}).get('project_name', 'unknown_project')
        podcast_id = podcast_data.get('metadata', {}).get('podcast_id', podcast_data.get('id', 'unknown'))
        
        # Generate output filename - ensure it contains the podcast_id
        output_filename = settings.get('output_filename', f"podcast_video_{podcast_id}.mp4")
        if not output_filename.endswith('.mp4'):
            output_filename += '.mp4'
        
        # Create video subdirectory in project output folder
        output_dir = Path(f"output/{project_name}/video")
        output_dir.mkdir(parents=True, exist_ok=True)
        
        output_path = output_dir / output_filename
        
        # Export video with progress tracking
        logger.info(f"Exporting video to {output_path}")
        
        # Optimize for YouTube quality and speed balance
        import multiprocessing
        cpu_count = multiprocessing.cpu_count()
        
        # YouTube-optimized encoding settings
        final_video.write_videofile(
            str(output_path),
            fps=fps,
            codec='libx264',
            audio_codec='aac',
            bitrate=bitrate,
            preset='fast',  # Good balance for YouTube (not 'faster' which reduces quality too much)
            threads=max(4, cpu_count - 1),  # Use more CPU cores
            audio_bitrate='256k',  # YouTube recommended audio bitrate
            # YouTube-specific codec parameters
            ffmpeg_params=[
                '-crf', '18',  # High quality (lower = better, 18 is visually lossless)
                '-pix_fmt', 'yuv420p',  # YouTube compatibility
                '-movflags', '+faststart',  # Optimize for streaming
                '-profile:v', 'high',  # H.264 high profile
                '-level', '4.0',  # Compatibility level
                '-bf', '2',  # B-frames for better compression
                '-g', str(fps * 2),  # Keyframe interval (2 seconds)
            ],
            logger=None  # Suppress MoviePy's verbose output
        )
        
        # Clean up
        final_video.close()
        for clip in clips:
            clip.close()
        
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
    
    async def _create_dialogue_clip(
        self,
        visual_path: Path,
        audio_path: Path,
        duration: float,
        speaker: str,
        resolution: Tuple[int, int],
        speaker_style: str = "pulse",
        is_video: bool = False,
        show_speaker_indicator: bool = True
    ) -> CompositeVideoClip:
        """Create a video clip for a single dialogue"""
        
        # Load visual content - either video or image
        if is_video:
            # Load pre-rendered video with animations
            visual_clip = VideoFileClip(str(visual_path))
            # Ensure video matches audio duration
            if visual_clip.duration != duration:
                logger.warning(f"Video duration {visual_clip.duration} doesn't match audio {duration}")
                # Trim or loop video to match audio
                if visual_clip.duration > duration:
                    visual_clip = visual_clip.subclip(0, duration)
                else:
                    visual_clip = visual_clip.loop(duration=duration)
        else:
            # Load static image
            visual_clip = ImageClip(str(visual_path), duration=duration)
            
            # Log resolution mismatch but continue
            if visual_clip.size != resolution:
                logger.warning(f"Image size {visual_clip.size} doesn't match target {resolution}, but continuing anyway")
        
        # Load audio
        audio_clip = AudioFileClip(str(audio_path))
        
        # Create speaker indicator only for static images
        if show_speaker_indicator and not is_video:
            if speaker_style == "pulse":
                indicator_clip = self._create_pulsing_indicator(speaker, duration)
            elif speaker_style == "waveform":
                indicator_clip = self._create_waveform_indicator(speaker, audio_clip, duration)
            else:  # static
                indicator_clip = self._create_static_indicator(speaker, duration)
            
            # Position indicator with slight offset from edges for better visibility
            if speaker == "speaker_1":
                position = (self.speaker_padding, resolution[1] - self.speaker_padding - self.speaker_indicator_size[1] - 20)
            else:
                position = (resolution[0] - self.speaker_padding - self.speaker_indicator_size[0], 
                           resolution[1] - self.speaker_padding - self.speaker_indicator_size[1] - 20)
            
            indicator_clip = indicator_clip.set_position(position)
            
            # Composite video with indicator
            video = CompositeVideoClip([visual_clip, indicator_clip])
        else:
            # Use visual clip as is (video already has speaker indicator)
            video = visual_clip
        
        # Set audio
        video = video.set_audio(audio_clip)
        
        return video
    
    def _create_static_indicator(self, speaker: str, duration: float) -> ImageClip:
        """Create static speaker indicator with professional design"""
        # Create circular indicator image
        size = self.speaker_indicator_size
        img = Image.new('RGBA', size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Get color
        color = self.speaker_colors[speaker]
        
        # Draw multiple circles for depth effect
        # Outer glow
        for i in range(5, 0, -1):
            alpha = int(50 * (1 - i/5))
            glow_color = (*color, alpha)
            draw.ellipse(
                [size[0]//2 - size[0]//2 - i*2, 
                 size[1]//2 - size[1]//2 - i*2,
                 size[0]//2 + size[0]//2 + i*2,
                 size[1]//2 + size[1]//2 + i*2],
                fill=glow_color
            )
        
        # Main circle with gradient effect
        draw.ellipse([5, 5, size[0]-5, size[1]-5], fill=color)
        draw.ellipse([8, 8, size[0]-8, size[1]-8], fill=None, outline=(255, 255, 255), width=3)
        
        # Inner highlight for 3D effect
        highlight_size = (size[0]//3, size[1]//3)
        draw.ellipse(
            [15, 15, 15 + highlight_size[0], 15 + highlight_size[1]], 
            fill=(255, 255, 255, 80)
        )
        
        # Add speaker icon/initial with better font
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
        except:
            try:
                font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 48)
            except:
                font = ImageFont.load_default()
        
        # Use more meaningful labels
        label = "S" if speaker == "speaker_1" else "L"  # Student/Learner
        
        # Get text bbox for centering
        bbox = draw.textbbox((0, 0), label, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = (size[0] - text_width) // 2
        y = (size[1] - text_height) // 2
        
        draw.text((x, y), label, fill=(255, 255, 255), font=font)
        
        # Convert to numpy array
        img_array = np.array(img)
        
        return ImageClip(img_array, duration=duration)
    
    def _create_pulsing_indicator(self, speaker: str, duration: float) -> ImageClip:
        """Create pulsing speaker indicator with smooth animation"""
        # Simplified version - create a single frame with pulsing effect
        # using opacity changes instead of complex frame-by-frame animation
        
        size = self.speaker_indicator_size
        img = Image.new('RGBA', size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Get color
        color = self.speaker_colors[speaker]
        
        # Draw multiple concentric circles for pulsing effect
        for i in range(3):
            alpha = 255 - (i * 60)
            circle_color = (*color, alpha)
            offset = i * 8
            draw.ellipse(
                [offset, offset, size[0]-offset, size[1]-offset],
                fill=circle_color
            )
        
        # Add white inner circle
        draw.ellipse([20, 20, size[0]-20, size[1]-20], fill=None, outline=(255, 255, 255), width=3)
        
        # Add speaker label
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
        except:
            try:
                font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 48)
            except:
                font = ImageFont.load_default()
        
        label = "S" if speaker == "speaker_1" else "L"
        bbox = draw.textbbox((0, 0), label, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = (size[0] - text_width) // 2
        y = (size[1] - text_height) // 2
        
        draw.text((x, y), label, fill=(255, 255, 255), font=font)
        
        # Convert to numpy array and create clip
        img_array = np.array(img)
        clip = ImageClip(img_array, duration=duration)
        
        # Return clip without fx to avoid import issues
        # The pulsing effect is already created with the concentric circles
        return clip
    
    def _create_waveform_indicator(self, speaker: str, audio_clip: AudioFileClip, duration: float) -> ImageClip:
        """Create waveform visualization indicator"""
        # Simplified: use static indicator
        # Full implementation would analyze audio and create waveform
        return self._create_static_indicator(speaker, duration)
    
    def _concatenate_with_transitions(self, clips: List[VideoFileClip], transition_style: str) -> VideoFileClip:
        """Concatenate clips with specified transition style"""
        if not clips:
            return None
        
        if len(clips) == 1:
            return clips[0]
        
        if transition_style == "cut":
            # Simple concatenation with small gap
            return concatenate_videoclips(clips, padding=0.1, bg_color=(255, 255, 255))
        
        elif transition_style == "fade":
            # Smooth crossfade between clips
            processed_clips = []
            for i, clip in enumerate(clips):
                if i > 0:
                    # Fade in at start
                    clip = clip.crossfadein(self.transition_duration)
                if i < len(clips) - 1:
                    # Fade out at end
                    clip = clip.crossfadeout(self.transition_duration)
                processed_clips.append(clip)
            
            # Use composite to overlap fades
            if len(processed_clips) > 1:
                result = processed_clips[0]
                for i in range(1, len(processed_clips)):
                    # Start next clip slightly before previous ends
                    next_clip = processed_clips[i].set_start(
                        result.duration - self.transition_duration
                    )
                    result = CompositeVideoClip([result, next_clip])
                return result
            return processed_clips[0]
        
        elif transition_style == "slide":
            # Slide transition with movement
            processed_clips = []
            for i, clip in enumerate(clips):
                if i > 0:
                    # Add slide-in effect (simplified as fade for now)
                    clip = clip.crossfadein(self.transition_duration * 0.8)
                processed_clips.append(clip)
            
            return concatenate_videoclips(processed_clips, method="compose")
        
        else:
            # Default to fade
            return self._concatenate_with_transitions(clips, "fade")
    
    def _add_background_music(self, video: VideoFileClip, music_path: str, volume: float) -> VideoFileClip:
        """Add background music to video"""
        try:
            music = AudioFileClip(music_path)
            
            # Loop music if video is longer
            if music.duration < video.duration:
                music = music.loop(duration=video.duration)
            else:
                music = music.subclip(0, video.duration)
            
            # Adjust volume
            music = music.volumex(volume)
            
            # Apply fade in/out
            music = music.audio_fadein(2.0).audio_fadeout(2.0)
            
            # Composite audio
            final_audio = CompositeAudioClip([video.audio, music])
            
            return video.set_audio(final_audio)
            
        except Exception as e:
            logger.warning(f"Failed to add background music: {e}")
            return video
    
    async def _create_text_slide(self, text: str, title: str, resolution: Tuple[int, int]) -> Path:
        """Create a simple text slide as fallback"""
        # Create image with text
        img = Image.new('RGB', resolution, (255, 255, 255))
        draw = ImageDraw.Draw(img)
        
        try:
            title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
            text_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
        except:
            title_font = ImageFont.load_default()
            text_font = ImageFont.load_default()
        
        # Draw title
        draw.text((100, 100), title, fill=(0, 0, 0), font=title_font)
        
        # Draw text (wrapped)
        y_offset = 200
        words = text.split()
        lines = []
        current_line = []
        
        for word in words:
            current_line.append(word)
            line = ' '.join(current_line)
            bbox = draw.textbbox((0, 0), line, font=text_font)
            if bbox[2] > resolution[0] - 200:  # Leave margins
                current_line.pop()
                lines.append(' '.join(current_line))
                current_line = [word]
        
        if current_line:
            lines.append(' '.join(current_line))
        
        for line in lines[:10]:  # Max 10 lines
            draw.text((100, y_offset), line, fill=(0, 0, 0), font=text_font)
            y_offset += 40
        
        # Save
        temp_path = Path(f"temp/vibedoc_text_slides/text_slide_{hash(text)}.png")
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(temp_path)
        
        return temp_path
    
    async def _extract_audio_podcast(self, video_path: Path, audio_path: Path) -> None:
        """Extract audio from video and save as MP3 podcast"""
        import subprocess
        
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