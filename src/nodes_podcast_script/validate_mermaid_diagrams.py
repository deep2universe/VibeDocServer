from pocketflow import Node
from typing import Dict, List, Tuple, Optional
import json
import os
import subprocess
import tempfile
import re
from datetime import datetime
from src.utils.call_llm import call_llm
from src.utils.call_llm_with_logging import call_llm_with_logging
import yaml
from src.utils.token_counter import (
    check_prompt_size,
    truncate_prompt,
    estimate_tokens,
    DEFAULT_MAX_CONTEXT_TOKENS
)


class ValidateMermaidDiagrams(Node):
    """
    Validates all Mermaid diagrams in the final podcast JSON.
    Attempts to fix invalid diagrams via LLM, and converts to Markdown as fallback.
    Creates a new JSON file with all corrections applied.
    """
    
    def prep(self, shared: Dict) -> Tuple[Dict, str, str]:
        """Load the final podcast JSON and prepare for validation."""
        podcast_result = shared["podcast_result"]
        output_path = podcast_result["output_path"]
        task_id = shared.get("task_id", "")
        
        # Load the final JSON
        with open(output_path, 'r', encoding='utf-8') as f:
            podcast_data = json.load(f)
        
        # Store shared context for exec
        self.shared_context = shared
        
        return podcast_data, output_path, task_id
    
    def exec(self, inputs: Tuple[Dict, str, str]) -> Dict:
        """Validate and correct all Mermaid diagrams."""
        podcast_data, original_path, task_id = inputs
        
        # Extract all Mermaid diagrams with context
        mermaid_diagrams = self._extract_all_mermaid_diagrams(podcast_data)
        
        if not mermaid_diagrams:
            # No Mermaid diagrams found
            return {
                "status": "no_mermaid_found",
                "output_path": original_path,
                "corrections_count": 0
            }
        
        # Log initial extraction
        if self.shared_context.get("logging_enabled"):
            from src.utils.podcast_logger import PodcastLogger
            logger = PodcastLogger(self.shared_context.get("task_id"))
            logger.log_node_start(
                "ValidateMermaidDiagrams - Extraction",
                {
                    "total_mermaid_diagrams": len(mermaid_diagrams),
                    "diagram_ids": [d["id"] for d in mermaid_diagrams]
                }
            )
        
        # Log progress
        progress_callback = self.shared_context.get("progress_callback")
        if progress_callback and callable(progress_callback):
            progress_callback(
                "validate_mermaid",
                f"Validating {len(mermaid_diagrams)} Mermaid diagrams"
            )
        
        # Phase 1: Validate all diagrams
        validation_results = []
        for diagram in mermaid_diagrams:
            is_valid, error = self._validate_with_mmdc(diagram["content"])
            validation_results.append({
                **diagram,
                "valid": is_valid,
                "error": error
            })
            
            # Log each validation result
            if self.shared_context.get("logging_enabled") and not is_valid:
                logger.log_warning(
                    "ValidateMermaidDiagrams",
                    f"Diagram {diagram['id']} validation failed: {error}"
                )
        
        # Collect failed diagrams
        failed_diagrams = [d for d in validation_results if not d["valid"]]
        
        # Log validation summary
        if self.shared_context.get("logging_enabled"):
            logger.log_node_start(
                "ValidateMermaidDiagrams - Phase 1 Summary",
                {
                    "total_validated": len(validation_results),
                    "valid_count": len(validation_results) - len(failed_diagrams),
                    "failed_count": len(failed_diagrams),
                    "failed_ids": [d["id"] for d in failed_diagrams]
                }
            )
        
        if not failed_diagrams:
            # All diagrams are valid
            return {
                "status": "all_valid",
                "output_path": original_path,
                "corrections_count": 0
            }
        
        # Phase 2: LLM correction attempt
        if progress_callback and callable(progress_callback):
            progress_callback(
                "validate_mermaid",
                f"Correcting {len(failed_diagrams)} invalid Mermaid diagrams"
            )
        
        corrected_diagrams = self._correct_diagrams_with_llm(failed_diagrams)
        
        # Log LLM correction results
        if self.shared_context.get("logging_enabled"):
            logger.log_node_start(
                "ValidateMermaidDiagrams - LLM Correction Results",
                {
                    "total_sent_for_correction": len(failed_diagrams),
                    "total_corrections_received": len(corrected_diagrams),
                    "correction_ids": list(corrected_diagrams.keys())
                }
            )
        
        # Phase 3: Validate corrected diagrams
        still_failed = []
        successfully_corrected = {}
        
        # Check if we got corrections for all failed diagrams
        for failed_diagram in failed_diagrams:
            diagram_id = failed_diagram["id"]
            
            if diagram_id not in corrected_diagrams:
                # No correction received from LLM
                if self.shared_context.get("logging_enabled"):
                    logger.log_warning(
                        "ValidateMermaidDiagrams",
                        f"No correction received from LLM for diagram {diagram_id}"
                    )
                still_failed.append(failed_diagram)
                continue
            
            corrected_content = corrected_diagrams[diagram_id]
            is_valid, error = self._validate_with_mmdc(corrected_content)
            
            if is_valid:
                successfully_corrected[diagram_id] = corrected_content
                if self.shared_context.get("logging_enabled"):
                    logger.log_node_start(
                        f"ValidateMermaidDiagrams - Correction Success",
                        {"diagram_id": diagram_id}
                    )
            else:
                # Correction still failed
                still_failed.append({
                    **failed_diagram,
                    "corrected_content": corrected_content,
                    "correction_error": error
                })
                if self.shared_context.get("logging_enabled"):
                    logger.log_warning(
                        "ValidateMermaidDiagrams",
                        f"Corrected diagram {diagram_id} still failed: {error}"
                    )
        
        # Log Phase 3 summary
        if self.shared_context.get("logging_enabled"):
            logger.log_node_start(
                "ValidateMermaidDiagrams - Phase 3 Summary",
                {
                    "successfully_corrected": len(successfully_corrected),
                    "still_failed": len(still_failed),
                    "still_failed_ids": [d["id"] for d in still_failed]
                }
            )
        
        # Phase 4: Convert still-failed diagrams to Markdown
        markdown_conversions = {}
        if still_failed:
            if progress_callback and callable(progress_callback):
                progress_callback(
                    "validate_mermaid",
                    f"Converting {len(still_failed)} diagrams to Markdown"
                )
            
            markdown_conversions = self._convert_to_markdown(still_failed)
            
            # Log markdown conversion results
            if self.shared_context.get("logging_enabled"):
                logger.log_node_start(
                    "ValidateMermaidDiagrams - Markdown Conversion Results",
                    {
                        "total_converted": len(markdown_conversions),
                        "converted_ids": list(markdown_conversions.keys())
                    }
                )
        
        # Phase 5: Apply all corrections and create new JSON
        new_podcast_data = self._apply_all_corrections(
            podcast_data,
            successfully_corrected,
            markdown_conversions
        )
        
        # Save corrected JSON with special name
        new_path = self._save_corrected_json(new_podcast_data, original_path)
        
        # Log final results
        total_corrections = len(successfully_corrected) + len(markdown_conversions)
        if progress_callback and callable(progress_callback):
            progress_callback(
                "validate_mermaid",
                f"Validation complete: {len(successfully_corrected)} fixed, "
                f"{len(markdown_conversions)} converted to Markdown"
            )
        
        return {
            "status": "corrected",
            "output_path": new_path,
            "corrections_count": total_corrections,
            "mermaid_fixed": len(successfully_corrected),
            "converted_to_markdown": len(markdown_conversions)
        }
    
    def _extract_all_mermaid_diagrams(self, podcast_data: Dict) -> List[Dict]:
        """Extract all Mermaid diagrams with their context."""
        diagrams = []
        
        for cluster in podcast_data.get('clusters', []):
            for dialogue in cluster.get('dialogues', []):
                if 'visualization' in dialogue:
                    viz = dialogue['visualization']
                    if viz.get('type') == 'mermaid':
                        diagrams.append({
                            'id': f"{cluster['cluster_id']}_dialogue_{dialogue['dialogue_id']}",
                            'content': viz['content'],
                            'cluster_id': cluster['cluster_id'],
                            'dialogue_id': dialogue['dialogue_id'],
                            'context': dialogue['text'],
                            'cluster_title': cluster['cluster_title'],
                            'speaker': dialogue.get('speaker', '')
                        })
        
        return diagrams
    
    def _validate_with_mmdc(self, mermaid_content: str) -> Tuple[bool, Optional[str]]:
        """Validate a Mermaid diagram using mmdc CLI."""
        # Check if mmdc is available
        mmdc_available = subprocess.run(
            ['which', 'mmdc'],
            capture_output=True,
            text=True
        ).returncode == 0
        
        if not mmdc_available:
            # Mock validation for testing when mmdc is not installed
            # Log that we're using mock validation
            if self.shared_context.get("logging_enabled"):
                from src.utils.podcast_logger import PodcastLogger
                logger = PodcastLogger(self.shared_context.get("task_id"))
                logger.log_warning(
                    "ValidateMermaidDiagrams",
                    "mmdc not available - using mock validation (less accurate)"
                )
            
            # More realistic validation - only catch obvious errors
            lines = mermaid_content.strip().split('\n')
            
            # Count brackets on each line
            bracket_count = 0
            for i, line in enumerate(lines):
                # Skip comment lines
                if line.strip().startswith('%%'):
                    continue
                
                # Count opening and closing brackets
                bracket_count += line.count('[') - line.count(']')
                
                # Check for obvious unclosed bracket on a single line
                # Only flag if it looks like node definition with arrow
                if '[' in line and ']' not in line and '-->' in line:
                    # Check if it's really a bracket issue (not inside quotes)
                    before_bracket = line.split('[')[0].strip()
                    after_bracket = line.split('[')[1] if '[' in line else ""
                    
                    # If there's an arrow immediately after unclosed bracket, it's an error
                    if '-->' in after_bracket and ']' not in after_bracket.split('-->')[0]:
                        return False, f"Parse error on line {i+1}: Unclosed bracket"
            
            # Overall bracket mismatch
            if bracket_count != 0:
                return False, f"Parse error: Mismatched brackets (off by {abs(bracket_count)})"
            
            # Check sequence diagram specific errors
            if 'sequenceDiagram' in mermaid_content:
                for i, line in enumerate(lines):
                    line = line.strip()
                    # Check for obvious wrong arrow syntax
                    if '-->>' in line:
                        return False, f"Parse error on line {i+1}: Invalid arrow syntax '-->>' in sequence diagram"
                    
                    # Check participant declarations
                    if line.startswith('participant ') and ' as ' not in line:
                        parts = line.split()
                        if len(parts) > 2:
                            return False, f"Parse error on line {i+1}: Missing 'as' in participant declaration"
            
            # Check for Note syntax in sequence diagrams
            for i, line in enumerate(lines):
                line = line.strip()
                if line.startswith('Note ') and ':' not in line:
                    return False, f"Parse error on line {i+1}: Missing colon after 'Note'"
            
            # If no obvious errors found, assume it's valid
            return True, None
        
        # Create temporary files
        with tempfile.NamedTemporaryFile(mode='w', suffix='.mmd', delete=False) as input_file:
            input_file.write(mermaid_content)
            input_path = input_file.name
        
        output_path = tempfile.mktemp(suffix='.svg')
        
        try:
            # Find the global mmdc path
            mmdc_path_result = subprocess.run(
                ['which', 'mmdc'],
                capture_output=True,
                text=True
            )
            
            if mmdc_path_result.returncode == 0:
                mmdc_path = mmdc_path_result.stdout.strip()
            else:
                # Try common locations
                import os
                possible_paths = [
                    '/usr/local/bin/mmdc',
                    '/opt/homebrew/bin/mmdc',
                    os.path.expanduser('~/.npm-global/bin/mmdc'),
                    '/usr/bin/mmdc'
                ]
                mmdc_path = None
                for path in possible_paths:
                    if os.path.exists(path):
                        mmdc_path = path
                        break
                
                if not mmdc_path:
                    return False, "mmdc executable not found in PATH"
            
            # Run mmdc directly (not through node)
            result = subprocess.run(
                [mmdc_path,
                 '-i', input_path,
                 '-o', output_path],
                capture_output=True,
                text=True,
                timeout=10  # 10 second timeout as specified
            )
            
            if result.returncode == 0:
                return True, None
            else:
                # Extract error message
                error_msg = result.stderr
                
                # Try to extract specific error info
                if "Parse error on line" in error_msg:
                    line_match = re.search(r'Parse error on line (\d+):(.*?)(?=\n|$)', error_msg, re.DOTALL)
                    if line_match:
                        return False, f"Line {line_match.group(1)}: {line_match.group(2).strip()}"
                
                return False, error_msg.strip() if error_msg else "Unknown validation error"
                
        except subprocess.TimeoutExpired:
            return False, "Validation timeout - diagram too complex or contains infinite loops"
        except Exception as e:
            return False, f"Validation error: {str(e)}"
        finally:
            # Cleanup temporary files
            try:
                os.unlink(input_path)
                if os.path.exists(output_path):
                    os.unlink(output_path)
            except:
                pass
    
    def _correct_diagrams_with_llm(self, failed_diagrams: List[Dict]) -> Dict[str, str]:
        """Correct all failed Mermaid diagrams, batching if needed for token limits."""
        # Check if we need to batch due to token limits
        all_corrections = {}
        
        # Estimate tokens per diagram (rough estimate)
        avg_tokens_per_diagram = 500  # Conservative estimate
        base_prompt_tokens = 2000  # Template tokens
        max_diagrams_per_batch = (DEFAULT_MAX_CONTEXT_TOKENS - base_prompt_tokens - 5000) // avg_tokens_per_diagram
        
        # Process in batches if needed
        for i in range(0, len(failed_diagrams), max_diagrams_per_batch):
            batch = failed_diagrams[i:i + max_diagrams_per_batch]
            
            # Build comprehensive prompt for this batch
            prompt = self._build_correction_prompt(batch)
            
            # Check prompt size
            is_within_limit, token_count, token_limit = check_prompt_size(prompt)
            
            if not is_within_limit:
                # If still too large, reduce batch size
                if len(batch) > 1:
                    # Try with half the batch
                    half_size = len(batch) // 2
                    batch = batch[:half_size]
                    prompt = self._build_correction_prompt(batch)
                else:
                    # Single diagram still too large, truncate the context
                    print(f"Warning: Diagram correction prompt exceeds token limit. Truncating...")
                    prompt = truncate_prompt(prompt, token_limit)
            
            # Log batch processing
            if self.shared_context.get("logging_enabled"):
                from src.utils.podcast_logger import PodcastLogger
                logger = PodcastLogger(self.shared_context.get("task_id"))
                logger.log_node_start(
                    "ValidateMermaidDiagrams - Processing Batch",
                    {
                        "batch_number": i // max_diagrams_per_batch + 1,
                        "batch_size": len(batch),
                        "total_batches": (len(failed_diagrams) + max_diagrams_per_batch - 1) // max_diagrams_per_batch
                    }
                )
            
            # Call LLM with logging if enabled
            shared = self.shared_context
            if shared.get("logging_enabled") and shared.get("task_id"):
                response = call_llm_with_logging(
                    prompt=prompt,
                    node_name="ValidateMermaidDiagrams",
                    cluster_info={"action": "correct_mermaid", "batch_size": len(batch)},
                    task_id=shared.get("task_id")
                )
            else:
                response = call_llm(prompt)
            
            # Parse corrections and add to all_corrections
            batch_corrections = self._parse_corrections_yaml(response)
            all_corrections.update(batch_corrections)
        
        return all_corrections
    
    def _build_correction_prompt(self, failed_diagrams: List[Dict]) -> str:
        """Build prompt for correcting Mermaid diagrams."""
        prompt = """You are an expert in Mermaid diagram syntax.
I have several Mermaid diagrams with syntax errors that need to be fixed.

## Failed Mermaid Diagrams:

"""
        
        for diagram in failed_diagrams:
            prompt += f"""
### Diagram ID: {diagram['id']}
**Context**: Speaker {diagram['speaker']} in cluster "{diagram['cluster_title']}"
**Dialogue**: {diagram['context'][:300]}{"..." if len(diagram['context']) > 300 else ""}
**Error**: {diagram['error']}

```mermaid
{diagram['content']}
```

---
"""
            
            # Log each diagram being sent for correction
            if self.shared_context.get("logging_enabled"):
                from src.utils.podcast_logger import PodcastLogger
                logger = PodcastLogger(self.shared_context.get("task_id"))
                logger.log_node_start(
                    f"ValidateMermaidDiagrams - Sending for Correction",
                    {
                        "diagram_id": diagram['id'],
                        "error": diagram['error'],
                        "content_length": len(diagram['content'])
                    }
                )
        
        prompt += """

## Your Task:
1. Carefully analyze each syntax error
2. Fix the syntax while preserving the diagram's meaning and structure
3. Ensure proper Mermaid syntax (correct arrows, proper node definitions, balanced quotes/brackets)
4. Keep the visual information relevant to the dialogue context

## Common Mermaid Syntax Rules:
- Use proper arrow syntax: --> (solid), -.-> (dotted), ==> (thick)
- Node IDs cannot contain spaces (use quotes for labels with spaces)
- Ensure all quotes and brackets are balanced
- Use proper subgraph syntax: subgraph name [...] end

## Output Format:
Return ONLY the corrected diagrams in this YAML format:

```yaml
corrections:
  cluster_id_dialogue_number: |
    graph TD
      A["Corrected Node"]
      B["Another Node"]
      A --> B
  
  another_cluster_id_dialogue_number: |
    sequenceDiagram
      participant A as Alice
      participant B as Bob
      A->>B: Fixed message
```

Make sure each diagram is syntactically correct and will render properly.
"""
        
        return prompt
    
    def _parse_corrections_yaml(self, response: str) -> Dict[str, str]:
        """Parse LLM response containing corrected diagrams."""
        corrections = {}
        
        # Log the raw response for debugging
        if self.shared_context.get("logging_enabled"):
            from src.utils.podcast_logger import PodcastLogger
            logger = PodcastLogger(self.shared_context.get("task_id"))
            logger.log_node_start(
                "ValidateMermaidDiagrams - LLM Response Parsing",
                {
                    "response_length": len(response),
                    "response_preview": response[:500] + "..." if len(response) > 500 else response
                }
            )
        
        try:
            # Extract YAML content
            yaml_match = re.search(r'```yaml\n(.*?)\n```', response, re.DOTALL)
            if yaml_match:
                yaml_content = yaml_match.group(1)
                data = yaml.safe_load(yaml_content)
                corrections = data.get('corrections', {})
                
                if self.shared_context.get("logging_enabled"):
                    logger.log_node_start(
                        "ValidateMermaidDiagrams - Parsed Corrections",
                        {
                            "corrections_found": len(corrections),
                            "correction_keys": list(corrections.keys())
                        }
                    )
            else:
                # No YAML block found
                if self.shared_context.get("logging_enabled"):
                    logger.log_warning(
                        "ValidateMermaidDiagrams",
                        "No YAML block found in LLM response"
                    )
        except yaml.YAMLError as e:
            # YAML parsing error
            if self.shared_context.get("logging_enabled"):
                logger.log_error(
                    "ValidateMermaidDiagrams",
                    f"YAML parsing error: {str(e)}"
                )
        except Exception as e:
            # Other parsing error
            if self.shared_context.get("logging_enabled"):
                logger.log_error(
                    "ValidateMermaidDiagrams",
                    f"Failed to parse LLM corrections: {str(e)}"
                )
        
        return corrections
    
    def _convert_to_markdown(self, still_failed: List[Dict]) -> Dict[str, Dict]:
        """Convert still-failed diagrams to Markdown descriptions, batching if needed."""
        all_conversions = {}
        
        # Similar batching logic as corrections
        avg_tokens_per_diagram = 400
        base_prompt_tokens = 1500
        max_diagrams_per_batch = (DEFAULT_MAX_CONTEXT_TOKENS - base_prompt_tokens - 5000) // avg_tokens_per_diagram
        
        for i in range(0, len(still_failed), max_diagrams_per_batch):
            batch = still_failed[i:i + max_diagrams_per_batch]
            
            # Build prompt for Markdown conversion
            prompt = self._build_markdown_conversion_prompt(batch)
            
            # Check prompt size
            is_within_limit, token_count, token_limit = check_prompt_size(prompt)
            
            if not is_within_limit:
                if len(batch) > 1:
                    # Reduce batch size
                    batch = batch[:len(batch) // 2]
                    prompt = self._build_markdown_conversion_prompt(batch)
                else:
                    # Truncate if single diagram is too large
                    prompt = truncate_prompt(prompt, token_limit)
            
            # Call LLM
            shared = self.shared_context
            if shared.get("logging_enabled") and shared.get("task_id"):
                response = call_llm_with_logging(
                    prompt=prompt,
                    node_name="ValidateMermaidDiagrams",
                    cluster_info={"action": "convert_to_markdown", "batch_size": len(batch)},
                    task_id=shared.get("task_id")
                )
            else:
                response = call_llm(prompt)
            
            # Parse Markdown conversions
            batch_conversions = self._parse_markdown_conversions(response)
            all_conversions.update(batch_conversions)
        
        return all_conversions
    
    def _build_markdown_conversion_prompt(self, still_failed: List[Dict]) -> str:
        """Build prompt for converting failed Mermaid to Markdown."""
        prompt = """You need to convert failed Mermaid diagrams into rich Markdown descriptions.
Since these diagrams cannot be fixed, create equivalent visual information using Markdown.

## Failed Diagrams to Convert:

"""
        
        for diagram in still_failed:
            prompt += f"""
### Diagram ID: {diagram['id']}
**Context**: {diagram['speaker']} explaining in "{diagram['cluster_title']}"
**Dialogue**: {diagram['context'][:300]}{"..." if len(diagram['context']) > 300 else ""}

**Original Mermaid Attempt**:
```
{diagram['content']}
```

**What the diagram tried to show**: Analyze the attempted diagram and dialogue context.

---
"""
        
        prompt += """

## Your Task:
1. Understand what each diagram was trying to visualize
2. Create an equivalent Markdown representation with:
   - Clear headers and structure
   - Bullet points or numbered lists for flows/sequences
   - Tables where appropriate
   - Code blocks if showing technical content
   - ASCII art for simple diagrams (optional)
   - Bold/italic text for emphasis
   - No info about corrected View, only the Markdown content

## Output Format:
```yaml
conversions:
  cluster_id_dialogue_number:
    content: |
      ## Title Based on Diagram Purpose
      
      Your rich Markdown content here...
      - With proper structure
      - Clear visual hierarchy
      
      ```
      Code examples if needed
      ```
  
  another_id:
    content: |
      ### Another Visualization
      
      More Markdown content...
```

Make each Markdown visualization informative and visually clear.
"""
        
        return prompt
    
    def _parse_markdown_conversions(self, response: str) -> Dict[str, Dict]:
        """Parse Markdown conversions from LLM response."""
        conversions = {}
        
        # Log the response for debugging
        if self.shared_context.get("logging_enabled"):
            from src.utils.podcast_logger import PodcastLogger
            logger = PodcastLogger(self.shared_context.get("task_id"))
            logger.log_node_start(
                "ValidateMermaidDiagrams - Markdown Response Parsing",
                {
                    "response_length": len(response),
                    "response_preview": response[:500] + "..." if len(response) > 500 else response
                }
            )
        
        try:
            yaml_match = re.search(r'```yaml\n(.*?)\n```', response, re.DOTALL)
            if yaml_match:
                yaml_content = yaml_match.group(1)
                data = yaml.safe_load(yaml_content)
                raw_conversions = data.get('conversions', {})
                
                # Transform to expected format
                for diagram_id, conversion_data in raw_conversions.items():
                    conversions[diagram_id] = {
                        'type': 'markdown',
                        'content': conversion_data.get('content', '')
                    }
                
                if self.shared_context.get("logging_enabled"):
                    logger.log_node_start(
                        "ValidateMermaidDiagrams - Parsed Markdown Conversions",
                        {
                            "conversions_found": len(conversions),
                            "conversion_keys": list(conversions.keys())
                        }
                    )
            else:
                if self.shared_context.get("logging_enabled"):
                    logger.log_warning(
                        "ValidateMermaidDiagrams",
                        "No YAML block found in Markdown conversion response"
                    )
        except Exception as e:
            if self.shared_context.get("logging_enabled"):
                logger.log_error(
                    "ValidateMermaidDiagrams",
                    f"Failed to parse Markdown conversions: {str(e)}"
                )
        
        return conversions
    
    def _apply_all_corrections(self, 
                              podcast_data: Dict, 
                              mermaid_corrections: Dict[str, str],
                              markdown_conversions: Dict[str, Dict]) -> Dict:
        """Apply all corrections and conversions to create new podcast data."""
        # Deep copy the original data
        import copy
        new_data = copy.deepcopy(podcast_data)
        
        # Track statistics
        corrections_applied = 0
        conversions_applied = 0
        
        # Apply corrections
        for cluster in new_data.get('clusters', []):
            for dialogue in cluster.get('dialogues', []):
                if 'visualization' in dialogue:
                    diagram_id = f"{cluster['cluster_id']}_dialogue_{dialogue['dialogue_id']}"
                    
                    # Check for Mermaid correction
                    if diagram_id in mermaid_corrections:
                        dialogue['visualization']['content'] = mermaid_corrections[diagram_id]
                        dialogue['visualization']['corrected'] = True
                        dialogue['visualization']['validation_status'] = 'corrected'
                        corrections_applied += 1
                    
                    # Check for Markdown conversion
                    elif diagram_id in markdown_conversions:
                        # Change type and content
                        dialogue['visualization'] = {
                            'type': 'markdown',
                            'content': markdown_conversions[diagram_id]['content'],
                            'original_type': 'mermaid',
                            'converted_reason': 'mermaid_validation_failed',
                            'validation_status': 'converted_to_markdown'
                        }
                        conversions_applied += 1
        
        # Update metadata
        if 'metadata' not in new_data:
            new_data['metadata'] = {}
        
        new_data['metadata']['mermaid_validation'] = {
            'validated_at': datetime.now().isoformat(),
            'total_mermaid_diagrams': len(mermaid_corrections) + len(markdown_conversions),
            'corrections_applied': corrections_applied,
            'conversions_to_markdown': conversions_applied,
            'validation_version': '1.0'
        }
        
        return new_data
    
    def _save_corrected_json(self, podcast_data: Dict, original_path: str) -> str:
        """Save the corrected podcast JSON with a special name."""
        # Extract base name and directory
        dir_name = os.path.dirname(original_path)
        base_name = os.path.basename(original_path)
        name_parts = base_name.rsplit('.', 1)
        
        # Create new filename with _validated suffix
        if len(name_parts) == 2:
            new_name = f"{name_parts[0]}_validated.{name_parts[1]}"
        else:
            new_name = f"{base_name}_validated"
        
        new_path = os.path.join(dir_name, new_name)
        
        # Save the corrected JSON
        with open(new_path, 'w', encoding='utf-8') as f:
            json.dump(podcast_data, f, indent=2, ensure_ascii=False)
        
        return new_path
    
    def post(self, shared: Dict, prep_res: Tuple, exec_res: Dict) -> str:
        """Update shared context with validation results."""
        # Update the podcast result with new path
        shared["podcast_result"]["output_path"] = exec_res["output_path"]
        shared["podcast_result"]["validation_status"] = exec_res["status"]
        
        # Add validation statistics
        if exec_res["status"] == "corrected":
            shared["podcast_result"]["validation_stats"] = {
                "total_corrections": exec_res["corrections_count"],
                "mermaid_fixed": exec_res.get("mermaid_fixed", 0),
                "converted_to_markdown": exec_res.get("converted_to_markdown", 0)
            }
        
        # Log completion
        if shared.get("logging_enabled") and shared.get("task_id"):
            from src.utils.podcast_logger import PodcastLogger
            logger = PodcastLogger(shared.get("task_id"))
            
            if exec_res["status"] == "corrected":
                # Use log_node_start to log completion info
                logger.log_node_start(
                    "ValidateMermaidDiagrams - Completion",
                    {
                        "status": exec_res["status"],
                        "total_corrections": exec_res['corrections_count'],
                        "mermaid_fixed": exec_res.get("mermaid_fixed", 0),
                        "converted_to_markdown": exec_res.get("converted_to_markdown", 0),
                        "output_path": exec_res["output_path"]
                    }
                )
        
        return "default"