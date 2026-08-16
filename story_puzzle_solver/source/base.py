"""StorySource interfaces and the ``StoryItem`` data model."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


@dataclass
class StoryItem:
    """A single story publication discovered by a source."""

    story_id: str
    media_url: Optional[str] = None
    media_path: Optional[Path] = None
    media_type_hint: Optional[str] = None  # IMAGE | VIDEO | None(unknown)
    timestamp: Optional[datetime] = None
    author: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def __hash__(self) -> int:  # for dedup sets
        return hash(self.story_id)


class StorySource(ABC):
    """Conceptual story source interface (rule 7)."""

    @abstractmethod
    def connect(self) -> bool: ...

    @abstractmethod
    def poll(self) -> List[StoryItem]: ...

    def subscribe(self, callback: Callable[[StoryItem], None]) -> None:
        """Optional push subscription. Default: not supported (poll-only)."""
        raise NotImplementedError("subscribe not supported by this source")

    @abstractmethod
    def get_latest(self) -> Optional[StoryItem]: ...

    @abstractmethod
    def get_media(self, story: StoryItem, dest: Path) -> Path: ...

    @abstractmethod
    def disconnect(self) -> None: ...


class AuthorizedStorySource(StorySource, ABC):
    """Interface for a real, *authorized* story source.

    Implementations MUST use only legitimate, authorized access (rule 7).
    Prohibited: cookie theft, session recovery from another user, auth
    circumvention, CAPTCHA bypass, anti-bot bypass, vulnerability exploitation,
    access to private content without authorization.

    The architecture is wired so that dropping in an ``AuthorizedStorySource``
    implementation does not require rewriting the rest of the system.
    """

    @abstractmethod
    def authorize(self, credentials: Dict[str, Any]) -> bool: ...
