from pocketflow import BatchNode
from src.utils.call_llm import call_llm
from .identify_abstractions import get_content_for_indices
from src.utils.token_counter import (
    check_prompt_size,
    truncate_prompt,
    estimate_tokens,
    DEFAULT_MAX_CONTEXT_TOKENS
)


class WriteChapters(BatchNode):
    def prep(self, shared):
        # Store shared context for SSE callbacks
        self._shared = shared
        
        chapter_order = shared["chapter_order"]  # List of indices
        abstractions = shared[
            "abstractions"
        ]  # List of {"name": str, "description": str, "files": [int]}
        files_data = shared["files"]  # List of (path, content) tuples
        project_name = shared["project_name"]
        language = shared.get("language", "english")
        use_cache = shared.get("use_cache", True)  # Get use_cache flag, default to True

        # Get already written chapters to provide context
        # We store them temporarily during the batch run, not in shared memory yet
        # The 'previous_chapters_summary' will be built progressively in the exec context
        self.chapters_written_so_far = (
            []
        )  # Use instance variable for temporary storage across exec calls

        # Create a complete list of all chapters
        all_chapters = []
        chapter_filenames = {}  # Store chapter filename mapping for linking
        for i, abstraction_index in enumerate(chapter_order):
            if 0 <= abstraction_index < len(abstractions):
                chapter_num = i + 1
                chapter_name = abstractions[abstraction_index][
                    "name"
                ]  # Potentially translated name
                # Create safe filename (from potentially translated name)
                safe_name = "".join(
                    c if c.isalnum() else "_" for c in chapter_name
                ).lower()
                filename = f"{i+1:02d}_{safe_name}.md"
                # Format with link (using potentially translated name)
                all_chapters.append(f"{chapter_num}. [{chapter_name}]({filename})")
                # Store mapping of chapter index to filename for linking
                chapter_filenames[abstraction_index] = {
                    "num": chapter_num,
                    "name": chapter_name,
                    "filename": filename,
                }

        # Create a formatted string with all chapters
        full_chapter_listing = "\n".join(all_chapters)

        items_to_process = []
        for i, abstraction_index in enumerate(chapter_order):
            if 0 <= abstraction_index < len(abstractions):
                abstraction_details = abstractions[
                    abstraction_index
                ]  # Contains potentially translated name/desc
                # Use 'files' (list of indices) directly
                related_file_indices = abstraction_details.get("files", [])
                # Get content using helper, passing indices
                related_files_content_map = get_content_for_indices(
                    files_data, related_file_indices
                )

                # Get previous chapter info for transitions (uses potentially translated name)
                prev_chapter = None
                if i > 0:
                    prev_idx = chapter_order[i - 1]
                    prev_chapter = chapter_filenames[prev_idx]

                # Get next chapter info for transitions (uses potentially translated name)
                next_chapter = None
                if i < len(chapter_order) - 1:
                    next_idx = chapter_order[i + 1]
                    next_chapter = chapter_filenames[next_idx]

                items_to_process.append(
                    {
                        "chapter_num": i + 1,
                        "abstraction_index": abstraction_index,
                        "abstraction_details": abstraction_details,  # Has potentially translated name/desc
                        "related_files_content_map": related_files_content_map,
                        "project_name": shared["project_name"],  # Add project name
                        "full_chapter_listing": full_chapter_listing,  # Add the full chapter listing (uses potentially translated names)
                        "chapter_filenames": chapter_filenames,  # Add chapter filenames mapping (uses potentially translated names)
                        "prev_chapter": prev_chapter,  # Add previous chapter info (uses potentially translated name)
                        "next_chapter": next_chapter,  # Add next chapter info (uses potentially translated name)
                        "language": language,  # Add language for multi-language support
                        "use_cache": use_cache, # Pass use_cache flag
                        # previous_chapters_summary will be added dynamically in exec
                    }
                )
            else:
                print(
                    f"Warning: Invalid abstraction index {abstraction_index} in chapter_order. Skipping."
                )

        print(f"Preparing to write {len(items_to_process)} chapters...")
        
        # Send SSE update
        if 'sse_callback' in shared:
            callback = shared['sse_callback']
            callback("node_progress", {
                "node": "WriteChapters",
                "status": "starting",
                "message": f"Starting to write {len(items_to_process)} tutorial chapters..."
            })
        
        return items_to_process  # Iterable for BatchNode

    def exec(self, item):
        # This runs for each item prepared above
        abstraction_name = item["abstraction_details"][
            "name"
        ]  # Potentially translated name
        abstraction_description = item["abstraction_details"][
            "description"
        ]  # Potentially translated description
        chapter_num = item["chapter_num"]
        project_name = item.get("project_name")
        language = item.get("language", "english")
        use_cache = item.get("use_cache", True) # Read use_cache from item
        print(f"Writing chapter {chapter_num} for: {abstraction_name} using LLM...")
        
        # Send SSE update for chapter start
        if hasattr(self, '_shared') and 'sse_callback' in self._shared:
            callback = self._shared['sse_callback']
            total_chapters = len(self._shared.get("chapter_order", []))
            callback("node_progress", {
                "node": "WriteChapters",
                "status": "progress",
                "message": f"Writing chapter {chapter_num}/{total_chapters}: {abstraction_name}",
                "data": {
                    "current_chapter": chapter_num,
                    "total_chapters": total_chapters,
                    "chapter_name": abstraction_name
                }
            })

        # Get summary of chapters written *before* this one
        # Use the temporary instance variable
        previous_chapters_summary = "\n---\n".join(self.chapters_written_so_far)

        # Add language instruction and context notes only if not English
        language_instruction = ""
        concept_details_note = ""
        structure_note = ""
        prev_summary_note = ""
        instruction_lang_note = ""
        mermaid_lang_note = ""
        code_comment_note = ""
        link_lang_note = ""
        tone_note = ""
        if language.lower() != "english":
            lang_cap = language.capitalize()
            language_instruction = f"IMPORTANT: Write this ENTIRE tutorial chapter in **{lang_cap}**. Some input context (like concept name, description, chapter list, previous summary) might already be in {lang_cap}, but you MUST translate ALL other generated content including explanations, examples, technical terms, and potentially code comments into {lang_cap}. DO NOT use English anywhere except in code syntax, required proper nouns, or when specified. The entire output MUST be in {lang_cap}.\n\n"
            concept_details_note = f" (Note: Provided in {lang_cap})"
            structure_note = f" (Note: Chapter names might be in {lang_cap})"
            prev_summary_note = f" (Note: This summary might be in {lang_cap})"
            instruction_lang_note = f" (in {lang_cap})"
            mermaid_lang_note = f" (Use {lang_cap} for labels/text if appropriate)"
            code_comment_note = f" (Translate to {lang_cap} if possible, otherwise keep minimal English for clarity)"
            link_lang_note = (
                f" (Use the {lang_cap} chapter title from the structure above)"
            )
            tone_note = f" (appropriate for {lang_cap} readers)"

        # Prepare file context string from the map with token awareness
        max_context_tokens = DEFAULT_MAX_CONTEXT_TOKENS
        
        # Estimate tokens for the base prompt without file context
        base_prompt_estimate = estimate_tokens(f"""
{language_instruction}Write a very beginner-friendly tutorial chapter...
{abstraction_name}
{abstraction_description}
{item["full_chapter_listing"]}
{previous_chapters_summary if previous_chapters_summary else "This is the first chapter."}
        """)
        
        # Calculate remaining tokens for file context
        available_for_files = max_context_tokens - base_prompt_estimate - 10000  # Reserve buffer
        
        # Build file context with token limits
        file_context_parts = []
        current_tokens = 0
        
        for idx_path, content in item["related_files_content_map"].items():
            file_path = idx_path.split('# ')[1] if '# ' in idx_path else idx_path
            file_entry = f"--- File: {file_path} ---\n{content}\n\n"
            entry_tokens = estimate_tokens(file_entry)
            
            if current_tokens + entry_tokens <= available_for_files:
                file_context_parts.append(file_entry)
                current_tokens += entry_tokens
            else:
                # Try to include truncated version
                remaining = available_for_files - current_tokens
                if remaining > 500:
                    chars_to_include = remaining * 3
                    truncated_content = content[:chars_to_include]
                    # Find good breakpoint
                    last_newline = truncated_content.rfind('\n')
                    if last_newline > chars_to_include * 0.7:
                        truncated_content = truncated_content[:last_newline]
                    file_context_parts.append(
                        f"--- File: {file_path} ---\n{truncated_content}\n\n[... truncated due to length ...]\n\n"
                    )
                break
        
        file_context_str = "".join(file_context_parts)
        
        if len(file_context_parts) < len(item["related_files_content_map"]):
            print(f"Note: Some file content truncated for chapter {chapter_num} due to token limits")

        prompt = f"""
{language_instruction}Write a very beginner-friendly tutorial chapter (in Markdown format) for the project `{project_name}` about the concept: "{abstraction_name}". This is Chapter {chapter_num}.

Concept Details{concept_details_note}:
- Name: {abstraction_name}
- Description:
{abstraction_description}

Complete Tutorial Structure{structure_note}:
{item["full_chapter_listing"]}

Context from previous chapters{prev_summary_note}:
{previous_chapters_summary if previous_chapters_summary else "This is the first chapter."}

Relevant Code Snippets (Code itself remains unchanged):
{file_context_str if file_context_str else "No specific code snippets provided for this abstraction."}

Instructions for the chapter (Generate content in {language.capitalize()} unless specified otherwise):
- Start with a clear heading (e.g., `# Chapter {chapter_num}: {abstraction_name}`). Use the provided concept name.

- If this is not the first chapter, begin with a brief transition from the previous chapter{instruction_lang_note}, referencing it with a proper Markdown link using its name{link_lang_note}.

- Begin with a high-level motivation explaining what problem this abstraction solves{instruction_lang_note}. Start with a central use case as a concrete example. The whole chapter should guide the reader to understand how to solve this use case. Make it very minimal and friendly to beginners.

- If the abstraction is complex, break it down into key concepts. Explain each concept one-by-one in a very beginner-friendly way{instruction_lang_note}.

- Explain how to use this abstraction to solve the use case{instruction_lang_note}. Give example inputs and outputs for code snippets (if the output isn't values, describe at a high level what will happen{instruction_lang_note}).

- Each code block should be BELOW 10 lines! If longer code blocks are needed, break them down into smaller pieces and walk through them one-by-one. Aggresively simplify the code to make it minimal. Use comments{code_comment_note} to skip non-important implementation details. Each code block should have a beginner friendly explanation right after it{instruction_lang_note}.

- Describe the internal implementation to help understand what's under the hood{instruction_lang_note}. First provide a non-code or code-light walkthrough on what happens step-by-step when the abstraction is called{instruction_lang_note}. It's recommended to use a simple sequenceDiagram with a dummy example - keep it minimal with at most 5 participants to ensure clarity. If participant name has space, use: `participant QP as Query Processing`. {mermaid_lang_note}.

- Then dive deeper into code for the internal implementation with references to files. Provide example code blocks, but make them similarly simple and beginner-friendly. Explain{instruction_lang_note}.

- IMPORTANT: When you need to refer to other core abstractions covered in other chapters, ALWAYS use proper Markdown links like this: [Chapter Title](filename.md). Use the Complete Tutorial Structure above to find the correct filename and the chapter title{link_lang_note}. Translate the surrounding text.

- Use mermaid diagrams to illustrate complex concepts (```mermaid``` format). {mermaid_lang_note}.

- Heavily use analogies and examples throughout{instruction_lang_note} to help beginners understand.

- End the chapter with a brief conclusion that summarizes what was learned{instruction_lang_note} and provides a transition to the next chapter{instruction_lang_note}. If there is a next chapter, use a proper Markdown link: [Next Chapter Title](next_chapter_filename){link_lang_note}.

- Ensure the tone is welcoming and easy for a newcomer to understand{tone_note}.

- Output *only* the Markdown content for this chapter.

Now, directly provide a super beginner-friendly Markdown output (DON'T need ```markdown``` tags):
"""
        # Check prompt size before sending
        is_within_limit, token_count, token_limit = check_prompt_size(prompt)
        
        if not is_within_limit:
            print(f"Warning: Chapter {chapter_num} prompt exceeds token limit ({token_count} > {token_limit}). Truncating...")
            # Send SSE update about truncation
            if hasattr(self, '_shared') and 'sse_callback' in self._shared:
                callback = self._shared['sse_callback']
                callback("node_progress", {
                    "node": "WriteChapters",
                    "status": "warning",
                    "message": f"Chapter {chapter_num} content truncated to fit token limits"
                })
            
            # Try to preserve structure by truncating file context first
            if file_context_str and len(file_context_str) > 1000:
                # Reduce file context
                max_file_chars = int(len(file_context_str) * 0.5)
                file_context_str = file_context_str[:max_file_chars] + "\n\n[... additional files truncated ...]"
                # Rebuild prompt with shorter file context
                prompt = prompt.replace(
                    item["related_files_content_map"], 
                    file_context_str
                )
            
            # If still too long, truncate the whole prompt
            if not check_prompt_size(prompt)[0]:
                prompt = truncate_prompt(prompt, token_limit)
        
        chapter_content = call_llm(prompt, use_cache=(use_cache and self.cur_retry == 0)) # Use cache only if enabled and not retrying
        # Basic validation/cleanup
        actual_heading = f"# Chapter {chapter_num}: {abstraction_name}"  # Use potentially translated name
        if not chapter_content.strip().startswith(f"# Chapter {chapter_num}"):
            # Add heading if missing or incorrect, trying to preserve content
            lines = chapter_content.strip().split("\n")
            if lines and lines[0].strip().startswith(
                "#"
            ):  # If there's some heading, replace it
                lines[0] = actual_heading
                chapter_content = "\n".join(lines)
            else:  # Otherwise, prepend it
                chapter_content = f"{actual_heading}\n\n{chapter_content}"

        # Add the generated content to our temporary list for the next iteration's context
        self.chapters_written_so_far.append(chapter_content)

        return chapter_content  # Return the Markdown string (potentially translated)

    def post(self, shared, prep_res, exec_res_list):
        # exec_res_list contains the generated Markdown for each chapter, in order
        shared["chapters"] = exec_res_list
        # Clean up the temporary instance variable
        del self.chapters_written_so_far
        print(f"Finished writing {len(exec_res_list)} chapters.")
        
        # Send SSE update
        if 'sse_callback' in shared:
            callback = shared['sse_callback']
            callback("node_progress", {
                "node": "WriteChapters",
                "status": "completed",
                "message": f"Successfully wrote {len(exec_res_list)} tutorial chapters"
            })