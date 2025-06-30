"""
Asset renderer for converting Markdown and Mermaid to images
"""
import asyncio
import tempfile
import hashlib
import json
import re
import base64
from pathlib import Path
from typing import Tuple, Optional, List, Dict
import logging

from playwright.async_api import async_playwright
from PIL import Image
import subprocess

logger = logging.getLogger(__name__)


class AssetRenderer:
    """Renders Markdown and Mermaid content to images"""
    
    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or Path("temp/vibedoc_asset_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.render_dir = Path("temp/vibedoc_rendered_assets")
        self.render_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_cache_key(self, content: str, content_type: str, resolution: Tuple[int, int]) -> str:
        """Generate cache key for content"""
        # Include 4K output flag in cache key to invalidate old cache
        data = f"{content_type}:{content}:{resolution[0]}x{resolution[1]}:4K_v2"
        return hashlib.sha256(data.encode()).hexdigest()
    
    def _get_cached_path(self, cache_key: str) -> Optional[Path]:
        """Check if asset is cached"""
        cache_path = self.cache_dir / f"{cache_key}.png"
        if cache_path.exists():
            logger.info(f"Cache hit for {cache_key}")
            return cache_path
        return None
    
    async def render_mermaid(self, mermaid_code: str, asset_id: str, resolution: Tuple[int, int], scale: float = 1.0) -> Path:
        """Render Mermaid diagram using mermaid-cli"""
        # Check cache first
        cache_key = self._get_cache_key(mermaid_code + str(scale), "mermaid", resolution)
        cached_path = self._get_cached_path(cache_key)
        if cached_path:
            return cached_path
        
        logger.info(f"Rendering Mermaid diagram {asset_id} at resolution {resolution} with scale {scale}")
        
        # Create temp file for mermaid code
        with tempfile.NamedTemporaryFile(mode='w', suffix='.mmd', delete=False) as f:
            f.write(mermaid_code)
            mermaid_file = f.name
        
        output_path = self.cache_dir / f"{cache_key}.png"
        
        try:
            # Configure mermaid with professional theme
            config = {
                "theme": "base",
                "flowchart": {
                    "useMaxWidth": True,  # Use maximum available width
                    "htmlLabels": True,
                    "curve": "basis"
                },
                "themeVariables": {
                    "primaryColor": "#0066CC",
                    "primaryTextColor": "#000000",
                    "primaryBorderColor": "#004080",
                    "lineColor": "#333333",
                    "secondaryColor": "#E6F2FF",
                    "tertiaryColor": "#F0F8FF",
                    "background": "#FFFFFF",
                    "mainBkg": "#E6F2FF",
                    "secondBkg": "#F0F8FF",
                    "tertiaryBkg": "#FFFFFF",
                    "fontSize": "24px",  # Larger font for better readability
                    "fontFamily": "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif",
                    "nodeTextColor": "#000000",
                    "edgeLabelBackground": "#FFFFFF"
                }
            }
            
            config_file = None
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                json.dump(config, f)
                config_file = f.name
            
            # Run mermaid-cli - render at higher resolution for 4K scaling
            # For embedded diagrams in markdown, use smaller scale
            if scale == 1.0:  # Standalone mermaid diagram
                # Render larger for better quality when scaling to 4K
                # Account for 50px padding on each side
                width = int(3740)  # 3840 - 100 (50px padding each side)
                height = int(2060)  # 2160 - 100 (50px padding each side)
            else:  # Embedded in markdown
                width = int(resolution[0] * 0.8 * scale)
                height = int(resolution[1] * 0.7 * scale)
            
            cmd = [
                'mmdc',
                '-i', mermaid_file,
                '-o', str(output_path),
                '-w', str(width),
                '-H', str(height),
                '--backgroundColor', 'white',
                '--configFile', config_file
            ]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                error_msg = stderr.decode() if stderr else "Unknown error"
                logger.error(f"Mermaid rendering failed: {error_msg}")
                # Fall back to text rendering
                return await self.render_markdown(
                    f"```mermaid\n{mermaid_code}\n```\n\n*Error rendering diagram*",
                    asset_id,
                    resolution
                )
            
            # Post-process: ensure correct size and add padding
            await self._post_process_image(output_path, resolution)
            
            return output_path
            
        finally:
            # Clean up temp files
            Path(mermaid_file).unlink(missing_ok=True)
            if config_file:
                Path(config_file).unlink(missing_ok=True)
    
    async def _extract_and_render_mermaid_blocks(self, markdown_content: str, resolution: Tuple[int, int]) -> Dict[str, str]:
        """Extract Mermaid blocks from markdown and render them to base64 images"""
        mermaid_pattern = r'```mermaid\n(.*?)\n```'
        mermaid_blocks = re.findall(mermaid_pattern, markdown_content, re.DOTALL)
        
        rendered_images = {}
        
        for i, mermaid_code in enumerate(mermaid_blocks):
            try:
                # Render the mermaid diagram at smaller scale for embedding
                temp_id = f"embedded_mermaid_{i}"
                # Use smaller resolution for embedded diagrams
                embed_resolution = (int(resolution[0] * 0.8), int(resolution[1] * 0.8))
                image_path = await self.render_mermaid(mermaid_code, temp_id, embed_resolution, scale=0.8)
                
                # Convert to base64
                with open(image_path, 'rb') as img_file:
                    base64_image = base64.b64encode(img_file.read()).decode('utf-8')
                    rendered_images[mermaid_code] = f"data:image/png;base64,{base64_image}"
                    
            except Exception as e:
                logger.warning(f"Failed to render embedded Mermaid diagram {i}: {e}")
                # Keep the original code block on failure
                rendered_images[mermaid_code] = None
        
        return rendered_images
    
    async def _preprocess_markdown_with_mermaid(self, markdown_content: str, resolution: Tuple[int, int]) -> str:
        """Preprocess markdown to replace Mermaid blocks with rendered images"""
        # Extract and render all Mermaid blocks
        rendered_images = await self._extract_and_render_mermaid_blocks(markdown_content, resolution)
        
        # Replace Mermaid blocks with images or styled code blocks
        processed_content = markdown_content
        
        for mermaid_code, base64_image in rendered_images.items():
            original_block = f"```mermaid\n{mermaid_code}\n```"
            
            if base64_image:
                # Replace with image
                image_html = f'<div class="mermaid-diagram"><img src="{base64_image}" alt="Mermaid Diagram" /></div>'
                processed_content = processed_content.replace(original_block, image_html)
            else:
                # Style as code block with error message
                error_html = f'<div class="mermaid-error"><pre><code class="language-mermaid">{mermaid_code}</code></pre><p class="error-message">⚠️ Diagram rendering failed</p></div>'
                processed_content = processed_content.replace(original_block, error_html)
        
        return processed_content
    
    async def render_markdown(self, markdown_content: str, asset_id: str, resolution: Tuple[int, int]) -> Path:
        """Render Markdown using Playwright with embedded Mermaid support"""
        # Check cache first
        cache_key = self._get_cache_key(markdown_content, "markdown", resolution)
        cached_path = self._get_cached_path(cache_key)
        if cached_path:
            return cached_path
        
        logger.info(f"Rendering Markdown content {asset_id}")
        
        # Preprocess markdown to handle Mermaid blocks
        processed_markdown = await self._preprocess_markdown_with_mermaid(markdown_content, resolution)
        
        output_path = self.cache_dir / f"{cache_key}.png"
        
        # HTML template with professional styling and animations
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
                    padding: 50px;  /* 50px padding as requested */
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
                    background: #ffffff;
                    width: {width}px;
                    height: {height}px;
                    display: flex;
                    align-items: flex-start;
                    justify-content: center;
                    overflow: hidden;
                    -webkit-font-smoothing: antialiased;
                    -moz-osx-font-smoothing: grayscale;
                }}
                
                .slide-content {{
                    width: 100%;
                    height: 100%;
                    display: flex;
                    flex-direction: column;
                    justify-content: center;
                    overflow: hidden;
                    animation: slideIn 0.8s ease-out;
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
                
                .content {{
                    max-width: 100%;
                    max-height: 100%;
                    overflow: hidden;
                }}
                
                h1 {{
                    color: #0066CC;
                    font-size: 72px;  /* Larger for YouTube readability */
                    margin: 0 0 40px 0;
                    font-weight: 900;
                    line-height: 1.1;
                    letter-spacing: -0.02em;
                    animation: fadeInScale 1s ease-out;
                    text-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
                }}
                
                h2 {{
                    color: #0066CC;
                    font-size: 54px;  /* Larger for YouTube */
                    margin: 32px 0 24px 0;
                    font-weight: 800;
                    line-height: 1.2;
                    animation: fadeInScale 1s ease-out 0.2s both;
                    text-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
                }}
                
                h3 {{
                    color: #004080;
                    font-size: 42px;  /* Larger for YouTube */
                    margin: 24px 0 20px 0;
                    font-weight: 700;
                    line-height: 1.3;
                    animation: fadeInScale 1s ease-out 0.3s both;
                }}
                
                h4 {{
                    color: #003366;
                    font-size: 26px;
                    margin: 16px 0 12px 0;
                    font-weight: 600;
                    animation: fadeInScale 1s ease-out 0.4s both;
                }}
                
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
                
                p {{
                    line-height: 1.8;
                    color: #000000;
                    font-size: 32px;  /* Much larger for YouTube */
                    margin: 0 0 28px 0;
                    font-weight: 500;  /* Slightly bolder */
                    animation: fadeInUp 0.8s ease-out 0.5s both;
                }}
                
                ul, ol {{
                    margin: 0 0 24px 0;
                    padding-left: 40px;
                }}
                
                li {{
                    line-height: 1.8;
                    color: #000000;
                    font-size: 30px;  /* Larger for YouTube */
                    margin: 0 0 16px 0;
                    font-weight: 500;
                    animation: fadeInLeft 0.6s ease-out both;
                }}
                
                li:nth-child(1) {{ animation-delay: 0.6s; }}
                li:nth-child(2) {{ animation-delay: 0.7s; }}
                li:nth-child(3) {{ animation-delay: 0.8s; }}
                li:nth-child(4) {{ animation-delay: 0.9s; }}
                li:nth-child(5) {{ animation-delay: 1.0s; }}
                li:nth-child(6) {{ animation-delay: 1.1s; }}
                
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
                
                @keyframes fadeInLeft {{
                    from {{
                        opacity: 0;
                        transform: translateX(-20px);
                    }}
                    to {{
                        opacity: 1;
                        transform: translateX(0);
                    }}
                }}
                
                pre {{
                    background: #F8FBFF;
                    border: 3px solid #0066CC;
                    padding: 32px;
                    border-radius: 16px;
                    overflow-x: auto;
                    margin: 0 0 32px 0;
                    box-shadow: 0 6px 12px rgba(0, 102, 204, 0.15);
                    animation: fadeInScale 0.8s ease-out 0.6s both;
                }}
                
                code {{
                    background: #E6F2FF;
                    padding: 3px 8px;
                    border-radius: 6px;
                    font-family: 'SF Mono', Monaco, Consolas, 'Courier New', monospace;
                    font-size: 18px;
                    color: #0066CC;
                    font-weight: 600;
                }}
                
                pre code {{
                    background: none;
                    padding: 0;
                    color: #000000;
                    font-size: 26px;  /* Larger code for YouTube */
                    line-height: 1.7;
                    font-weight: 500;  /* Slightly bolder */
                    font-family: 'SF Mono', 'Consolas', 'Monaco', monospace;
                }}
                
                blockquote {{
                    border-left: 6px solid #0066CC;
                    padding: 20px 20px 20px 30px;
                    margin: 0 0 24px 0;
                    color: #003366;
                    font-style: italic;
                    background: #F0F8FF;
                    border-radius: 0 8px 8px 0;
                    font-size: 22px;
                    animation: fadeInLeft 0.8s ease-out 0.7s both;
                }}
                
                table {{
                    border-collapse: collapse;
                    width: 100%;
                    margin: 0 0 16px 0;
                }}
                
                th, td {{
                    border: 1px solid #e2e8f0;
                    padding: 12px;
                    text-align: left;
                }}
                
                th {{
                    background: #f7fafc;
                    font-weight: 600;
                    color: #2d3748;
                }}
                
                img {{
                    max-width: 100%;
                    height: auto;
                    display: block;
                    margin: 0 auto;
                }}
                
                .highlight {{
                    background-color: #fef3c7;
                    padding: 2px 4px;
                    border-radius: 2px;
                }}
                
                /* Enhanced syntax highlighting */
                .language-python .token.keyword,
                .language-javascript .token.keyword,
                .language-typescript .token.keyword {{
                    color: #0066CC;
                    font-weight: bold;
                }}
                
                .language-python .token.string,
                .language-javascript .token.string,
                .language-typescript .token.string {{
                    color: #008000;
                }}
                
                .language-python .token.function,
                .language-javascript .token.function,
                .language-typescript .token.function {{
                    color: #CC0066;
                    font-weight: 600;
                }}
                
                .language-python .token.comment,
                .language-javascript .token.comment,
                .language-typescript .token.comment {{
                    color: #666666;
                    font-style: italic;
                }}
                
                .language-python .token.number,
                .language-javascript .token.number,
                .language-typescript .token.number {{
                    color: #CC6600;
                }}
                
                /* Special styling for strong emphasis */
                strong {{
                    color: #0066CC;
                    font-weight: 800;
                    font-size: 1.05em;
                }}
                
                /* Highlight important text */
                .highlight {{
                    background-color: #FFE666;
                    padding: 4px 8px;
                    border-radius: 4px;
                    font-weight: 600;
                    animation: pulse 2s ease-in-out infinite;
                }}
                
                @keyframes pulse {{
                    0%, 100% {{ background-color: #FFE666; }}
                    50% {{ background-color: #FFD700; }}
                }}
                
                /* Mermaid diagram styles */
                .mermaid-diagram {{
                    margin: 24px 0;
                    text-align: center;
                    animation: fadeInScale 0.8s ease-out 0.6s both;
                }}
                
                .mermaid-diagram img {{
                    max-width: 90%;
                    max-height: 80%;
                    border: 2px solid #E6F2FF;
                    border-radius: 12px;
                    box-shadow: 0 4px 8px rgba(0, 102, 204, 0.1);
                }}
                
                .mermaid-error {{
                    margin: 24px 0;
                    background: #FFF0F0;
                    border: 2px solid #FFCCCC;
                    border-radius: 12px;
                    padding: 16px;
                }}
                
                .mermaid-error pre {{
                    background: #FFFFFF;
                    border: 1px solid #FFDDDD;
                    margin-bottom: 12px;
                }}
                
                .mermaid-error .error-message {{
                    color: #CC0000;
                    font-size: 16px;
                    margin: 0;
                    font-weight: 600;
                }}
            </style>
            <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/themes/prism.min.css">
            <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
            <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/prism.min.js"></script>
            <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/prism-python.min.js"></script>
            <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/prism-javascript.min.js"></script>
            <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/prism-typescript.min.js"></script>
        </head>
        <body>
            <div class="slide-content">
                <div class="content" id="content"></div>
            </div>
            <script>
                const content = `{markdown}`;
                document.getElementById('content').innerHTML = marked.parse(content);
                Prism.highlightAll();
            </script>
        </body>
        </html>
        """
        
        # Escape markdown for JavaScript (but keep HTML intact)
        escaped_markdown = (processed_markdown
            .replace('\\', '\\\\')
            .replace('`', '\\`')
            .replace('$', '\\$')
            .replace('\n', '\\n')
            .replace('\r', '\\r')
            .replace('"', '\\"')
        )
        
        # Use render dimensions instead of target resolution
        html_content = html_template.format(
            width=3000,  # Match viewport width
            height=1800,  # Match viewport height
            markdown=escaped_markdown
        )
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                args=['--no-sandbox', '--disable-setuid-sandbox']
            )
            # Render at higher resolution for 4K output
            render_width = 3000  # Render larger
            render_height = 1800  # Will be scaled to 4K
            page = await browser.new_page(
                viewport={'width': render_width, 'height': render_height},
                device_scale_factor=1.5  # Good quality without being too slow
            )
            
            await page.set_content(html_content)
            await page.wait_for_load_state('networkidle')
            await page.wait_for_timeout(500)  # Wait for Prism highlighting
            
            await page.screenshot(path=str(output_path), full_page=False)
            await browser.close()
        
        # Post-process to ensure 4K output
        await self._post_process_image(output_path, resolution)
        
        return output_path
    
    async def _post_process_image(self, image_path: Path, target_resolution: Tuple[int, int]):
        """Post-process image to ensure 4K output (3840x2160)"""
        img = Image.open(image_path)
        
        # Force 4K resolution for all images
        final_resolution = (3840, 2160)
        
        # Create new image with 4K resolution and white background
        new_img = Image.new('RGBA', final_resolution, (255, 255, 255, 255))
        
        # Calculate scale to maximize image size while maintaining aspect ratio
        # Add small padding for visual comfort
        padding = 100  # 50px on each side (as requested)
        scale_w = (final_resolution[0] - padding) / img.width
        scale_h = (final_resolution[1] - padding) / img.height
        
        # Use the smaller scale to ensure the image fits
        scale = min(scale_w, scale_h)
        
        # Always scale up to fill 4K screen
        new_width = int(img.width * scale)
        new_height = int(img.height * scale)
        
        # High-quality resize using LANCZOS
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        
        # Center the image in 4K frame
        x = (final_resolution[0] - new_width) // 2
        y = (final_resolution[1] - new_height) // 2
        
        # Paste the scaled image centered
        if img.mode == 'RGBA':
            new_img.paste(img, (x, y), img)
        else:
            new_img.paste(img, (x, y))
        
        # Save with high quality
        new_img.save(image_path, 'PNG', optimize=True, quality=95)
        
        # Log scaling info
        logger.info(f"Scaled to 4K: {image_path.name} - Original: {img.size}, Scaled content: ({new_width}x{new_height}), Final: {final_resolution}")
    
    async def render_title_slide(self, title: str, subtitle: str, resolution: Tuple[int, int]) -> Path:
        """Render a simple title slide"""
        markdown_content = f"# {title}\n\n## {subtitle}"
        return await self.render_markdown(markdown_content, "title_slide", resolution)