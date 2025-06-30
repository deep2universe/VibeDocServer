from pocketflow import Node
from typing import Dict, List, Tuple
from src.utils.call_llm import call_llm
from src.utils.call_llm_with_logging import call_llm_with_logging
import yaml


class EnrichWithMetadata(Node):
    """
    Enriches clusters with McKinsey-style summaries.
    Single LLM call to generate summaries for all clusters.
    """
    
    def prep(self, shared: Dict) -> Tuple[List[Dict], Dict]:
        """Get clusters and generation config."""
        clusters = shared["clusters_with_visuals"]
        generation_config = shared["generation_config"]
        return clusters, generation_config
    
    def exec(self, inputs: Tuple[List[Dict], Dict]) -> List[Dict]:
        """Generate McKinsey summaries for all clusters."""
        clusters, config = inputs
        
        # Create summary generation prompt
        prompt = self._create_summary_prompt(clusters, config)
        
        # Call LLM with logging if enabled
        shared = getattr(self, 'shared_context', {})
        if shared.get("logging_enabled") and shared.get("task_id"):
            response = call_llm_with_logging(
                prompt=prompt,
                node_name="EnrichWithMetadata",
                cluster_info={"action": "generate_summaries"},
                task_id=shared.get("task_id")
            )
        else:
            response = call_llm(prompt)
        
        # Parse summaries
        summaries = self._parse_summaries(response)
        
        # Apply summaries to clusters
        enriched_clusters = []
        for cluster in clusters:
            enriched_cluster = cluster.copy()
            cluster_id = cluster['cluster_id']
            
            # Find matching summary
            summary = summaries.get(cluster_id, self._generate_fallback_summary(cluster))
            enriched_cluster['mckinsey_summary'] = summary
            
            enriched_clusters.append(enriched_cluster)
        
        return enriched_clusters
    
    def _create_summary_prompt(self, clusters: List[Dict], config: Dict) -> str:
        """Create prompt for McKinsey-style summaries."""
        # Format cluster information
        cluster_info = []
        for cluster in clusters:
            # Get dialogue summary
            dialogue_count = len(cluster.get('dialogues', []))
            topics = self._extract_topics_from_dialogues(cluster['dialogues'])
            
            cluster_info.append(f"""
- cluster_id: {cluster['cluster_id']}
  title: {cluster['title']}
  dialogue_count: {dialogue_count}
  key_topics: {', '.join(topics[:5])}
""")
        
        cluster_text = "\n".join(cluster_info)
        
        # Preset-specific focus
        preset_focus = {
            "overview": "Focus on business value and high-level benefits",
            "deep_dive": "Emphasize technical innovation and implementation efficiency",
            "comprehensive": "Balance strategic value with technical excellence",
            "custom": "Create impactful summaries that capture the essence"
        }
        
        # Get language
        language = config.get("language", "english")
        
        # Language-specific instructions
        language_summary_instructions = {
            "english": "Generate the summaries in English.",
            "german": "Generiere die Zusammenfassungen auf Deutsch. Verwende prägnante deutsche Geschäftssprache.",
            "spanish": "Genera los resúmenes en español. Usa lenguaje empresarial español conciso.",
            "french": "Générez les résumés en français. Utilisez un langage commercial français concis.",
            "italian": "Genera i riassunti in italiano. Usa un linguaggio aziendale italiano conciso.",
            "portuguese": "Gere os resumos em português. Use linguagem empresarial portuguesa concisa.",
            "dutch": "Genereer de samenvattingen in het Nederlands. Gebruik beknopte Nederlandse zakelijke taal.",
            "russian": "Создайте резюме на русском языке. Используйте краткий деловой русский язык.",
            "japanese": "要約を日本語で生成してください。簡潔なビジネス日本語を使用してください。",
            "chinese": "用中文生成摘要。使用简洁的商务中文。",
            "korean": "요약을 한국어로 생성하세요. 간결한 비즈니스 한국어를 사용하세요."
        }
        
        language_instruction = language_summary_instructions.get(
            language.lower(),
            f"Generate the summaries in {language}. Use concise business language."
        )
        
        prompt = f"""
Generate McKinsey-style executive summaries for these podcast clusters.

**LANGUAGE REQUIREMENT**: {language_instruction}

## Clusters to Summarize:
{cluster_text}

## Requirements:
1. Each summary should be ONE concise sentence (10-15 words)
2. Use quantified impact where possible (e.g., "reduces complexity by 70%")
3. Focus on VALUE and OUTCOMES, not just features
4. Make it memorable and impactful
5. {preset_focus.get(config.get('preset', 'custom'), preset_focus['custom'])}

{f"6. Special emphasis on: {', '.join(config.get('focus_areas', []))}" if config.get('focus_areas') else ""}

## Examples of Good McKinsey-Style Summaries:
- "Orchestration patterns that reduce system complexity by 70% while improving maintainability"
- "Foundation concepts enabling 10x faster workflow automation with minimal code"
- "Battle-tested architecture patterns from Fortune 500 distributed systems"

## Output Format:
```yaml
summaries:
  index: "Your impactful one-liner summary here"
  01_flow: "Another powerful summary"
  02_node: "Value-focused summary"
```

Generate summaries for ALL clusters listed above.
"""
        return prompt
    
    def _extract_topics_from_dialogues(self, dialogues: List[Dict]) -> List[str]:
        """Extract key topics from dialogue content."""
        topics = []
        
        for dialogue in dialogues[:3]:  # Look at first 3 dialogues
            text = dialogue.get('text', '')
            # Simple topic extraction - look for capitalized phrases
            words = text.split()
            for i in range(len(words) - 1):
                if words[i][0].isupper() and words[i+1][0].isupper():
                    topic = f"{words[i]} {words[i+1]}"
                    if topic not in topics and len(topic) > 5:
                        topics.append(topic)
        
        return topics
    
    def _parse_summaries(self, response: str) -> Dict[str, str]:
        """Parse McKinsey summaries from LLM response."""
        summaries = {}
        
        try:
            # Extract YAML content
            yaml_content = response.split("```yaml")[1].split("```")[0].strip()
            data = yaml.safe_load(yaml_content)
            summaries = data.get('summaries', {})
        except Exception as e:
            # Log error but don't fail
            pass
        
        return summaries
    
    def _generate_fallback_summary(self, cluster: Dict) -> str:
        """Generate fallback summary if parsing fails."""
        title = cluster['title']
        return f"Essential concepts and patterns for understanding {title}"
    
    def post(self, shared: Dict, prep_res: Tuple, exec_res: List[Dict]) -> str:
        """Store final enriched clusters."""
        shared["final_clusters"] = exec_res
        
        # Store shared context for exec
        self.shared_context = shared
        
        # Log progress
        progress_callback = shared.get("progress_callback")
        if progress_callback and callable(progress_callback):
            progress_callback(
                "enrich_metadata",
                f"Generated McKinsey summaries for {len(exec_res)} clusters"
            )
        
        return "default"