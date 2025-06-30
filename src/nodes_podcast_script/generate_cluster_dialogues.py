from pocketflow import BatchNode
from typing import Dict, List, Tuple, Any
from src.utils.call_llm import call_llm
from src.utils.call_llm_with_logging import call_llm_with_logging
from .character_config import get_characters
import yaml


class GenerateClusterDialogues(BatchNode):
    """
    Generates dialogues for each cluster using LLM.
    Uses BatchNode to process clusters in parallel.
    """
    
    # Preset-specific dialogue styles
    PRESET_DIALOGUE_STYLES = {
        "overview": """
        - Keep technical details light, focus on concepts and purposes
        - Use analogies and real-world examples
        - Emphasize the "why" before the "how"
        - Perfect for managers or beginners
        """,
        
        "deep_dive": """
        - Dive into implementation details and code specifics
        - Discuss design decisions and trade-offs
        - Include performance considerations
        - Assume technical background
        """,
        
        "comprehensive": """
        - Start with high-level concepts, then drill down
        - Balance theory with practical implementation
        - Include both beginner-friendly explanations and expert insights
        - Cover edge cases and advanced usage
        """,
        
        "custom": """
        - Adapt based on the provided focus areas and custom prompt
        - Balance technical accuracy with accessibility
        """
    }
    
    def prep(self, shared: Dict) -> List[Tuple[Dict, Dict]]:
        """Prepare clusters for parallel processing."""
        clusters = shared["clusters"]
        generation_config = shared["generation_config"]
        
        # Get character configurations
        char1, char2 = get_characters(
            shared.get("character_1"),
            shared.get("character_2")
        )
        
        # Store for later use
        self.shared_context = shared
        self.characters = (char1, char2)
        
        # Return list of (cluster, config) tuples for batch processing
        return [(cluster, generation_config) for cluster in clusters]
    
    def exec(self, inputs: Tuple[Dict, Dict]) -> Dict:
        """Generate dialogues for one cluster."""
        cluster, config = inputs
        
        # Create dialogue generation prompt
        prompt = self._create_dialogue_prompt(cluster, config)
        
        # Call LLM with logging if enabled
        shared = getattr(self, 'shared_context', {})
        if shared.get("logging_enabled") and shared.get("task_id"):
            response = call_llm_with_logging(
                prompt=prompt,
                node_name="GenerateClusterDialogues",
                cluster_info={
                    "cluster_id": cluster['cluster_id'],
                    "title": cluster['title']
                },
                task_id=shared.get("task_id")
            )
        else:
            response = call_llm(prompt)
        
        # Parse response
        try:
            yaml_content = response.split("```yaml")[1].split("```")[0].strip()
            dialogue_data = yaml.safe_load(yaml_content)
            dialogues = dialogue_data.get("dialogues", [])
        except Exception as e:
            # Fallback: create minimal dialogue
            dialogues = [{
                "speaker": self.characters[0].name.lower(),
                "text": f"Let's explore {cluster['title']}!",
                "emotion": "eager"
            }]
        
        # Return cluster with dialogues
        cluster_with_dialogues = cluster.copy()
        cluster_with_dialogues["dialogues"] = dialogues
        
        return cluster_with_dialogues
    
    def _create_dialogue_prompt(self, cluster: Dict, config: Dict) -> str:
        """Create detailed prompt for dialogue generation."""
        char1, char2 = self.characters
        
        # Determine max dialogues
        max_dialogues = config.get("max_dialogues_per_cluster", 4)
        if cluster['cluster_id'] == 'index':
            max_dialogues = 1
        
        # Get preset style
        preset_style = self.PRESET_DIALOGUE_STYLES.get(
            config.get("preset", "custom"),
            self.PRESET_DIALOGUE_STYLES["custom"]
        )
        
        # Get language
        language = config.get("language", "english")
        
        # Language-specific instructions
        language_instructions = {
            "english": "Generate the dialogue in English.",
            "german": "Generiere den Dialog auf Deutsch. Verwende natürliche deutsche Ausdrücke und Redewendungen.",
            "spanish": "Genera el diálogo en español. Usa expresiones naturales en español.",
            "french": "Générez le dialogue en français. Utilisez des expressions naturelles en français.",
            "italian": "Genera il dialogo in italiano. Usa espressioni naturali in italiano.",
            "portuguese": "Gere o diálogo em português. Use expressões naturais em português.",
            "dutch": "Genereer de dialoog in het Nederlands. Gebruik natuurlijke Nederlandse uitdrukkingen.",
            "russian": "Создайте диалог на русском языке. Используйте естественные русские выражения.",
            "japanese": "日本語で対話を生成してください。自然な日本語の表現を使用してください。",
            "chinese": "用中文生成对话。使用自然的中文表达。",
            "korean": "한국어로 대화를 생성하세요. 자연스러운 한국어 표현을 사용하세요."
        }
        
        language_instruction = language_instructions.get(
            language.lower(), 
            f"Generate the dialogue in {language}. Use natural expressions in this language."
        )
        
        prompt = f"""
You are creating an engaging podcast dialogue about technical topics.

**LANGUAGE REQUIREMENT**: {language_instruction}

## Characters

**{char1.name}** ({char1.role}):
- Personality: {char1.personality}
- Background: {char1.background}
- Speaking style: {char1.speaking_style}

**{char2.name}** ({char2.role}):
- Personality: {char2.personality}
- Background: {char2.background}
- Speaking style: {char2.speaking_style}

## Content to Discuss
Topic: {cluster['title']}
{"This is the introduction - create an engaging podcast opening!" if cluster['is_first'] else f"Transition naturally from '{cluster['prev_title']}' to '{cluster['title']}'"}

Source Material:
{cluster['content'][:3000]}{"..." if len(cluster['content']) > 3000 else ""}

## Dialogue Requirements

1. **Number of exchanges**: Generate {max_dialogues} dialogue pairs maximum
   - For short content (<200 words), you may generate fewer exchanges
   - index.md should have exactly 1 exchange (opening only)

2. **Dialogue Style**:
   - Make it CONVERSATIONAL and NATURAL - use contractions, pauses, "um", "well", etc.
   - Include reactions: "Oh, that's interesting!", "Wait, so you mean..."
   - Show personality through speech patterns
   - {char1.name} drives the conversation with questions/observations
   - {char2.name} provides insights and explanations
   
3. **Content Approach**:
   - DON'T read the markdown verbatim
   - Extract KEY CONCEPTS and discuss them naturally
   - Use the source as inspiration, not a script
   - Add personal experiences, analogies, and examples
   - Make technical concepts accessible through conversation

4. **Emotional Dynamics**:
   - Vary emotions naturally (curious→excited, confused→enlightened)
   - React to each other's points
   - Show genuine interest and enthusiasm
   - Available emotions for {char1.name}: curious, thoughtful, excited, confused, surprised, satisfied, eager, contemplative
   - Available emotions for {char2.name}: encouraging, enthusiastic, patient, amused, impressed, thoughtful, explanatory

5. **Preset Style**: {config.get("preset", "custom")}
{preset_style}

{f"6. **Special Focus**: Emphasize these areas in your discussion: {', '.join(config.get('focus_areas', []))}" if config.get('focus_areas') else ""}
{f"7. **Additional Context**: {config.get('custom_prompt')}" if config.get('custom_prompt') else ""}

## Output Format
Generate dialogue in this exact YAML format:

```yaml
dialogues:
  - speaker: {char1.name.lower()}
    text: "Your natural, conversational text here..."
    emotion: curious
  - speaker: {char2.name.lower()}
    text: "Response that feels like real conversation..."
    emotion: encouraging
```

Remember: This should feel like overhearing two people having a genuine, engaging conversation about technology!
"""
        return prompt
    
    def post(self, shared: Dict, prep_res: List[Tuple], exec_res_list: List[Dict]) -> str:
        """Store clusters with dialogues."""
        shared["clusters_with_dialogues"] = exec_res_list
        
        # Count total dialogues
        total_dialogues = sum(len(cluster.get('dialogues', [])) for cluster in exec_res_list)
        
        # Log progress
        progress_callback = shared.get("progress_callback")
        if progress_callback and callable(progress_callback):
            progress_callback(
                "generate_dialogues",
                f"Generated {total_dialogues} dialogues across {len(exec_res_list)} clusters"
            )
        
        return "default"