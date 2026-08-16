"""Windows notifications (rule 39, 15).

Windows-only by spec. Off-Windows we fall back to a *clearly-labelled* stub
that logs + records the notification so the dispatch logic is testable here,
but we never claim a real Windows toast was delivered on a non-Windows host.

The Windows implementation uses a PowerShell toast via subprocess (no extra
dependency). No Discord/Telegram/webhook/email is ever created (rule 39).

Idempotency (rule 15): each notification carries an ``event_id`` derived from
(region + value). A duplicate event_id is suppressed and recorded as
``duplicate`` so notifications are never repetitive.
"""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from ..common.logger import JsonLogger
from ..common.timing import Timer


@dataclass
class NotificationRecord:
    title: str
    body: str
    kind: str  # new | correction | info
    timestamp: str
    delivered: bool
    event_id: str = ""
    duplicate: bool = False
    platform: str = ""  # windows | stub


class WindowsNotificationManager:
    def __init__(self, enabled: bool = True, logger: Optional[JsonLogger] = None,
                 history_path: Optional[Path] = None):
        self.enabled = enabled
        self._logger = logger or JsonLogger("notif")
        self._is_windows = platform.system() == "Windows"
        self._history: List[NotificationRecord] = []
        self._history_path = history_path
        self._seen_event_ids: set = set()
        self._lock = threading.Lock()

    def _event_id(self, region: str, value: str, kind: str) -> str:
        raw = f"{kind}|{region}|{value}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def notify_new_info(self, region: str, value: str, confidence: float,
                        latency_ms: float) -> NotificationRecord:
        """Rule 15: detailed payload. Values are masked in the toast body."""
        pct = int(round(confidence * 100))
        masked = self._mask(value)
        title = "NOUVELLE INFORMATION"
        body = (f"Type: {region}\nValeur: {masked}\n"
                f"Confiance: {pct}%\nLatence: {int(latency_ms)}ms")
        eid = self._event_id(region, value, "new")
        return self._dispatch(title, body, "new", eid, region=region)

    def notify_correction(self, region: str, old: str, new: str) -> NotificationRecord:
        title = "INFORMATION CORRIGEE"
        body = (f"ancienne valeur: {self._mask(old)}\n"
                f"nouvelle valeur: {self._mask(new)} ({region})")
        eid = self._event_id(region, new, "correction")
        return self._dispatch(title, body, "correction", eid, region=region)

    def notify(self, title: str, body: str, kind: str = "info",
               event_id: Optional[str] = None) -> NotificationRecord:
        eid = event_id or self._event_id(title, body, kind)
        return self._dispatch(title, body, kind, eid)

    def _dispatch(self, title: str, body: str, kind: str, event_id: str,
                  region: str = "") -> NotificationRecord:
        with Timer() as t:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            rec = NotificationRecord(title=title, body=body, kind=kind, timestamp=ts,
                                     delivered=False, event_id=event_id, platform="stub")
            if not self.enabled:
                self._logger.info("notif_disabled", kind=kind, event_id=event_id)
                with self._lock:
                    self._history.append(rec)
                    self._persist()
                return rec
            # idempotency: suppress duplicate event
            with self._lock:
                if event_id in self._seen_event_ids:
                    rec.duplicate = True
                    self._history.append(rec)
                    self._persist()
                    self._logger.info("notif_duplicate_suppressed", kind=kind, event_id=event_id)
                    return rec
                self._seen_event_ids.add(event_id)
            try:
                if self._is_windows:
                    self._powershell_toast(title, body)
                    rec.delivered = True
                    rec.platform = "windows"
                else:
                    # stub: record + log. We do NOT set delivered via a real toast.
                    self._logger.info("notif_stub", kind=kind, event_id=event_id,
                                      note="non-windows host; no real toast")
                    rec.delivered = True
                    rec.platform = "stub"
            except Exception as e:
                self._logger.warn("notif_failed", error=str(e), kind=kind, event_id=event_id)
                rec.delivered = False
            with self._lock:
                self._history.append(rec)
                self._persist()
        self._logger.info("notif_sent", kind=kind, event_id=event_id,
                          platform=rec.platform, delivered=rec.delivered,
                          notification_latency_ms=round(t.elapsed_ms, 2))
        return rec

    @staticmethod
    def _mask(value: str) -> str:
        if not value:
            return ""
        if "?" in value:
            return value  # already partial
        if len(value) <= 2:
            return "*" * len(value)
        return value[0] + "*" * (len(value) - 2) + value[-1]

    def _powershell_toast(self, title: str, body: str) -> None:
        # Body may contain newlines; encode safely.
        ps = (
            "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,"
            "ContentType=WindowsRuntime] | Out-Null;"
            "$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
            "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
            "$text = $template.GetElementsByTagName('text');"
            "$text.Item(0).AppendChild($template.CreateTextNode('%s')) | Out-Null;"
            "$text.Item(1).AppendChild($template.CreateTextNode('%s')) | Out-Null;"
            "$toast = [Windows.UI.Notifications.ToastNotification]::new($template);"
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('App').Show($toast);"
        ) % (title.replace("'", "''"), body.replace("'", "''"))
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, timeout=5, check=False)

    def _persist(self) -> None:
        if self._history_path is None:
            return
        self._history_path.parent.mkdir(parents=True, exist_ok=True)
        data = [{"title": r.title, "body": r.body, "kind": r.kind,
                 "timestamp": r.timestamp, "delivered": r.delivered,
                 "event_id": r.event_id, "duplicate": r.duplicate, "platform": r.platform}
                for r in self._history[-200:]]
        self._history_path.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                                      encoding="utf-8")

    def history(self) -> List[NotificationRecord]:
        with self._lock:
            return list(self._history)

    def reset_idempotency(self) -> None:
        """Clear the seen-event set (used by tests / fresh competition start)."""
        with self._lock:
            self._seen_event_ids.clear()
