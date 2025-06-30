"""
Animated video renderer using Playwright's native video recording
Records CSS animations and converts to MP4 format
"""
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Dict, Optional, Tuple, List
import logging
import tempfile
import shutil
import subprocess
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)


class AnimatedVideoRenderer:
    """Render animated HTML content to MP4 videos using browser recording"""
    
    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or Path("temp/vibedoc_video_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Temp directory for WebM files before conversion
        self.webm_temp_dir = Path("temp/vibedoc_webm_temp")
        self.webm_temp_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_cache_key(self, content: str, content_type: str, duration: float, dialogue_id: str = None) -> str:
        """Generate cache key for video content"""
        data = f"{content_type}:{content}:{duration}:animated_v1"
        hash_key = hashlib.sha256(data.encode()).hexdigest()
        # If dialogue_id provided, use it as prefix (similar to audio files)
        if dialogue_id:
            return f"{dialogue_id}_{hash_key}"
        return hash_key
    
    def _get_cached_video(self, cache_key: str) -> Optional[Path]:
        """Check if video is cached"""
        cache_path = self.cache_dir / f"{cache_key}.mp4"
        if cache_path.exists():
            logger.info(f"Video cache hit for {cache_key}")
            return cache_path
        return None
    
    async def render_animated_content(
        self,
        content: str,
        content_type: str,  # "markdown" or "mermaid"
        duration_seconds: float,
        asset_id: str,
        resolution: Tuple[int, int] = (1920, 1080),
        speaker: Optional[str] = None,
        speaker_position: Optional[str] = None  # "left" or "right"
    ) -> Path:
        """
        Render content with CSS animations to MP4 video
        
        Args:
            content: Markdown or Mermaid content
            content_type: Type of content
            duration_seconds: How long to record (matches audio duration)
            asset_id: Unique identifier for logging
            resolution: Video resolution
            speaker: Speaker identifier for indicator
            speaker_position: Position of speaker indicator
            
        Returns:
            Path to MP4 video file
        """
        # Extract dialogue_id from asset_id (e.g., "dialogue_123" -> "123")
        dialogue_id = None
        if asset_id and asset_id.startswith("dialogue_"):
            dialogue_id = asset_id.replace("dialogue_", "")
        
        # Check cache first
        cache_key = self._get_cache_key(content, content_type, duration_seconds, dialogue_id)
        cached_path = self._get_cached_video(cache_key)
        if cached_path:
            return cached_path
        
        logger.info(f"Rendering animated {content_type} video for {asset_id}, duration: {duration_seconds}s")
        
        # Generate HTML based on content type
        if content_type == "markdown":
            html_content = await self._create_animated_markdown_html(
                content, resolution, duration_seconds, speaker, speaker_position
            )
        else:  # mermaid
            html_content = await self._create_animated_mermaid_html(
                content, resolution, duration_seconds, speaker, speaker_position
            )
        
        # Record video
        webm_path = await self._record_browser_video(
            html_content, duration_seconds, resolution, cache_key
        )
        
        # Convert WebM to MP4
        mp4_path = await self._convert_webm_to_mp4(webm_path, cache_key)
        
        # Clean up WebM file
        webm_path.unlink(missing_ok=True)
        
        logger.info(f"Created animated video: {mp4_path}")
        return mp4_path
    
    async def _record_browser_video(
        self,
        html_content: str,
        duration_seconds: float,
        resolution: Tuple[int, int],
        cache_key: str
    ) -> Path:
        """Record browser session to WebM video"""
        
        # Create temporary directory for this recording
        with tempfile.TemporaryDirectory() as temp_dir:
            video_dir = Path(temp_dir) / "videos"
            video_dir.mkdir()
            
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        '--disable-dev-shm-usage',
                        '--disable-blink-features=AutomationControlled'
                    ]
                )
                
                context = await browser.new_context(
                    viewport={'width': resolution[0], 'height': resolution[1]},
                    record_video_dir=str(video_dir),
                    record_video_size={'width': resolution[0], 'height': resolution[1]},
                    # Ensure animations are not disabled
                    reduced_motion='no-preference'
                )
                
                page = await context.new_page()
                
                # Set content and wait for initial load
                await page.set_content(html_content)
                await page.wait_for_load_state('networkidle')
                
                # Wait for animations to complete
                # Add 100ms at start for animation init, 500ms at end for completion
                total_wait_ms = int(duration_seconds * 1000) + 600
                await page.wait_for_timeout(total_wait_ms)
                
                # Close context to save video
                await context.close()
                await browser.close()
                
                # Find the recorded video
                video_files = list(video_dir.glob("*.webm"))
                if not video_files:
                    raise Exception("No video file was created by Playwright")
                
                # Move to temp location with hash name
                webm_path = self.webm_temp_dir / f"{cache_key}.webm"
                shutil.move(str(video_files[0]), str(webm_path))
                
                return webm_path
    
    async def _convert_webm_to_mp4(self, webm_path: Path, cache_key: str) -> Path:
        """Convert WebM to MP4 using FFmpeg"""
        mp4_path = self.cache_dir / f"{cache_key}.mp4"
        
        # FFmpeg command for high-quality conversion
        cmd = [
            'ffmpeg',
            '-i', str(webm_path),
            '-c:v', 'libx264',
            '-preset', 'fast',
            '-crf', '18',  # High quality
            '-pix_fmt', 'yuv420p',  # Compatibility
            '-movflags', '+faststart',  # Web optimization
            '-y',  # Overwrite output
            str(mp4_path)
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            raise Exception(f"FFmpeg conversion failed: {error_msg}")
        
        return mp4_path
    
    async def _create_animated_markdown_html(
        self,
        markdown_content: str,
        resolution: Tuple[int, int],
        duration_seconds: float,
        speaker: Optional[str],
        speaker_position: Optional[str]
    ) -> str:
        """Create HTML with animated markdown content and speaker indicator"""
        
        # Calculate animation timings - faster for readability
        # Use only 30% of duration for animation to allow reading
        animation_duration = duration_seconds * 0.3
        
        html_template = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                * {{
                    box-sizing: border-box;
                }}
                
                body {{
                    margin: 0;
                    padding: 0;
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;
                    background: #ffffff;
                    width: 100vw;
                    height: 100vh;
                    overflow: hidden;
                    -webkit-font-smoothing: antialiased;
                    position: relative;
                }}
                
                .content-wrapper {{
                    position: absolute;
                    top: 40px;
                    left: 40px;
                    right: 40px;
                    bottom: 40px;
                    display: flex;
                    flex-direction: column;
                    overflow-y: auto;
                    overflow-x: hidden;
                    /* Custom scrollbar for better visibility */
                    scrollbar-width: thin;
                    scrollbar-color: #0066CC #f0f0f0;
                    /* Ensure content fits */
                    max-height: calc(100vh - 80px);
                }}
                
                .content-wrapper::-webkit-scrollbar {{
                    width: 8px;
                }}
                
                .content-wrapper::-webkit-scrollbar-track {{
                    background: #f0f0f0;
                }}
                
                .content-wrapper::-webkit-scrollbar-thumb {{
                    background: #0066CC;
                    border-radius: 4px;
                }}
                
                .slide-content {{
                    width: 100%;
                    min-height: min-content;
                }}
                
                /* Typewriter effect for text */
                .typewriter-line {{
                    display: inline-block;
                    overflow: hidden;
                    white-space: nowrap;
                    max-width: 0;
                    animation: typewriter 0.5s steps(30) forwards;
                    animation-fill-mode: forwards;
                }}
                
                @keyframes typewriter {{
                    from {{ max-width: 0; }}
                    to {{ max-width: 100%; }}
                }}
                
                /* Speaker indicator styles - abstract shapes with high transparency */
                .speaker-indicator {{
                    position: absolute;
                    bottom: 40px;
                    {speaker_position}: 40px;
                    width: 80px;
                    height: 80px;
                    opacity: 0.15; /* Very transparent */
                }}
                
                /* Speaker 1 - Triangle shape */
                .speaker-1-shape {{
                    width: 0;
                    height: 0;
                    border-left: 40px solid transparent;
                    border-right: 40px solid transparent;
                    border-bottom: 70px solid #0066CC;
                    animation: float 3s ease-in-out infinite;
                }}
                
                /* Speaker 2 - Square shape */
                .speaker-2-shape {{
                    width: 70px;
                    height: 70px;
                    background: #0099CC;
                    transform: rotate(45deg);
                    animation: rotate 4s linear infinite;
                }}
                
                @keyframes float {{
                    0%, 100% {{ transform: translateY(0); }}
                    50% {{ transform: translateY(-10px); }}
                }}
                
                @keyframes rotate {{
                    from {{ transform: rotate(45deg); }}
                    to {{ transform: rotate(405deg); }}
                }}
                
                /* Typography - optimized for full screen display */
                h1, h2, h3, h4, h5, h6 {{
                    color: #0066CC;
                    margin: 0 0 12px 0;
                    font-weight: 900;
                    line-height: 1.1;
                    white-space: normal;
                    word-wrap: break-word;
                }}
                
                h1 {{
                    font-size: 42px;
                }}
                
                h2 {{
                    font-size: 32px;
                    font-weight: 800;
                }}
                
                h3 {{
                    font-size: 26px;
                    color: #004080;
                    font-weight: 700;
                }}
                
                p {{
                    font-size: 20px;
                    color: #000000;
                    line-height: 1.4;
                    margin: 0 0 10px 0;
                    font-weight: 500;
                    white-space: normal;
                    word-wrap: break-word;
                }}
                
                li {{
                    font-size: 18px;
                    color: #000000;
                    line-height: 1.4;
                    margin: 0 0 8px 0;
                    font-weight: 500;
                    white-space: normal;
                    word-wrap: break-word;
                }}
                
                ul, ol {{
                    margin: 0 0 12px 0;
                    padding-left: 25px;
                }}
                
                /* Code blocks */
                pre {{
                    background: #F8FBFF;
                    border: 2px solid #0066CC;
                    padding: 10px 12px;
                    border-radius: 8px;
                    overflow-x: auto;
                    margin: 0 0 10px 0;
                    white-space: pre-wrap;
                    word-wrap: break-word;
                }}
                
                code {{
                    font-size: 16px;
                    color: #000000;
                    font-weight: 500;
                    font-family: 'SF Mono', 'Consolas', 'Monaco', monospace;
                    line-height: 1.3;
                }}
                
                /* Ensure embedded images are visible */
                img {{
                    max-width: 100%;
                    height: auto;
                    display: block;
                    margin: 16px auto;
                }}
                
                /* Mermaid diagram container */
                .mermaid-diagram {{
                    margin: 16px 0;
                    text-align: center;
                    width: 100%;
                }}
                
                .mermaid-diagram img {{
                    max-width: 100%;
                    max-height: 70vh;
                    width: auto;
                    height: auto;
                }}
                
                /* Mermaid diagrams in markdown */
                .mermaid {{
                    margin: 12px 0;
                    text-align: center;
                    max-width: 100%;
                    overflow: auto;
                }}
                
                .mermaid svg {{
                    max-width: 100%;
                    height: auto;
                }}
                
                /* Enable typewriter effect for all text elements */
                .typewriter-container {{
                    opacity: 1;
                }}
            </style>
            <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
            <script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
        </head>
        <body>
            <div class="content-wrapper">
                <div class="slide-content typewriter-container" id="content"></div>
            </div>
            
            {speaker_html}
            
            <script>
                // Initialize mermaid
                mermaid.initialize({{ 
                    startOnLoad: false,
                    theme: 'default',
                    themeVariables: {{
                        primaryColor: '#0066CC',
                        primaryTextColor: '#000000',
                        primaryBorderColor: '#004080',
                        fontSize: '20px'
                    }},
                    flowchart: {{
                        useMaxWidth: true,
                        htmlLabels: true
                    }}
                }});
                
                // Parse and render markdown
                const markdown = `{markdown}`;
                const parsedContent = marked.parse(markdown);
                
                // Apply typewriter effect and handle mermaid
                const tempDiv = document.createElement('div');
                tempDiv.innerHTML = parsedContent;
                
                // Find and process mermaid code blocks
                tempDiv.querySelectorAll('code.language-mermaid').forEach((mermaidCode, index) => {{
                    const mermaidContent = mermaidCode.textContent;
                    const mermaidDiv = document.createElement('div');
                    mermaidDiv.className = 'mermaid';
                    mermaidDiv.id = `mermaid-${{index}}`;
                    mermaidDiv.textContent = mermaidContent;
                    
                    // Replace the pre/code block with mermaid div
                    const preElement = mermaidCode.closest('pre');
                    if (preElement) {{
                        preElement.parentNode.replaceChild(mermaidDiv, preElement);
                    }}
                }});
                
                // Process typewriter effect
                let lineIndex = 0;
                const processElement = (element) => {{
                    if (element.nodeType === Node.TEXT_NODE && element.textContent.trim()) {{
                        const span = document.createElement('span');
                        span.className = 'typewriter-line';
                        span.style.animationDelay = `${{lineIndex * 0.03}}s`; // Even faster
                        span.textContent = element.textContent;
                        element.parentNode.replaceChild(span, element);
                        lineIndex++;
                    }} else if (element.nodeType === Node.ELEMENT_NODE) {{
                        // Skip pre, code, images, and mermaid
                        if (!['PRE', 'CODE', 'IMG'].includes(element.tagName) && 
                            !element.classList.contains('mermaid') &&
                            !element.classList.contains('mermaid-diagram')) {{
                            // Process children
                            Array.from(element.childNodes).forEach(child => {{
                                processElement(child);
                            }});
                        }}
                    }}
                }};
                
                // Process all content
                Array.from(tempDiv.childNodes).forEach(child => {{
                    processElement(child);
                }});
                
                document.getElementById('content').innerHTML = tempDiv.innerHTML;
                
                // Render mermaid diagrams after content is added
                mermaid.run();
            </script>
        </body>
        </html>
        """
        
        # Speaker indicator HTML with abstract shapes
        speaker_html = ""
        if speaker and speaker_position:
            shape_class = "speaker-1-shape" if speaker == "speaker_1" else "speaker-2-shape"
            speaker_html = f"""
            <div class="speaker-indicator">
                <div class="{shape_class}"></div>
            </div>
            """
        
        # Escape markdown for JavaScript
        escaped_markdown = (markdown_content
            .replace('\\', '\\\\')
            .replace('`', '\\`')
            .replace('$', '\\$')
            .replace('\n', '\\n')
            .replace('\r', '\\r')
            .replace('"', '\\"')
        )
        
        return html_template.format(
            width=resolution[0],
            height=resolution[1],
            anim_duration=animation_duration,
            speaker_position=speaker_position or "left",
            speaker_color="#0066CC" if speaker == "speaker_1" else "#0099CC",
            speaker_html=speaker_html,
            markdown=escaped_markdown
        )
    
    async def _create_animated_mermaid_html(
        self,
        mermaid_content: str,
        resolution: Tuple[int, int],
        duration_seconds: float,
        speaker: Optional[str],
        speaker_position: Optional[str]
    ) -> str:
        """Create HTML with mermaid diagram (no animation) and speaker indicator"""
        
        html_template = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                body {{
                    margin: 0;
                    padding: 0;
                    background: #ffffff;
                    width: 100vw;
                    height: 100vh;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    position: relative;
                }}
                
                .mermaid-container {{
                    position: absolute;
                    top: 50px;
                    left: 50px;
                    right: 50px;
                    bottom: 50px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    overflow: auto;
                }}
                
                /* Speaker indicator - abstract shapes with high transparency */
                .speaker-indicator {{
                    position: absolute;
                    bottom: 40px;
                    {speaker_position}: 40px;
                    width: 80px;
                    height: 80px;
                    opacity: 0.15;
                }}
                
                /* Speaker 1 - Triangle shape */
                .speaker-1-shape {{
                    width: 0;
                    height: 0;
                    border-left: 40px solid transparent;
                    border-right: 40px solid transparent;
                    border-bottom: 70px solid #0066CC;
                    animation: float 3s ease-in-out infinite;
                }}
                
                /* Speaker 2 - Square shape */
                .speaker-2-shape {{
                    width: 70px;
                    height: 70px;
                    background: #0099CC;
                    transform: rotate(45deg);
                    animation: rotate 4s linear infinite;
                }}
                
                @keyframes float {{
                    0%, 100% {{ transform: translateY(0); }}
                    50% {{ transform: translateY(-10px); }}
                }}
                
                @keyframes rotate {{
                    from {{ transform: rotate(45deg); }}
                    to {{ transform: rotate(405deg); }}
                }}
                
                /* Mermaid specific styles */
                .mermaid {{
                    font-size: 24px;
                    width: 100%;
                    height: 100%;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                }}
                
                /* Force mermaid SVG to scale */
                .mermaid svg {{
                    max-width: 100%;
                    max-height: 100%;
                    width: auto;
                    height: auto;
                }}
            </style>
            <script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
        </head>
        <body>
            <div class="mermaid-container">
                <div class="mermaid" id="mermaid-diagram">
                    {mermaid_content}
                </div>
            </div>
            
            {speaker_html}
            
            <script>
                mermaid.initialize({{ 
                    startOnLoad: true,
                    theme: 'default',
                    themeVariables: {{
                        primaryColor: '#0066CC',
                        primaryTextColor: '#000000',
                        primaryBorderColor: '#004080',
                        fontSize: '32px',
                        fontFamily: '-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Arial, sans-serif'
                    }},
                    flowchart: {{
                        useMaxWidth: true,
                        htmlLabels: true,
                        rankSpacing: 80,
                        nodeSpacing: 80,
                        curve: 'basis'
                    }},
                    securityLevel: 'loose',
                    maxTextSize: 100000
                }});
                
                // After mermaid renders, ensure it fills the container
                window.addEventListener('load', () => {{
                    const svg = document.querySelector('.mermaid svg');
                    if (svg) {{
                        // Remove any fixed width/height attributes
                        svg.removeAttribute('width');
                        svg.removeAttribute('height');
                        svg.style.width = '100%';
                        svg.style.height = '100%';
                    }}
                }});
            </script>
        </body>
        </html>
        """
        
        # Speaker indicator HTML with abstract shapes
        speaker_html = ""
        if speaker and speaker_position:
            shape_class = "speaker-1-shape" if speaker == "speaker_1" else "speaker-2-shape"
            speaker_html = f"""
            <div class="speaker-indicator">
                <div class="{shape_class}"></div>
            </div>
            """
        
        return html_template.format(
            width=resolution[0],
            height=resolution[1],
            speaker_position=speaker_position or "left",
            speaker_html=speaker_html,
            mermaid_content=mermaid_content
        )