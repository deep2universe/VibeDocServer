from pocketflow import Node
from typing import Dict, List, Tuple


class EnrichDialogueIDs(Node):
    """
    Enriches dialogues with global sequential IDs.
    This is a pure Python node that doesn't call LLMs.
    """
    
    def prep(self, shared: Dict) -> List[Dict]:
        """Get clusters with dialogues from shared context."""
        return shared.get("clusters_with_dialogues", [])
    
    def exec(self, clusters: List[Dict]) -> List[Dict]:
        """Add sequential dialogue IDs across all clusters."""
        global_id = 1
        enriched_clusters = []
        
        for cluster in clusters:
            enriched_cluster = cluster.copy()
            enriched_dialogues = []
            
            for dialogue in cluster.get('dialogues', []):
                enriched_dialogue = dialogue.copy()
                enriched_dialogue['dialogue_id'] = global_id
                global_id += 1
                enriched_dialogues.append(enriched_dialogue)
            
            enriched_cluster['dialogues'] = enriched_dialogues
            enriched_clusters.append(enriched_cluster)
        
        return enriched_clusters
    
    def post(self, shared: Dict, prep_res: List[Dict], exec_res: List[Dict]) -> str:
        """Store enriched clusters in shared context."""
        shared["enriched_clusters"] = exec_res
        
        # Calculate total dialogue count
        total_dialogues = sum(len(cluster.get('dialogues', [])) for cluster in exec_res)
        
        # Log progress
        progress_callback = shared.get("progress_callback")
        if progress_callback and callable(progress_callback):
            progress_callback(
                "enrich_dialogue_ids",
                f"Added IDs to {total_dialogues} dialogues"
            )
        
        return "default"