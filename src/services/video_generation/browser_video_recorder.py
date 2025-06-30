"""
Browser-based video recording for CSS animations using Playwright
"""
import asyncio
from pathlib import Path
from typing import Dict, Optional, Tuple
import logging
from playwright.async_api import async_playwright
import tempfile
import shutil

logger = logging.getLogger(__name__)


class BrowserVideoRecorder:
    """Record browser sessions with CSS animations using Playwright's native recording"""
    
    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or Path("temp/vibedoc_browser_recordings")
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    async def record_markdown_with_animations(
        self,
        html_content: str,
        duration_seconds: float,
        resolution: Tuple[int, int] = (1920, 1080),
        output_path: Optional[Path] = None
    ) -> Path:
        """
        Record HTML content with CSS animations to video
        
        Args:
            html_content: Full HTML with CSS animations
            duration_seconds: How long to record (matches audio duration)
            resolution: Video resolution
            output_path: Where to save the video
            
        Returns:
            Path to the recorded video
        """
        # Create temporary directory for recording
        with tempfile.TemporaryDirectory() as temp_dir:
            video_dir = Path(temp_dir) / "videos"
            video_dir.mkdir()
            
            async with async_playwright() as p:
                # Launch browser with video recording enabled
                browser = await p.chromium.launch(
                    headless=True,  # Can be True for production
                    args=['--no-sandbox', '--disable-setuid-sandbox']
                )
                
                context = await browser.new_context(
                    viewport={'width': resolution[0], 'height': resolution[1]},
                    record_video_dir=str(video_dir),
                    record_video_size={'width': resolution[0], 'height': resolution[1]}
                )
                
                page = await context.new_page()
                
                # Set content and wait for animations to start
                await page.set_content(html_content)
                await page.wait_for_load_state('networkidle')
                
                # Record for the specified duration
                # Add small buffer for animation completion
                await page.wait_for_timeout(int(duration_seconds * 1000) + 500)
                
                # Close to save video
                await context.close()
                await browser.close()
                
                # Find the recorded video
                video_files = list(video_dir.glob("*.webm"))
                if not video_files:
                    raise Exception("No video file was created")
                
                # Move to output location
                if not output_path:
                    output_path = self.output_dir / f"animation_{hash(html_content)}.webm"
                
                shutil.move(str(video_files[0]), str(output_path))
                logger.info(f"Recorded animation video to {output_path}")
                
                return output_path
    
    async def create_animated_slide_html(
        self,
        markdown_content: str,
        title: str,
        resolution: Tuple[int, int]
    ) -> str:
        """
        Create HTML with CSS animations optimized for video recording
        """
        html_template = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                * {{
                    box-sizing: border-box;
                }}
                
                body {{
                    margin: 0;
                    padding: 60px 80px;
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;
                    background: #ffffff;
                    width: {width}px;
                    height: {height}px;
                    overflow: hidden;
                    -webkit-font-smoothing: antialiased;
                }}
                
                .slide-content {{
                    width: 100%;
                    height: 100%;
                    display: flex;
                    flex-direction: column;
                    justify-content: center;
                    animation: none; /* Start with no animation */
                }}
                
                .slide-content.animate {{
                    animation: slideIn 0.8s ease-out forwards;
                }}
                
                @keyframes slideIn {{
                    from {{
                        opacity: 0;
                        transform: translateY(30px);
                    }}
                    to {{
                        opacity: 1;
                        transform: translateY(0);
                    }}
                }}
                
                h1 {{
                    opacity: 0;
                    animation: fadeInScale 1s ease-out 0.2s forwards;
                }}
                
                h2 {{
                    opacity: 0;
                    animation: fadeInScale 1s ease-out 0.4s forwards;
                }}
                
                p, li {{
                    opacity: 0;
                    animation: fadeInUp 0.8s ease-out 0.6s forwards;
                }}
                
                li:nth-child(1) {{ animation-delay: 0.6s; }}
                li:nth-child(2) {{ animation-delay: 0.7s; }}
                li:nth-child(3) {{ animation-delay: 0.8s; }}
                li:nth-child(4) {{ animation-delay: 0.9s; }}
                li:nth-child(5) {{ animation-delay: 1.0s; }}
                
                @keyframes fadeInScale {{
                    from {{
                        opacity: 0;
                        transform: scale(0.95);
                    }}
                    to {{
                        opacity: 1;
                        transform: scale(1);
                    }}
                }}
                
                @keyframes fadeInUp {{
                    from {{
                        opacity: 0;
                        transform: translateY(20px);
                    }}
                    to {{
                        opacity: 1;
                        transform: translateY(0);
                    }}
                }}
                
                /* Styles from existing template... */
                {styles}
            </style>
            <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
        </head>
        <body>
            <div class="slide-content" id="content"></div>
            <script>
                // Parse markdown
                const markdown = `{markdown}`;
                document.getElementById('content').innerHTML = marked.parse(markdown);
                
                // Trigger animations after a brief delay
                setTimeout(() => {{
                    document.getElementById('content').classList.add('animate');
                }}, 100);
            </script>
        </body>
        </html>
        """
        
        # Include existing styles (truncated for brevity)
        existing_styles = """
            h1 { font-size: 72px; color: #0066CC; }
            h2 { font-size: 54px; color: #0066CC; }
            /* ... rest of styles ... */
        """
        
        return html_template.format(
            width=resolution[0],
            height=resolution[1],
            markdown=markdown_content.replace('`', '\\`').replace('$', '\\$'),
            styles=existing_styles
        )