"""
Podcast generation logger with beautiful formatting for LLM prompts and responses
"""
import os
import json
from datetime import datetime
from typing import Dict, Any, Optional
import textwrap


class PodcastLogger:
    """
    Logger for podcast generation workflow.
    Captures all LLM prompts, responses, and cluster information.
    """
    
    def __init__(self, log_dir: str = "logs", task_id: str = None):
        self.log_dir = log_dir
        self.task_id = task_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = os.path.join(log_dir, f"podcast_{self.task_id}.log")
        self.call_counter = 0
        
        # Create log directory if it doesn't exist
        os.makedirs(log_dir, exist_ok=True)
        
        # Initialize log file with header
        self._init_log_file()
    
    def _init_log_file(self):
        """Initialize the log file with a beautiful header."""
        header = f"""
╔════════════════════════════════════════════════════════════════════════════════╗
                          PODCAST GENERATION LOG                                  
                                                                                  
   Task ID: {self.task_id:<68}  
   Started: {datetime.now().strftime("%Y-%m-%d %H:%M:%S"):<68}  
╚════════════════════════════════════════════════════════════════════════════════╝

"""
        with open(self.log_file, 'w', encoding='utf-8') as f:
            f.write(header)
    
    def log_node_start(self, node_name: str, details: Dict[str, Any] = None):
        """Log the start of a node execution."""
        self.call_counter += 1
        
        log_entry = f"""
┌─────────────────────────────────────────────────────────────────────────────────
│ NODE: {node_name}
│ Time: {datetime.now().strftime("%H:%M:%S")}
│ Call #: {self.call_counter}
"""
        
        if details:
            for key, value in details.items():
                if isinstance(value, (list, dict)):
                    value_str = json.dumps(value, indent=2, ensure_ascii=False)
                    value_lines = value_str.split('\n')
                    log_entry += f"│ {key}:\n"
                    for line in value_lines:
                        log_entry += f"│   {line}\n"
                else:
                    log_entry += f"│ {key}: {value}\n"
        
        log_entry += "└─────────────────────────────────────────────────────────────────────────────────\n"
        
        self._append_to_log(log_entry)
    
    def log_llm_call(self, node_name: str, prompt: str, response: str, 
                     cluster_info: Optional[Dict] = None, execution_time: float = 0):
        """Log an LLM call with prompt and response."""
        
        # Format cluster info if provided
        cluster_section = ""
        if cluster_info:
            cluster_section = f"""
┌─ CLUSTER INFO ──────────────────────────────────────────────────────────────────
│ Cluster ID: {cluster_info.get('cluster_id', 'N/A')}
│ Title: {cluster_info.get('cluster_title', 'N/A')}
│ Topics: {', '.join(cluster_info.get('topics', [])[:3])}
└─────────────────────────────────────────────────────────────────────────────────
"""
        
        # Format the prompt
        prompt_lines = prompt.split('\n')
        formatted_prompt = ""
        for line in prompt_lines:
            wrapped = textwrap.wrap(line, width=78) if line else ['']
            for wrapped_line in wrapped:
                formatted_prompt += f"  {wrapped_line:<78}  \n"
        
        # Format the response
        response_lines = response.split('\n')
        formatted_response = ""
        for line in response_lines:
            wrapped = textwrap.wrap(line, width=78) if line else ['']
            for wrapped_line in wrapped:
                formatted_response += f"  {wrapped_line:<78}  \n"
        
        log_entry = f"""
{'═' * 82}
🤖 LLM CALL #{self.call_counter} - {node_name}
{'═' * 82}
{cluster_section}
┌─ PROMPT ────────────────────────────────────────────────────────────────────────
  Length: {len(prompt)} characters | Execution: {execution_time:.2f}s
╟─────────────────────────────────────────────────────────────────────────────────
{formatted_prompt}└─────────────────────────────────────────────────────────────────────────────────

┌─ RESPONSE ──────────────────────────────────────────────────────────────────────
  Length: {len(response)} characters
╟─────────────────────────────────────────────────────────────────────────────────
{formatted_response}└─────────────────────────────────────────────────────────────────────────────────

"""
        
        self._append_to_log(log_entry)
    
    def log_cluster_summary(self, clusters: list):
        """Log a summary of all clusters."""
        summary = f"""
╔════════════════════════════════════════════════════════════════════════════════╗
                               CLUSTER SUMMARY                                     
╚════════════════════════════════════════════════════════════════════════════════╝

Total Clusters: {len(clusters)}

"""
        
        for i, cluster in enumerate(clusters, 1):
            summary += f"""
┌─ Cluster {i}/{len(clusters)} ────────────────────────────────────────────────────────────
│ ID: {cluster.get('cluster_id', 'N/A')}
│ Title: {cluster.get('cluster_title', 'N/A')}
│ McKinsey Summary: {cluster.get('mckinsey_summary', 'N/A')[:60]}...
│ Dialogue Count: {len(cluster.get('dialogues', []))}
│ Topics: {', '.join(cluster.get('topics', [])[:5])}
└─────────────────────────────────────────────────────────────────────────────────
"""
        
        self._append_to_log(summary)
    
    def log_visualization_summary(self, cluster_id: str, visualizations: list):
        """Log visualization details for a cluster."""
        viz_summary = f"""
┌─ VISUALIZATIONS for {cluster_id} ───────────────────────────────────────────────
│ Total: {len(visualizations)} visualizations
│"""
        
        for i, viz in enumerate(visualizations):
            viz_type = viz.get('type', 'unknown')
            duration = viz.get('duration', 1)
            content_preview = viz.get('content', '')[:50].replace('\n', ' ')
            
            viz_summary += f"""
│ [{i+1}] Type: {viz_type} | Duration: {duration} dialogue(s)
│     Preview: {content_preview}...
│"""
        
        viz_summary += "\n└─────────────────────────────────────────────────────────────────────────────────\n"
        
        self._append_to_log(viz_summary)
    
    def log_error(self, node_name: str, message: str):
        """Log an error message."""
        error_log = f"""
╔════════════════════════════════════════════════════════════════════════════════╗
                                     ERROR                                         
╚════════════════════════════════════════════════════════════════════════════════╝
Node: {node_name}
Time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
Message: {message}
{'─' * 82}

"""
        self._append_to_log(error_log)
    
    def log_warning(self, node_name: str, message: str):
        """Log a warning message."""
        warning_log = f"""
╔════════════════════════════════════════════════════════════════════════════════╗
                                    WARNING                                        
╚════════════════════════════════════════════════════════════════════════════════╝
Node: {node_name}
Time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
Message: {message}
{'─' * 82}

"""
        self._append_to_log(warning_log)
    
    def log_task_completion(self, total_clusters: int, total_dialogues: int, 
                           total_visualizations: int, output_file: str):
        """Log task completion with statistics."""
        completion_log = f"""
╔════════════════════════════════════════════════════════════════════════════════╗
                            TASK COMPLETION SUMMARY                                
╚════════════════════════════════════════════════════════════════════════════════╝

📊 Generation Statistics:
   - Total Clusters: {total_clusters}
   - Total Dialogues: {total_dialogues}
   - Total Visualizations: {total_visualizations}
   - LLM Calls Made: {self.call_counter}
   
📁 Output:
   - File: {output_file}
   - Completed: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

{'═' * 82}
                          END OF PODCAST GENERATION
{'═' * 82}
"""
        self._append_to_log(completion_log)
    
    def log_final_stats(self, stats: Dict[str, Any]):
        """Log final statistics."""
        stats_log = f"""
╔════════════════════════════════════════════════════════════════════════════════╗
                             FINAL STATISTICS                                      
╚════════════════════════════════════════════════════════════════════════════════╝

📊 Generation Stats:
   - Total LLM Calls: {self.call_counter}
   - Total Clusters: {stats.get('total_clusters', 0)}
   - Total Dialogues: {stats.get('total_dialogues', 0)}
   - Mermaid Diagrams: {stats.get('mermaid_diagrams', 0)}
   - Markdown Slides: {stats.get('markdown_slides', 0)}
   
📁 Output:
   - File: {stats.get('output_file', 'N/A')}
   - UUID: {stats.get('uuid', 'N/A')}
   - Size: {stats.get('file_size', 0):,} bytes

⏱️  Completed: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

{'═' * 82}
                            END OF LOG
{'═' * 82}
"""
        self._append_to_log(stats_log)
    
    def _append_to_log(self, content: str):
        """Append content to the log file."""
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(content)
    
    def get_log_path(self) -> str:
        """Return the full path to the log file."""
        return self.log_file


# Singleton instance for easy access
_logger_instance = None

def get_podcast_logger(task_id: str = None) -> PodcastLogger:
    """Get or create the podcast logger instance."""
    global _logger_instance
    if _logger_instance is None or (task_id and _logger_instance.task_id != task_id):
        _logger_instance = PodcastLogger(task_id=task_id)
    return _logger_instance