"""
Pydantic models for video generation API
"""
from pydantic import BaseModel, Field
from typing import Optional, Literal, Dict, Any
from pathlib import Path


class VideoGenerationRequest(BaseModel):
    """Request model for video generation"""
    
    # Required fields
    podcast_json_path: str = Field(
        description="Path to validated podcast JSON file"
    )
    
    # Optional fields with sensible defaults
    quality: Literal["fast", "balanced", "maximum"] = Field(
        default="balanced",
        description="Video quality preset affecting resolution and render time"
    )
    
    speaker_1_voice_id: str = Field(
        default="uYXf8XasLslADfZ2MB4u",  # Studentin / Student voice
        description="ElevenLabs voice ID for speaker 1"
    )
    
    speaker_2_voice_id: str = Field(
        default="66PBrqxlmGTw9isOc21D",  # Senior Developer voice
        description="ElevenLabs voice ID for speaker 2"
    )
    
    output_filename: Optional[str] = Field(
        default=None,
        description="Output filename without extension. Default: podcast_{id}_video"
    )
    
    background_music_volume: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Background music volume (0.0-1.0)"
    )
    
    transition_style: Literal["cut", "fade", "slide"] = Field(
        default="fade",
        description="Transition style between slides"
    )
    
    speaker_indicator_style: Literal["pulse", "static", "waveform"] = Field(
        default="pulse",
        description="Animation style for speaker indicators"
    )
    
    elevenlabs_api_key: Optional[str] = Field(
        default=None,
        description="ElevenLabs API key (uses env var if not provided)"
    )
    
    generate_audio_podcast: bool = Field(
        default=True,
        description="Generate standalone MP3 podcast file alongside video"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "podcast_json_path": "output/express_de/podcast_abc123_validated.json",
                "quality": "balanced",
                "speaker_1_voice_id": "EXAVITQu4vr4xnSDxMaL",
                "speaker_2_voice_id": "ErXwobaYiN019PkySvjV",
                "transition_style": "fade"
            }
        }


class VideoGenerationResponse(BaseModel):
    """Response model for video generation"""
    task_id: str = Field(description="Unique task ID for progress tracking")
    status: str = Field(description="Initial status (always 'pending')")
    message: str = Field(description="Confirmation message")
    sse_url: str = Field(description="URL for SSE progress stream")
    estimated_duration_seconds: int = Field(description="Estimated processing time")


class VideoQualityPreset(BaseModel):
    """Video quality preset configuration"""
    resolution: tuple[int, int]
    fps: int
    video_bitrate: str
    render_scale: float
    description: str


# Quality presets
QUALITY_PRESETS: Dict[str, VideoQualityPreset] = {
    "fast": VideoQualityPreset(
        resolution=(1920, 1080),  # YouTube minimum for HD
        fps=30,  # YouTube standard
        video_bitrate="5M",  # YouTube recommended for 1080p30
        render_scale=1.0,
        description="Fast rendering for YouTube HD"
    ),
    "balanced": VideoQualityPreset(
        resolution=(1920, 1080),
        fps=30,  # YouTube optimal
        video_bitrate="8M",  # YouTube recommended for better quality
        render_scale=1.0,
        description="YouTube recommended settings"
    ),
    "maximum": VideoQualityPreset(
        resolution=(1920, 1080),  # Keep 1080p for compatibility
        fps=60,  # YouTube supports 60fps
        video_bitrate="12M",  # YouTube max recommended for 1080p60
        render_scale=1.0,
        description="YouTube maximum quality"
    )
}


# ElevenLabs configuration
ELEVENLABS_CONFIG = {
    "model_id": "eleven_multilingual_v2",
    "output_format": "mp3_44100_128",
    "voice_settings": {
        "stability": 0.5,
        "similarity_boost": 0.75,
        "style": 0,
        "use_speaker_boost": True
    }
}


# Default voices for different languages
DEFAULT_VOICES = {
    "speaker_1": {
        "en": "uYXf8XasLslADfZ2MB4u",  # Studentin / Student voice (works for all languages)
        "de": "uYXf8XasLslADfZ2MB4u",  # Same voice - Multilingual v2 handles language automatically
        "es": "uYXf8XasLslADfZ2MB4u",
        "fr": "uYXf8XasLslADfZ2MB4u"
    },
    "speaker_2": {
        "en": "66PBrqxlmGTw9isOc21D",  # Senior Developer voice (works for all languages)
        "de": "66PBrqxlmGTw9isOc21D",  # Same voice - Multilingual v2 handles language automatically
        "es": "66PBrqxlmGTw9isOc21D",
        "fr": "66PBrqxlmGTw9isOc21D"
    }
}