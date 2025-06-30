from pocketflow import Node
from typing import Dict, List, Tuple
import os
import re


class ParseTutorialV2(Node):
    """
    Parses tutorial markdown files and creates cluster structure.
    Each markdown file becomes one cluster.
    """
    
    def prep(self, shared: Dict) -> str:
        """Get tutorial path from shared context."""
        return shared.get("tutorial_path", "")
    
    def exec(self, tutorial_path: str) -> List[Dict]:
        """Parse all markdown files and create clusters."""
        if not os.path.exists(tutorial_path):
            raise ValueError(f"Tutorial path does not exist: {tutorial_path}")
        
        # Find all markdown files
        md_files = []
        for file in os.listdir(tutorial_path):
            if file.endswith('.md'):
                md_files.append(file)
        
        # Sort files: index.md first, then numbered files
        def sort_key(filename):
            if filename == 'index.md':
                return (0, '')
            # Extract number from filename like "01_flow.md"
            match = re.match(r'(\d+)', filename)
            if match:
                return (1, int(match.group(1)))
            return (2, filename)
        
        md_files.sort(key=sort_key)
        
        # Create clusters
        clusters = []
        for i, filename in enumerate(md_files):
            filepath = os.path.join(tutorial_path, filename)
            
            # Read file content
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Extract title from filename or content
            if filename == 'index.md':
                title = "Introduction"
                cluster_id = "index"
            else:
                # Remove number prefix and .md extension
                title_match = re.match(r'\d*_?(.+)\.md', filename)
                if title_match:
                    title = title_match.group(1).replace('_', ' ').title()
                else:
                    title = filename.replace('.md', '').replace('_', ' ').title()
                cluster_id = filename.replace('.md', '')
            
            # Extract existing mermaid diagrams
            mermaid_pattern = r'```mermaid\n(.*?)\n```'
            existing_diagrams = re.findall(mermaid_pattern, content, re.DOTALL)
            
            # Determine next cluster for transitions
            next_cluster_title = None
            if i < len(md_files) - 1:
                next_file = md_files[i + 1]
                next_match = re.match(r'\d*_?(.+)\.md', next_file)
                if next_match:
                    next_cluster_title = next_match.group(1).replace('_', ' ').title()
            
            cluster = {
                "cluster_id": cluster_id,
                "title": title,
                "content": content,
                "is_first": filename == 'index.md',
                "has_mermaid": len(existing_diagrams) > 0,
                "existing_diagrams": existing_diagrams,
                "prev_title": clusters[-1]["title"] if clusters else None,
                "next_cluster_title": next_cluster_title
            }
            
            clusters.append(cluster)
        
        return clusters
    
    def post(self, shared: Dict, prep_res: str, exec_res: List[Dict]) -> str:
        """Store clusters in shared context."""
        shared["clusters"] = exec_res
        shared["cluster_count"] = len(exec_res)
        
        # Log progress
        progress_callback = shared.get("progress_callback")
        if progress_callback and callable(progress_callback):
            progress_callback(
                "parse_tutorial_v2",
                f"Parsed {len(exec_res)} markdown files into clusters"
            )
        
        return "default"