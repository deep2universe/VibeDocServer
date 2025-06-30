from pocketflow import Flow
from src.nodes_podcast_script import (
    ParseTutorialV2,
    GenerateClusterDialogues,
    EnrichDialogueIDs,
    GenerateVisualizations,
    EnrichWithMetadata,
    AssemblePodcastV2,
    ValidateMermaidDiagrams
)


def create_podcast_flow_v2():
    """
    Creates the simplified podcast generation flow v2.
    Each markdown file becomes one cluster with dialogues and visualizations.
    """
    
    # Instantiate nodes_code_tutorial with appropriate retry settings
    parse_tutorial = ParseTutorialV2()
    generate_dialogues = GenerateClusterDialogues(max_retries=5, wait=15)  # BatchNode
    enrich_ids = EnrichDialogueIDs()
    generate_visuals = GenerateVisualizations(max_retries=5, wait=15)  # BatchNode
    enrich_metadata = EnrichWithMetadata(max_retries=3, wait=10)
    assemble_podcast = AssemblePodcastV2()
    validate_mermaid = ValidateMermaidDiagrams(max_retries=3, wait=10)  # New validation node
    
    # Connect nodes_code_tutorial in sequence
    parse_tutorial >> generate_dialogues
    generate_dialogues >> enrich_ids
    enrich_ids >> generate_visuals
    generate_visuals >> enrich_metadata
    enrich_metadata >> assemble_podcast
    assemble_podcast >> validate_mermaid
    
    # Create the flow starting with ParseTutorialV2
    podcast_flow_v2 = Flow(start=parse_tutorial)
    
    return podcast_flow_v2