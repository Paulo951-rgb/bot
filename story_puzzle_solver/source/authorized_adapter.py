"""Authorized story source — adapter contract (rule 7, §3, §24).

This module defines the integration point for a REAL, authorized story source.
It is intentionally NOT a working implementation: there is no official, openly
authorized API for the target platform available in this environment, and the
spec (§24) FORBIDS inventing a fake integration that pretends to fetch real
stories.

To connect a real source, subclass :class:`AuthorizedStorySourceAdapter` and
implement :meth:`authorize` + :meth:`_poll_impl` + :meth:`_get_media_impl`
using ONLY legitimate, authorized access (an official API with the user's own
credentials, or an explicitly permitted integration). Then pass an instance to
the pipeline / dashboard and call :meth:`connect`.

PROHIBITED (rule 7): cookie theft, session recovery from another user, auth
circumvention, CAPTCHA bypass, anti-bot bypass, vulnerability exploitation,
access to private content without authorization.

Example skeleton (pseudo-wiring, NOT a real backend)::

    class MyAuthorizedSource(AuthorizedStorySourceAdapter):
        def authorize(self, credentials):
            # e.g. OAuth login with the user's own token
            ...
        def _poll_impl(self):
            # call the official API; return List[StoryItem]
            ...
        def _get_media_impl(self, story, dest):
            # download the media the API authorizes you to fetch
            ...
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..common.logger import JsonLogger
from .base import AuthorizedStorySource, StoryItem


class AuthorizedStorySourceAdapter(AuthorizedStorySource):
    """Base adapter for a real authorized source.

    Subclasses implement the three ``_impl`` hooks. Connection state is tracked
    honestly: ``connected`` is True only after a successful :meth:`authorize`.
    """

    def __init__(self, logger: Optional[JsonLogger] = None,
                 poll_interval_ms: int = 250):
        self._logger = logger or JsonLogger("auth_source")
        self.poll_interval_ms = poll_interval_ms
        self._connected = False
        self._latest: Optional[StoryItem] = None

    # --- AuthorizedStorySource interface --------------------------------
    def authorize(self, credentials: Dict[str, Any]) -> bool:
        """Subclass: perform real authorized login. Return True on success."""
        raise NotImplementedError("authorize() must be implemented by the real adapter")

    def connect(self) -> bool:
        # Connection requires a prior successful authorize(); be honest (rule 24).
        if not self._connected:
            self._logger.warn("auth_source_not_authorized",
                              note="authorize() not completed; cannot connect")
            return False
        return True

    def poll(self) -> List[StoryItem]:
        if not self._connected:
            return []
        items = self._poll_impl()
        if items:
            self._latest = items[-1]
        return items

    def get_latest(self) -> Optional[StoryItem]:
        return self._latest

    def get_media(self, story: StoryItem, dest: Path) -> Path:
        if not self._connected:
            raise ConnectionError("authorized source not connected")
        return self._get_media_impl(story, dest)

    def disconnect(self) -> None:
        self._connected = False
        self._logger.info("auth_source_disconnected")

    # --- hooks for subclasses -------------------------------------------
    def _poll_impl(self) -> List[StoryItem]:
        raise NotImplementedError("_poll_impl() must be implemented by the real adapter")

    def _get_media_impl(self, story: StoryItem, dest: Path) -> Path:
        raise NotImplementedError("_get_media_impl() must be implemented by the real adapter")

    @property
    def connected(self) -> bool:
        return self._connected
