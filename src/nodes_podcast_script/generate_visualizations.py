from pocketflow import BatchNode
from typing import Dict, List, Tuple, Any, Optional
from src.utils.call_llm import call_llm
from src.utils.call_llm_with_logging import call_llm_with_logging
from src.utils.podcast_logger import PodcastLogger
from .character_config import get_characters
import yaml
import re


class GenerateVisualizations(BatchNode):
    """
    Generates visualizations for dialogue clusters using LLM.
    Handles flexible parsing and error recovery.
    """
    
    def prep(self, shared: Dict) -> List[Tuple[Dict, Dict]]:
        """Prepare clusters for visualization generation."""
        clusters = shared["enriched_clusters"]
        generation_config = shared["generation_config"]
        
        # Get character configurations
        char1, char2 = get_characters(
            shared.get("character_1"),
            shared.get("character_2")
        )
        
        # Store for later use
        self.shared_context = shared
        self.characters = (char1, char2)
        
        # Return list of (cluster, config) tuples
        return [(cluster, generation_config) for cluster in clusters]
    
    def exec(self, inputs: Tuple[Dict, Dict]) -> Dict:
        """Generate visualizations for one cluster."""
        cluster, config = inputs
        
        # Create visualization prompt
        prompt = self._create_visualization_prompt(cluster, config)
        
        # Call LLM with logging if enabled
        shared = getattr(self, 'shared_context', {})
        if shared.get("logging_enabled") and shared.get("task_id"):
            response = call_llm_with_logging(
                prompt=prompt,
                node_name="GenerateVisualizations",
                cluster_info={
                    "cluster_id": cluster['cluster_id'],
                    "title": cluster['title']
                },
                task_id=shared.get("task_id")
            )
        else:
            response = call_llm(prompt)
        
        # Parse and apply visualizations
        cluster_with_visuals = self._parse_and_apply_visualizations(response, cluster)
        
        return cluster_with_visuals
    
    def _create_visualization_prompt(self, cluster: Dict, config: Dict) -> str:
        """Create detailed prompt for visualization generation."""
        char1, char2 = self.characters
        
        # Format dialogues with IDs
        dialogue_text = self._format_dialogues_with_ids(cluster['dialogues'])
        
        # Format existing diagrams
        existing_diagrams = ""
        if cluster.get('existing_diagrams'):
            existing_diagrams = "\n\n".join([
                f"Diagram {i+1}:\n```mermaid\n{diag}\n```"
                for i, diag in enumerate(cluster['existing_diagrams'][:3])
            ])
        
        # Preset-specific instructions
        preset_instructions = {
            "overview": "Create high-level conceptual visualizations. Focus on architecture and relationships. Use occasional emojis (🎯 ✅ 💡) for clarity.",
            "deep_dive": "Include detailed technical diagrams, code snippets, and implementation specifics. Show actual code examples where relevant.",
            "comprehensive": "Mix conceptual overviews with detailed breakdowns. Show both forest and trees. Include code examples and architectural diagrams.",
            "custom": "Balance technical detail with clarity based on the context."
        }
        
        # Get language
        language = config.get("language", "english")
        
        # Language instructions for visualizations
        language_viz_instructions = {
            "english": "Create all text content in visualizations in English.",
            "german": "Erstelle alle Textinhalte in den Visualisierungen auf Deutsch.",
            "spanish": "Crea todo el contenido de texto en las visualizaciones en español.",
            "french": "Créez tout le contenu textuel dans les visualisations en français.",
            "italian": "Crea tutto il contenuto testuale nelle visualizzazioni in italiano.",
            "portuguese": "Crie todo o conteúdo de texto nas visualizações em português.",
            "dutch": "Maak alle tekstinhoud in de visualisaties in het Nederlands.",
            "russian": "Создайте весь текстовый контент в визуализациях на русском языке.",
            "japanese": "ビジュアライゼーションのすべてのテキストコンテンツを日本語で作成してください。",
            "chinese": "在可视化中用中文创建所有文本内容。",
            "korean": "시각화의 모든 텍스트 콘텐츠를 한국어로 작성하세요."
        }
        
        language_instruction = language_viz_instructions.get(
            language.lower(),
            f"Create all text content in visualizations in {language}."
        )
        
        prompt = f"""
You are creating visualizations for a technical podcast between {char1.name} and {char2.name}.

**LANGUAGE REQUIREMENT**: {language_instruction}

## Dialogue Context
Topic: {cluster['title']}
Number of dialogues: {len(cluster['dialogues'])}

## Numbered Dialogues:
{dialogue_text}

## Source Material:
{cluster['content'][:2000]}{"..." if len(cluster['content']) > 2000 else ""}

{f"## Available Mermaid Diagrams from Source:\n{existing_diagrams}" if existing_diagrams else ""}

## Visualization Requirements:

1. **Assignment Rules**:
   - Create ONE visualization per speaker turn
   - You may group 2 consecutive speakers to share ONE visualization (never more than 2)
   - Use dialogue IDs for assignment

2. **CRITICAL FIRST SLIDE REQUIREMENT**:
   - The FIRST visualization (for dialogue_id 1) MUST be markdown type
   - This first markdown slide MUST include this image at the end:
     ![](https://vibedoc.s3.eu-central-1.amazonaws.com/VibeDoc_w.png)
   - Place the image AFTER your markdown content with a blank line before it

3. **Visualization Types**:
   - **Mermaid diagrams**: flowcharts, sequence diagrams, class diagrams, state diagrams, etc.
   - **Markdown slides**: bullet points, code blocks, tables, formatted text, headers
   
4. **Content Guidelines**:
   - {preset_instructions.get(config.get('preset', 'custom'), preset_instructions['custom'])}
   - Visualizations should ENHANCE the dialogue, not repeat it
   - For {char1.name}'s questions: Show what they're asking about visually
   - For {char2.name}'s answers: Illustrate the explanation with diagrams or structured content
   - Reuse/adapt existing diagrams where relevant, but feel free to create new ones

5. **Quality Standards**:
   - Mermaid: Must be syntactically correct and meaningful
   - Markdown: Rich content with structure (headers, lists, code blocks)
   - Avoid sparse content - each visualization should provide substantial value
   - Code examples should be realistic and relevant

6. **Style Guide**:
   - Professional but approachable
   - Clear labeling and structure
   - Consistent formatting throughout

{f"7. **Special Focus**: Emphasize these areas: {', '.join(config.get('focus_areas', []))}" if config.get('focus_areas') else ""}

## Output Format
Return ONLY the YAML structure below. Do not include any other text.

```yaml
visualizations:
  - dialogue_ids: [1]  # MUST be markdown type for first dialogue
    type: markdown
    content: |
      ## Relevant Title Here
      
      Your markdown content here...
      Multiple lines supported
      
      ![](https://vibedoc.s3.eu-central-1.amazonaws.com/VibeDoc_w.png)
      
  - dialogue_ids: [2]
    type: mermaid
    content: |
      graph TD
        A[Start] --> B[Process]
        B --> C[End]
        
  - dialogue_ids: [3, 4]  # Grouping two speakers
    type: markdown
    content: |
      ### Key Concepts
      - Point 1 with explanation
      - Point 2 with details
      
      ```python
      # Example code if relevant
      def example():
          return "Hello"
      ```
```

IMPORTANT: Each dialogue_id must have exactly ONE visualization assigned to it.
"""
        return prompt
    
    def _format_dialogues_with_ids(self, dialogues: List[Dict]) -> str:
        """Format dialogues with their IDs for the prompt."""
        formatted = []
        for d in dialogues:
            speaker = d['speaker'].title()
            text_preview = d['text'][:200] + "..." if len(d['text']) > 200 else d['text']
            formatted.append(f"{d['dialogue_id']}. {speaker}: \"{text_preview}\"")
        return "\n".join(formatted)
    
    def _parse_and_apply_visualizations(self, response: str, cluster: Dict) -> Dict:
        """Parse visualization response and apply to cluster dialogues."""
        visualizations = self._parse_visualization_response(response, cluster)
        
        # Create a copy of the cluster
        result_cluster = cluster.copy()
        result_cluster['dialogues'] = [d.copy() for d in cluster['dialogues']]
        
        # Get valid dialogue IDs
        valid_dialogue_ids = {d['dialogue_id'] for d in result_cluster['dialogues']}
        assigned_ids = set()
        
        # Apply visualizations
        for viz in visualizations:
            viz_dialogue_ids = viz.get('dialogue_ids', [])
            
            # Filter out invalid IDs
            valid_ids = [id for id in viz_dialogue_ids if id in valid_dialogue_ids]
            invalid_ids = [id for id in viz_dialogue_ids if id not in valid_dialogue_ids]
            
            # Log invalid IDs
            if invalid_ids and self.shared_context.get("logging_enabled"):
                logger = PodcastLogger(self.shared_context.get("task_id"))
                logger.log_warning(
                    "GenerateVisualizations",
                    f"Cluster {cluster['cluster_id']}: Visualization references invalid dialogue IDs: {invalid_ids}"
                )
            
            # Assign visualization to valid dialogues
            if valid_ids:
                for dialogue_id in valid_ids:
                    if dialogue_id not in assigned_ids:
                        self._assign_visualization_to_dialogue(
                            result_cluster['dialogues'],
                            dialogue_id,
                            viz
                        )
                        assigned_ids.add(dialogue_id)
        
        return result_cluster
    
    def _parse_visualization_response(self, response: str, cluster: Dict) -> List[Dict]:
        """Flexible parsing of visualization response."""
        visualizations = []
        
        try:
            # Try standard YAML parsing
            yaml_match = re.search(r'```yaml\n(.*?)\n```', response, re.DOTALL)
            if yaml_match:
                yaml_content = yaml_match.group(1)
                data = yaml.safe_load(yaml_content)
                visualizations = data.get('visualizations', [])
        except Exception as e:
            # Log parsing error
            if self.shared_context.get("logging_enabled"):
                logger = PodcastLogger(self.shared_context.get("task_id"))
                logger.log_warning(
                    "GenerateVisualizations",
                    f"YAML parsing failed for cluster {cluster['cluster_id']}: {str(e)}"
                )
            
            # Try fallback parsing
            try:
                visualizations = self._extract_visualizations_fallback(response)
            except Exception as e2:
                # Log complete failure
                if self.shared_context.get("logging_enabled"):
                    logger = PodcastLogger(self.shared_context.get("task_id"))
                    logger.log_error(
                        "GenerateVisualizations",
                        f"Failed to parse visualizations for cluster {cluster['cluster_id']}: {str(e2)}"
                    )
                return []
        
        return visualizations
    
    def _extract_visualizations_fallback(self, response: str) -> List[Dict]:
        """Fallback method to extract visualizations using regex."""
        visualizations = []
        
        # Pattern to find visualization blocks
        viz_pattern = r'dialogue_ids:\s*\[([\d,\s]+)\]\s*type:\s*(\w+)\s*content:\s*\|\s*((?:(?!\n\s*-).)*)'
        
        matches = re.finditer(viz_pattern, response, re.MULTILINE | re.DOTALL)
        
        for match in matches:
            try:
                # Extract IDs
                ids_str = match.group(1)
                ids = [int(id.strip()) for id in ids_str.split(',')]
                
                # Extract type
                viz_type = match.group(2).strip()
                
                # Extract content
                content = match.group(3).strip()
                
                visualizations.append({
                    'dialogue_ids': ids,
                    'type': viz_type,
                    'content': content
                })
            except:
                continue
        
        return visualizations
    
    def _assign_visualization_to_dialogue(self, dialogues: List[Dict], dialogue_id: int, viz: Dict):
        """Assign visualization to a specific dialogue."""
        for dialogue in dialogues:
            if dialogue['dialogue_id'] == dialogue_id:
                dialogue['visualization'] = {
                    'type': viz.get('type', 'markdown'),
                    'content': viz.get('content', '')
                }
                break
    
    def post(self, shared: Dict, prep_res: List[Tuple], exec_res_list: List[Dict]) -> str:
        """Store clusters with visualizations."""
        shared["clusters_with_visuals"] = exec_res_list
        
        # Count visualizations
        total_visuals = 0
        for cluster in exec_res_list:
            for dialogue in cluster.get('dialogues', []):
                if 'visualization' in dialogue:
                    total_visuals += 1
        
        # Log progress
        progress_callback = shared.get("progress_callback")
        if progress_callback and callable(progress_callback):
            progress_callback(
                "generate_visualizations",
                f"Generated {total_visuals} visualizations"
            )
        
        return "default"