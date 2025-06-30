import os
import shutil
import subprocess
import tempfile
from typing import Optional


def clone_repository(repo_url: str, branch: str = None, depth: int = 1) -> str:
    """
    Clone a git repository to a temporary directory.
    
    Args:
        repo_url: The git repository URL (supports http/https/git protocols)
        branch: Specific branch to clone (optional)
        depth: Clone depth (1 for shallow clone without history)
        
    Returns:
        The path to the cloned repository
        
    Raises:
        subprocess.CalledProcessError: If git clone fails
    """
    # Create a temporary directory
    temp_dir = tempfile.mkdtemp(prefix="vibedoc_repo_")
    
    # Build git clone command
    cmd = ["git", "clone", "--depth", str(depth)]
    
    if branch:
        cmd.extend(["--branch", branch])
    
    cmd.extend([repo_url, temp_dir])
    
    try:
        # Execute git clone
        print(f"Cloning repository: {repo_url}")
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        print(f"Repository cloned to: {temp_dir}")
        return temp_dir
    except subprocess.CalledProcessError as e:
        # Clean up on failure
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        raise RuntimeError(f"Failed to clone repository: {e.stderr}")


def cleanup_temp_repo(temp_dir: str) -> None:
    """
    Remove a temporary repository directory.
    
    Args:
        temp_dir: The temporary directory to remove
    """
    if temp_dir and os.path.exists(temp_dir) and temp_dir.startswith(tempfile.gettempdir()):
        try:
            shutil.rmtree(temp_dir)
            print(f"Cleaned up temporary repository: {temp_dir}")
        except Exception as e:
            print(f"Warning: Failed to clean up {temp_dir}: {e}")