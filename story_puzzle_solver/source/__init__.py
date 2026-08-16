"""Story source abstraction.

Rule 7: ``StorySource`` is the conceptual interface (connect/poll/subscribe/
getLatest/getMedia/disconnect). ``SimulationStorySource`` is concrete and used
for all tests. ``AuthorizedStorySource`` is the interface a real authorized
source must implement — it is NOT implemented here and must only ever use
legitimate, authorized access (rule 7 forbids cookie theft, session hijack,
captcha/anti-bot bypass, vulnerability exploitation, or private-content access
without authorization).
"""
from .base import StoryItem, StorySource, AuthorizedStorySource
from .simulation_source import SimulationStorySource

__all__ = ["StoryItem", "StorySource", "AuthorizedStorySource", "SimulationStorySource"]
