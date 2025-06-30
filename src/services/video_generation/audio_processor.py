"""
Audio processor using ElevenLabs Multilingual v2
"""
import asyncio
import aiohttp
import os
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import hashlib
import json
import logging

from .models import ELEVENLABS_CONFIG, DEFAULT_VOICES

logger = logging.getLogger(__name__)


class AudioProcessor:
    """Process dialogues to audio using ElevenLabs"""
    
    def __init__(self, api_key: Optional[str] = None, cache_dir: Optional[Path] = None):
        self.api_key = api_key or os.getenv("ELEVENLABS_API_KEY")
        if not self.api_key:
            raise ValueError("ElevenLabs API key not provided")
        
        self.api_base_url = "https://api.elevenlabs.io/v1"
        self.cache_dir = cache_dir or Path("temp/vibedoc_audio_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Semaphore to limit concurrent API calls
        self.api_semaphore = asyncio.Semaphore(5)  # Max 5 concurrent requests
    
    def _get_cache_key(self, text: str, voice_id: str, dialogue_id: str = None) -> str:
        """Generate cache key for audio"""
        data = f"{voice_id}:{text}"
        hash_key = hashlib.sha256(data.encode()).hexdigest()
        # If dialogue_id provided, use it as prefix
        if dialogue_id:
            return f"{dialogue_id}_{hash_key}"
        return hash_key
    
    def _get_cached_audio(self, cache_key: str) -> Optional[Path]:
        """Check if audio is cached"""
        cache_path = self.cache_dir / f"{cache_key}.mp3"
        if cache_path.exists():
            logger.info(f"Audio cache hit for {cache_key}")
            return cache_path
        return None
    
    async def generate_audio_tracks(
        self,
        podcast_data: Dict,
        speaker_1_voice: str,
        speaker_2_voice: str,
        progress_callback=None
    ) -> Dict[str, Tuple[Path, float]]:
        """
        Generate audio for all dialogues
        Returns: Dict[dialogue_id, (audio_path, duration_seconds)]
        """
        # Detect language from metadata or content
        language = self._detect_language(podcast_data)
        logger.info(f"Detected language: {language}")
        
        # Select appropriate voices for language
        voice_1 = self._get_voice_for_language(speaker_1_voice, "speaker_1", language)
        voice_2 = self._get_voice_for_language(speaker_2_voice, "speaker_2", language)
        
        logger.info(f"Using voices: Speaker 1={voice_1}, Speaker 2={voice_2}")
        
        # Collect all dialogues
        dialogues = []
        dialogue_counter = 0
        for cluster_idx, cluster in enumerate(podcast_data.get('clusters', [])):
            for dialogue_idx, dialogue in enumerate(cluster.get('dialogues', [])):
                # Get ID - check both 'dialogue_id' and 'id' fields
                dialogue_id = dialogue.get('dialogue_id') or dialogue.get('id', f"dialogue_{cluster_idx}_{dialogue_idx}_{dialogue_counter}")
                dialogue_counter += 1
                
                # Get text content - check both 'text' and 'content' fields
                text_content = dialogue.get('text') or dialogue.get('content', '')
                
                # Map speaker names
                speaker_name = dialogue.get('speaker', 'speaker_1').lower()
                # Map common names to speaker_1/speaker_2
                if speaker_name in ['lisa', 'emma', 'student', 'learner']:
                    speaker = 'speaker_1'
                elif speaker_name in ['alex', 'teacher', 'expert', 'senior']:
                    speaker = 'speaker_2'
                else:
                    # Default mapping based on order
                    speaker = 'speaker_1' if dialogue_idx % 2 == 0 else 'speaker_2'
                
                dialogues.append({
                    'id': str(dialogue_id),  # Ensure it's a string
                    'text': text_content,
                    'speaker': speaker,
                    'voice_id': voice_1 if speaker == 'speaker_1' else voice_2
                })
        
        # Generate audio in parallel with progress tracking
        total = len(dialogues)
        completed = 0
        results = {}
        
        # Process in batches to avoid overwhelming the API
        batch_size = 5
        for i in range(0, len(dialogues), batch_size):
            batch = dialogues[i:i + batch_size]
            
            tasks = []
            for dialogue in batch:
                task = self._generate_single_audio(
                    dialogue['id'],
                    dialogue['text'],
                    dialogue['voice_id']
                )
                tasks.append(task)
            
            # Wait for batch to complete
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for dialogue, result in zip(batch, batch_results):
                if isinstance(result, Exception):
                    logger.error(f"Failed to generate audio for {dialogue['id']}: {result}")
                    # Use a silence file or skip
                    continue
                
                dialogue_id, audio_path, duration = result
                results[dialogue_id] = (audio_path, duration)
                
                completed += 1
                if progress_callback:
                    await progress_callback(completed, total, dialogue_id)
        
        logger.info(f"Generated {len(results)} audio tracks")
        return results
    
    async def _generate_single_audio(
        self,
        dialogue_id: str,
        text: str,
        voice_id: str
    ) -> Tuple[str, Path, float]:
        """Generate audio for a single dialogue"""
        # Check cache first - include dialogue_id in cache key
        cache_key = self._get_cache_key(text, voice_id, dialogue_id)
        cached_path = self._get_cached_audio(cache_key)
        
        if cached_path:
            # Get duration from cached file
            duration = await self._get_audio_duration(cached_path)
            return (dialogue_id, cached_path, duration)
        
        # Generate new audio with dialogue_id in filename
        output_path = self.cache_dir / f"{cache_key}.mp3"
        
        async with self.api_semaphore:  # Limit concurrent API calls
            try:
                logger.info(f"Generating audio for dialogue {dialogue_id} (voice: {voice_id})")
                
                # Use aiohttp to call ElevenLabs API directly
                async with aiohttp.ClientSession() as session:
                    url = f"{self.api_base_url}/text-to-speech/{voice_id}"
                    
                    headers = {
                        "Accept": "audio/mpeg",
                        "xi-api-key": self.api_key,
                        "Content-Type": "application/json"
                    }
                    
                    data = {
                        "text": text,
                        "model_id": ELEVENLABS_CONFIG["model_id"],
                        "voice_settings": ELEVENLABS_CONFIG["voice_settings"]
                    }
                    
                    async with session.post(url, headers=headers, json=data) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            raise Exception(f"ElevenLabs API error {response.status}: {error_text}")
                        
                        # Save audio file
                        audio_data = await response.read()
                        with open(output_path, 'wb') as f:
                            f.write(audio_data)
                
                # Get duration
                duration = await self._get_audio_duration(output_path)
                
                logger.info(f"Generated audio for {dialogue_id}: {duration:.2f}s")
                return (dialogue_id, output_path, duration)
                
            except Exception as e:
                logger.error(f"Error generating audio for {dialogue_id}: {e}")
                raise
    
    async def _get_audio_duration(self, audio_path: Path) -> float:
        """Get duration of audio file using ffprobe"""
        cmd = [
            'ffprobe',
            '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            str(audio_path)
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode == 0:
            return float(stdout.decode().strip())
        else:
            # Fallback: estimate based on file size (rough approximation)
            file_size = audio_path.stat().st_size
            bitrate = 128000  # 128 kbps
            return (file_size * 8) / bitrate
    
    def _detect_language(self, podcast_data: Dict) -> str:
        """Detect primary language from podcast data"""
        # Check metadata first
        metadata = podcast_data.get('metadata', {})
        if 'language' in metadata:
            lang = metadata['language'].lower()
            # Map full language names to codes
            lang_map = {
                'english': 'en',
                'german': 'de',
                'spanish': 'es',
                'french': 'fr',
                'deutsch': 'de',
                'español': 'es',
                'français': 'fr'
            }
            return lang_map.get(lang, lang[:2])
        
        # Simple heuristic based on first dialogue
        try:
            first_dialogue = podcast_data['clusters'][0]['dialogues'][0]
            # Check both 'text' and 'content' fields
            first_text = (first_dialogue.get('text') or first_dialogue.get('content', '')).lower()
            
            # German indicators
            if any(word in first_text for word in ['der', 'die', 'das', 'und', 'ist', 'nicht']):
                return 'de'
            
            # Spanish indicators
            if any(word in first_text for word in ['el', 'la', 'los', 'las', 'es', 'está']):
                return 'es'
            
            # French indicators
            if any(word in first_text for word in ['le', 'la', 'les', 'est', 'dans', 'pour']):
                return 'fr'
            
        except:
            pass
        
        # Default to English
        return 'en'
    
    def _get_voice_for_language(self, requested_voice: str, speaker: str, language: str) -> str:
        """Get appropriate voice for language"""
        # If requested voice is already valid, use it
        if requested_voice and len(requested_voice) > 10:  # Looks like a voice ID
            return requested_voice
        
        # Otherwise, select from defaults based on language
        if speaker in DEFAULT_VOICES and language in DEFAULT_VOICES[speaker]:
            return DEFAULT_VOICES[speaker][language]
        
        # Fallback to English voice
        return DEFAULT_VOICES[speaker]['en']