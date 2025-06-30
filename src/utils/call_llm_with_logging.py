"""
Wrapper for call_llm that adds podcast logging functionality
"""
import time
from typing import Optional, Dict
from .call_llm import call_llm
from .podcast_logger import get_podcast_logger


def call_llm_with_logging(
    prompt: str, 
    node_name: str,
    cluster_info: Optional[Dict] = None,
    use_cache: bool = True,
    task_id: Optional[str] = None
) -> str:
    """
    Call LLM with automatic logging of prompt and response.
    
    Args:
        prompt: The prompt to send to the LLM
        node_name: Name of the node making the call
        cluster_info: Optional cluster information for context
        use_cache: Whether to use caching
        task_id: Task ID for logging
    
    Returns:
        The LLM response
    """
    # Get logger instance
    logger = get_podcast_logger(task_id)
    
    # Log node start if cluster info provided
    if cluster_info:
        logger.log_node_start(node_name, {
            "cluster_id": cluster_info.get("cluster_id", "N/A"),
            "cluster_title": cluster_info.get("cluster_title", "N/A")
        })
    
    # Time the LLM call
    start_time = time.time()
    
    try:
        # Make the actual LLM call
        response = call_llm(prompt, use_cache)
        
        # Calculate execution time
        execution_time = time.time() - start_time
        
        # Log the call
        logger.log_llm_call(
            node_name=node_name,
            prompt=prompt,
            response=response,
            cluster_info=cluster_info,
            execution_time=execution_time
        )
        
        return response
        
    except Exception as e:
        # Log error
        logger.log_error(node_name, e)
        raise