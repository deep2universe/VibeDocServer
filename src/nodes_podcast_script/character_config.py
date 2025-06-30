"""
Character configuration for podcast generation.
"""

from typing import Optional
from pydantic import BaseModel


class CharacterConfig(BaseModel):
    """Configuration for a podcast character."""
    name: str
    role: str
    personality: str
    background: str
    speaking_style: str


# Default character configurations
DEFAULT_CHARACTER_1 = CharacterConfig(
    name="Emma",
    role="Masters Student",
    personality="curious, analytical, eager to understand",
    background="Working on thesis about workflow orchestration systems",
    speaking_style="asks insightful questions, connects concepts to research, occasionally shares thesis insights"
)

DEFAULT_CHARACTER_2 = CharacterConfig(
    name="Alex",
    role="Senior Developer", 
    personality="patient, enthusiastic, knowledgeable",
    background="10+ years experience building distributed systems",
    speaking_style="explains with practical examples, uses analogies, encourages exploration"
)


def get_characters(char1_config: Optional[CharacterConfig] = None, 
                  char2_config: Optional[CharacterConfig] = None) -> tuple:
    """Get character configurations, using defaults if not provided."""
    char1 = char1_config or DEFAULT_CHARACTER_1
    char2 = char2_config or DEFAULT_CHARACTER_2
    return (char1, char2)