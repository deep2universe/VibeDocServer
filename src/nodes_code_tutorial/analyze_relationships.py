import yaml
from pocketflow import Node
from src.utils.call_llm import call_llm
from .identify_abstractions import get_content_for_indices
from src.utils.token_counter import (
    check_prompt_size,
    truncate_prompt,
    estimate_tokens,
    DEFAULT_MAX_CONTEXT_TOKENS
)


class AnalyzeRelationships(Node):
    def prep(self, shared):
        # Store shared context for SSE callbacks
        self._shared = shared
        
        abstractions = shared[
            "abstractions"
        ]  # Now contains 'files' list of indices, name/description potentially translated
        files_data = shared["files"]
        project_name = shared["project_name"]  # Get project name
        language = shared.get("language", "english")  # Get language
        use_cache = shared.get("use_cache", True)  # Get use_cache flag, default to True

        # Get the actual number of abstractions directly
        num_abstractions = len(abstractions)

        # Create context with abstraction names, indices, descriptions, and relevant file snippets
        context = "Identified Abstractions:\n"
        all_relevant_indices = set()
        abstraction_info_for_prompt = []
        for i, abstr in enumerate(abstractions):
            # Use 'files' which contains indices directly
            file_indices_str = ", ".join(map(str, abstr["files"]))
            # Abstraction name and description might be translated already
            info_line = f"- Index {i}: {abstr['name']} (Relevant file indices: [{file_indices_str}])\n  Description: {abstr['description']}"
            context += info_line + "\n"
            abstraction_info_for_prompt.append(
                f"{i} # {abstr['name']}"
            )  # Use potentially translated name here too
            all_relevant_indices.update(abstr["files"])

        context += "\nRelevant File Snippets (Referenced by Index and Path):\n"
        # Get content for relevant files using helper
        relevant_files_content_map = get_content_for_indices(
            files_data, sorted(list(all_relevant_indices))
        )
        
        # Check token budget for file content
        max_context_tokens = shared.get("max_context_tokens", DEFAULT_MAX_CONTEXT_TOKENS)
        base_context_tokens = estimate_tokens(context)
        remaining_tokens = max_context_tokens - base_context_tokens - 5000  # Reserve for prompt template
        
        # Build file context with token limit awareness
        file_context_parts = []
        current_tokens = 0
        
        for idx_path, content in relevant_files_content_map.items():
            file_entry = f"--- File: {idx_path} ---\n{content}\n\n"
            entry_tokens = estimate_tokens(file_entry)
            
            if current_tokens + entry_tokens <= remaining_tokens:
                file_context_parts.append(file_entry)
                current_tokens += entry_tokens
            else:
                # Try to include a truncated version
                available_tokens = remaining_tokens - current_tokens
                if available_tokens > 500:  # Only include if meaningful content can fit
                    chars_to_include = available_tokens * 3  # Rough estimate
                    truncated_content = content[:chars_to_include]
                    # Find good breakpoint
                    last_newline = truncated_content.rfind('\n')
                    if last_newline > chars_to_include * 0.7:
                        truncated_content = truncated_content[:last_newline]
                    file_context_parts.append(
                        f"--- File: {idx_path} ---\n{truncated_content}\n\n[... truncated due to token limit ...]\n\n"
                    )
                break
        
        file_context_str = "".join(file_context_parts)
        context += file_context_str
        
        if len(file_context_parts) < len(relevant_files_content_map):
            excluded_count = len(relevant_files_content_map) - len(file_context_parts)
            print(f"Note: {excluded_count} file(s) excluded from context due to token limits")

        return (
            context,
            "\n".join(abstraction_info_for_prompt),
            num_abstractions, # Pass the actual count
            project_name,
            language,
            use_cache,
        )  # Return use_cache

    def exec(self, prep_res):
        (
            context,
            abstraction_listing,
            num_abstractions, # Receive the actual count
            project_name,
            language,
            use_cache,
         ) = prep_res  # Unpack use_cache
        print(f"Analyzing relationships using LLM...")
        
        # Send SSE update
        if hasattr(self, '_shared') and 'sse_callback' in self._shared:
            callback = self._shared['sse_callback']
            callback("node_progress", {
                "node": "AnalyzeRelationships",
                "status": "starting",
                "message": "Analyzing relationships between abstractions..."
            })

        # Add language instruction and hints only if not English
        language_instruction = ""
        lang_hint = ""
        list_lang_note = ""
        if language.lower() != "english":
            language_instruction = f"IMPORTANT: Generate the `summary` and relationship `label` fields in **{language.capitalize()}** language. Do NOT use English for these fields.\n\n"
            lang_hint = f" (in {language.capitalize()})"
            list_lang_note = f" (Names might be in {language.capitalize()})"  # Note for the input list

        prompt = f"""
Based on the following abstractions and relevant code snippets from the project `{project_name}`:

List of Abstraction Indices and Names{list_lang_note}:
{abstraction_listing}

Context (Abstractions, Descriptions, Code):
{context}

{language_instruction}Please provide:
1. A high-level `summary` of the project's main purpose and functionality in a few beginner-friendly sentences{lang_hint}. Use markdown formatting with **bold** and *italic* text to highlight important concepts.
2. A list (`relationships`) describing the key interactions between these abstractions. For each relationship, specify:
    - `from_abstraction`: Index of the source abstraction (e.g., `0 # AbstractionName1`)
    - `to_abstraction`: Index of the target abstraction (e.g., `1 # AbstractionName2`)
    - `label`: A brief label for the interaction **in just a few words**{lang_hint} (e.g., "Manages", "Inherits", "Uses").
    Ideally the relationship should be backed by one abstraction calling or passing parameters to another.
    Simplify the relationship and exclude those non-important ones.

IMPORTANT: Make sure EVERY abstraction is involved in at least ONE relationship (either as source or target). Each abstraction index must appear at least once across all relationships.

Format the output as YAML:

```yaml
summary: |
  A brief, simple explanation of the project{lang_hint}.
  Can span multiple lines with **bold** and *italic* for emphasis.
relationships:
  - from_abstraction: 0 # AbstractionName1
    to_abstraction: 1 # AbstractionName2
    label: "Manages"{lang_hint}
  - from_abstraction: 2 # AbstractionName3
    to_abstraction: 0 # AbstractionName1
    label: "Provides config"{lang_hint}
  # ... other relationships
```

Now, provide the YAML output:
"""
        # Check prompt size before sending
        is_within_limit, token_count, token_limit = check_prompt_size(prompt)
        
        if not is_within_limit:
            print(f"Warning: Prompt exceeds token limit ({token_count} > {token_limit}). Truncating...")
            # Send SSE update about truncation
            if hasattr(self, '_shared') and 'sse_callback' in self._shared:
                callback = self._shared['sse_callback']
                callback("node_progress", {
                    "node": "AnalyzeRelationships",
                    "status": "warning",
                    "message": f"Large context detected. Truncating to fit token limits."
                })
            
            prompt = truncate_prompt(prompt, token_limit)
        
        response = call_llm(prompt, use_cache=(use_cache and self.cur_retry == 0)) # Use cache only if enabled and not retrying

        # --- Validation ---
        yaml_str = response.strip().split("```yaml")[1].split("```")[0].strip()
        relationships_data = yaml.safe_load(yaml_str)

        if not isinstance(relationships_data, dict) or not all(
            k in relationships_data for k in ["summary", "relationships"]
        ):
            raise ValueError(
                "LLM output is not a dict or missing keys ('summary', 'relationships')"
            )
        if not isinstance(relationships_data["summary"], str):
            raise ValueError("summary is not a string")
        if not isinstance(relationships_data["relationships"], list):
            raise ValueError("relationships is not a list")

        # Validate relationships structure
        validated_relationships = []
        for rel in relationships_data["relationships"]:
            # Check for 'label' key
            if not isinstance(rel, dict) or not all(
                k in rel for k in ["from_abstraction", "to_abstraction", "label"]
            ):
                raise ValueError(
                    f"Missing keys (expected from_abstraction, to_abstraction, label) in relationship item: {rel}"
                )
            # Validate 'label' is a string
            if not isinstance(rel["label"], str):
                raise ValueError(f"Relationship label is not a string: {rel}")

            # Validate indices
            try:
                from_idx = int(str(rel["from_abstraction"]).split("#")[0].strip())
                to_idx = int(str(rel["to_abstraction"]).split("#")[0].strip())
                if not (
                    0 <= from_idx < num_abstractions and 0 <= to_idx < num_abstractions
                ):
                    raise ValueError(
                        f"Invalid index in relationship: from={from_idx}, to={to_idx}. Max index is {num_abstractions-1}."
                    )
                validated_relationships.append(
                    {
                        "from": from_idx,
                        "to": to_idx,
                        "label": rel["label"],  # Potentially translated label
                    }
                )
            except (ValueError, TypeError):
                raise ValueError(f"Could not parse indices from relationship: {rel}")

        print("Generated project summary and relationship details.")
        
        # Send SSE update
        if hasattr(self, '_shared') and 'sse_callback' in self._shared:
            callback = self._shared['sse_callback']
            callback("node_progress", {
                "node": "AnalyzeRelationships",
                "status": "completed",
                "message": f"Analyzed {len(validated_relationships)} relationships",
                "data": {
                    "num_relationships": len(validated_relationships)
                }
            })
        
        return {
            "summary": relationships_data["summary"],  # Potentially translated summary
            "details": validated_relationships,  # Store validated, index-based relationships with potentially translated labels
        }

    def post(self, shared, prep_res, exec_res):
        # Structure is now {"summary": str, "details": [{"from": int, "to": int, "label": str}]}
        # Summary and label might be translated
        shared["relationships"] = exec_res