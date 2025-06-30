import yaml
from pocketflow import Node
from src.utils.call_llm import call_llm
from src.utils.token_counter import (
    truncate_context, 
    check_prompt_size, 
    truncate_prompt,
    DEFAULT_MAX_CONTEXT_TOKENS
)


class IdentifyAbstractions(Node):
    def prep(self, shared):
        # Store shared context for SSE callbacks
        self._shared = shared
        
        files_data = shared["files"]
        project_name = shared["project_name"]  # Get project name
        language = shared.get("language", "english")  # Get language
        use_cache = shared.get("use_cache", True)  # Get use_cache flag, default to True
        max_abstraction_num = shared.get("max_abstraction_num", 10)  # Get max_abstraction_num, default to 10

        # Helper to create context from files, respecting limits
        def create_llm_context(files_data, max_tokens=DEFAULT_MAX_CONTEXT_TOKENS):
            # First, collect all files into a dict for token counting
            file_contents = {}
            for i, (path, content) in enumerate(files_data):
                file_contents[f"{i}|{path}"] = content
            
            # Prioritize certain file types for better abstraction identification
            priority_patterns = [
                'main.', 'app.', 'index.',  # Entry points
                'config', 'settings',        # Configuration
                '.md', 'README',             # Documentation
                'schema', 'model',           # Data models
                'route', 'controller',       # API/Web layers
                'service', 'manager',        # Business logic
                'test', 'spec'               # Tests
            ]
            
            # Truncate to fit token limit
            truncated_contents, total_tokens = truncate_context(
                file_contents, 
                max_tokens=max_tokens - 5000,  # Reserve tokens for prompt template
                prioritize_files=priority_patterns
            )
            
            # Build context and file info from truncated content
            context = ""
            file_info = []  # Store tuples of (index, path)
            
            # Sort by original index to maintain order
            sorted_items = sorted(
                truncated_contents.items(), 
                key=lambda x: int(x[0].split('|')[0])
            )
            
            for key, content in sorted_items:
                idx_str, path = key.split('|', 1)
                idx = int(idx_str)
                entry = f"--- File Index {idx}: {path} ---\n{content}\n\n"
                context += entry
                file_info.append((idx, path))
            
            print(f"Context includes {len(file_info)} of {len(files_data)} files (approx {total_tokens} tokens)")
            if len(file_info) < len(files_data):
                print(f"Note: {len(files_data) - len(file_info)} files were excluded due to token limits")

            return context, file_info  # file_info is list of (index, path)

        # Get max tokens from shared context or use default
        max_context_tokens = shared.get("max_context_tokens", DEFAULT_MAX_CONTEXT_TOKENS)
        context, file_info = create_llm_context(files_data, max_context_tokens)
        # Format file info for the prompt (comment is just a hint for LLM)
        file_listing_for_prompt = "\n".join(
            [f"- {idx} # {path}" for idx, path in file_info]
        )
        
        # Store shared context for SSE callbacks
        self._shared = shared
        
        return (
            context,
            file_listing_for_prompt,
            len(files_data),
            project_name,
            language,
            use_cache,
            max_abstraction_num,
            file_info,  # Added file_info to return tuple
        )  # Return all parameters

    def exec(self, prep_res):
        (
            context,
            file_listing_for_prompt,
            file_count,
            project_name,
            language,
            use_cache,
            max_abstraction_num,
            file_info,  # Added file_info to unpacking
        ) = prep_res  # Unpack all parameters
        print(f"Identifying abstractions using LLM...")
        
        # Send SSE update
        if hasattr(self, '_shared') and 'sse_callback' in self._shared:
            callback = self._shared['sse_callback']
            callback("node_progress", {
                "node": "IdentifyAbstractions",
                "status": "starting",
                "message": "Analyzing codebase to identify key abstractions..."
            })

        # Add language instruction and hints only if not English
        language_instruction = ""
        name_lang_hint = ""
        desc_lang_hint = ""
        if language.lower() != "english":
            language_instruction = f"IMPORTANT: Generate the `name` and `description` for each abstraction in **{language.capitalize()}** language. Do NOT use English for these fields.\n\n"
            # Keep specific hints here as name/description are primary targets
            name_lang_hint = f" (value in {language.capitalize()})"
            desc_lang_hint = f" (value in {language.capitalize()})"

        # CRITICAL: Format instructions MUST come first to survive truncation
        format_instructions = f"""IMPORTANT: You MUST respond with a YAML list. Do NOT return code snippets or any other format.

Format the output as a YAML list of dictionaries:

```yaml
- name: |
    Query Processing{name_lang_hint}
  description: |
    Explains what the abstraction does.
    It's like a central dispatcher routing requests.{desc_lang_hint}
  file_indices:
    - 0 # path/to/file1.py
    - 3 # path/to/related.py
- name: |
    Query Optimization{name_lang_hint}
  description: |
    Another core concept, similar to a blueprint for objects.{desc_lang_hint}
  file_indices:
    - 5 # path/to/another.js
# ... up to {max_abstraction_num} abstractions
```

"""
        
        prompt = f"""{format_instructions}
For the project `{project_name}`:

TASK: {language_instruction}Analyze the codebase and identify the top 5-{max_abstraction_num} core abstractions.

For each abstraction, provide:
1. A concise `name`{name_lang_hint}.
2. A beginner-friendly `description` explaining what it is with a simple analogy, in around 100 words{desc_lang_hint}.
3. A list of relevant `file_indices` (integers) using the format `idx # path/comment`.

List of file indices and paths present in the context:
{file_listing_for_prompt}

Codebase Context:
{context}

Remember: Respond ONLY with the YAML format shown above. Do NOT include code snippets from the files."""
        # Check prompt size before sending
        is_within_limit, token_count, token_limit = check_prompt_size(prompt)
        
        if not is_within_limit:
            print(f"Warning: Prompt exceeds token limit ({token_count} > {token_limit}). Truncating...")
            # Send SSE update about truncation
            if hasattr(self, '_shared') and 'sse_callback' in self._shared:
                callback = self._shared['sse_callback']
                callback("node_progress", {
                    "node": "IdentifyAbstractions",
                    "status": "warning",
                    "message": f"Large codebase detected. Processing {len(file_info)} of {file_count} files to fit token limits."
                })
            
            prompt = truncate_prompt(prompt, token_limit)
        
        response = call_llm(prompt, use_cache=(use_cache and self.cur_retry == 0))  # Use cache only if enabled and not retrying

        # --- Validation ---
        # Extract YAML content more robustly
        yaml_str = None
        
        # Check if response contains code that's not YAML
        if response.strip().startswith(("def ", "class ", "function ", "const ", "var ", "import ")):
            raise ValueError(f"LLM returned code instead of YAML format. This often happens with large codebases. Response start: {response[:200]}...")
        
        # Try to find YAML block with ```yaml markers
        if "```yaml" in response and "```" in response.split("```yaml", 1)[1]:
            yaml_str = response.strip().split("```yaml")[1].split("```")[0].strip()
        # Try to find any code block
        elif "```" in response:
            parts = response.split("```")
            if len(parts) >= 3:  # At least one complete code block
                yaml_str = parts[1].strip()
                # Remove language identifier if present
                if yaml_str.startswith(("yaml", "yml")):
                    yaml_str = yaml_str.split("\n", 1)[1] if "\n" in yaml_str else ""
        # Last resort: try the entire response
        else:
            yaml_str = response.strip()
        
        if not yaml_str:
            raise ValueError("No YAML content found in LLM response")
        
        try:
            abstractions = yaml.safe_load(yaml_str)
        except yaml.YAMLError as e:
            # If YAML parsing fails and content looks like code, provide better error
            if any(keyword in yaml_str[:200] for keyword in ["def ", "class ", "function ", "import ", "const "]):
                raise ValueError(f"LLM returned code instead of YAML abstractions. The model may be confused by the large codebase context.")
            raise ValueError(f"Failed to parse YAML: {e}\nYAML content: {yaml_str[:200]}...")

        if not isinstance(abstractions, list):
            raise ValueError("LLM Output is not a list")

        validated_abstractions = []
        for item in abstractions:
            if not isinstance(item, dict) or not all(
                k in item for k in ["name", "description", "file_indices"]
            ):
                raise ValueError(f"Missing keys in abstraction item: {item}")
            if not isinstance(item["name"], str):
                raise ValueError(f"Name is not a string in item: {item}")
            if not isinstance(item["description"], str):
                raise ValueError(f"Description is not a string in item: {item}")
            if not isinstance(item["file_indices"], list):
                raise ValueError(f"file_indices is not a list in item: {item}")

            # Validate indices
            validated_indices = []
            for idx_entry in item["file_indices"]:
                try:
                    if isinstance(idx_entry, int):
                        idx = idx_entry
                    elif isinstance(idx_entry, str) and "#" in idx_entry:
                        idx = int(idx_entry.split("#")[0].strip())
                    else:
                        idx = int(str(idx_entry).strip())

                    if not (0 <= idx < file_count):
                        raise ValueError(
                            f"Invalid file index {idx} found in item {item['name']}. Max index is {file_count - 1}."
                        )
                    validated_indices.append(idx)
                except (ValueError, TypeError):
                    raise ValueError(
                        f"Could not parse index from entry: {idx_entry} in item {item['name']}"
                    )

            item["files"] = sorted(list(set(validated_indices)))
            # Store only the required fields
            validated_abstractions.append(
                {
                    "name": item["name"],  # Potentially translated name
                    "description": item[
                        "description"
                    ],  # Potentially translated description
                    "files": item["files"],
                }
            )

        print(f"Identified {len(validated_abstractions)} abstractions.")
        
        # Send SSE update
        if hasattr(self, '_shared') and 'sse_callback' in self._shared:
            callback = self._shared['sse_callback']
            callback("node_progress", {
                "node": "IdentifyAbstractions",
                "status": "completed",
                "message": f"Identified {len(validated_abstractions)} key abstractions",
                "data": {
                    "abstractions": [a["name"] for a in validated_abstractions]
                }
            })
        
        return validated_abstractions

    def post(self, shared, prep_res, exec_res):
        shared["abstractions"] = (
            exec_res  # List of {"name": str, "description": str, "files": [int]}
        )


# Helper to get content for specific file indices
def get_content_for_indices(files_data, indices):
    content_map = {}
    for i in indices:
        if 0 <= i < len(files_data):
            path, content = files_data[i]
            content_map[f"{i} # {path}"] = (
                content  # Use index + path as key for context
            )
    return content_map