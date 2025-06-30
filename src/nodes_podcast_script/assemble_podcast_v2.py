from pocketflow import Node
from typing import Dict, List, Tuple
import json
import os
from datetime import datetime
import uuid


class AssemblePodcastV2(Node):
    """
    Assembles the final podcast JSON file from all components.
    Pure Python node - no LLM calls.
    """
    
    def prep(self, shared: Dict) -> Tuple[List[Dict], Dict, str]:
        """Get final clusters, config, and tutorial path."""
        final_clusters = shared["final_clusters"]
        generation_config = shared["generation_config"]
        tutorial_path = shared["tutorial_path"]
        
        return final_clusters, generation_config, tutorial_path
    
    def exec(self, inputs: Tuple[List[Dict], Dict, str]) -> Dict:
        """Assemble the final podcast structure."""
        clusters, config, tutorial_path = inputs
        
        # Generate podcast ID
        podcast_id = str(uuid.uuid4())[:8]
        
        # Get character info
        from .character_config import get_characters
        char1, char2 = get_characters(
            getattr(self, 'shared_context', {}).get('character_1'),
            getattr(self, 'shared_context', {}).get('character_2')
        )
        
        # Calculate statistics
        total_dialogues = sum(len(cluster.get('dialogues', [])) for cluster in clusters)
        total_visualizations = 0
        for cluster in clusters:
            for dialogue in cluster.get('dialogues', []):
                if 'visualization' in dialogue:
                    total_visualizations += 1
        
        # Extract project name from tutorial path
        project_name = os.path.basename(tutorial_path.rstrip('/'))
        
        # Build final structure
        podcast_data = {
            "metadata": {
                "podcast_id": podcast_id,
                "generated_at": datetime.now().isoformat(),
                "project_name": project_name,
                "generation_config": {
                    "preset": config.get("preset", "custom"),
                    "language": config.get("language", "english"),
                    "focus_areas": config.get("focus_areas", []),
                    "custom_prompt": config.get("custom_prompt", ""),
                    "max_dialogues_per_cluster": config.get("max_dialogues_per_cluster", 4)
                },
                "statistics": {
                    "total_clusters": len(clusters),
                    "total_dialogues": total_dialogues,
                    "total_visualizations": total_visualizations,
                    "average_dialogues_per_cluster": round(total_dialogues / len(clusters), 1) if clusters else 0
                }
            },
            "participants": [
                {
                    "name": char1.name,
                    "role": char1.role,
                    "personality": char1.personality,
                    "background": char1.background,
                    "speaking_style": char1.speaking_style
                },
                {
                    "name": char2.name,
                    "role": char2.role,
                    "personality": char2.personality,
                    "background": char2.background,
                    "speaking_style": char2.speaking_style
                }
            ],
            "clusters": []
        }
        
        # Add clusters with cleaned structure
        for cluster in clusters:
            cluster_data = {
                "cluster_id": cluster['cluster_id'],
                "cluster_title": cluster['title'],
                "mckinsey_summary": cluster.get('mckinsey_summary', ''),
                "dialogues": []
            }
            
            # Add dialogues
            for dialogue in cluster.get('dialogues', []):
                dialogue_data = {
                    "dialogue_id": dialogue['dialogue_id'],
                    "speaker": dialogue['speaker'],
                    "text": dialogue['text'],
                    "emotion": dialogue.get('emotion', 'neutral')
                }
                
                # Add visualization if present
                if 'visualization' in dialogue:
                    dialogue_data['visualization'] = dialogue['visualization']
                
                cluster_data['dialogues'].append(dialogue_data)
            
            podcast_data['clusters'].append(cluster_data)
        
        # Save to file
        output_filename = f"podcast_{podcast_id}.json"
        output_path = os.path.join(tutorial_path, output_filename)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(podcast_data, f, indent=2, ensure_ascii=False)
        
        return {
            "podcast_id": podcast_id,
            "output_path": output_path,
            "statistics": podcast_data["metadata"]["statistics"]
        }
    
    def post(self, shared: Dict, prep_res: Tuple, exec_res: Dict) -> str:
        """Store results in shared context."""
        # Store shared context for exec
        self.shared_context = shared
        
        shared["podcast_result"] = exec_res
        
        # Log final progress
        progress_callback = shared.get("progress_callback")
        if progress_callback and callable(progress_callback):
            stats = exec_res["statistics"]
            progress_callback(
                "assemble_podcast",
                f"Created podcast with {stats['total_clusters']} clusters, "
                f"{stats['total_dialogues']} dialogues, and {stats['total_visualizations']} visualizations"
            )
        
        # Log completion
        if shared.get("logging_enabled") and shared.get("task_id"):
            from src.utils.podcast_logger import PodcastLogger
            logger = PodcastLogger(shared.get("task_id"))
            logger.log_task_completion(
                total_clusters=stats['total_clusters'],
                total_dialogues=stats['total_dialogues'],
                total_visualizations=stats['total_visualizations'],
                output_file=exec_res['output_path']
            )
        
        return "default"