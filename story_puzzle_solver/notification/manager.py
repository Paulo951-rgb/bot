"""Windows notifications (rule 39).

Windows-only by spec. Off-Windows we fall back to a stub that logs + records
the notification so the dispatch logic is fully testable here. The Windows
implementation uses a PowerShell toast via subprocess (no extra dependency).
No Discord/Telegram/webhook/email is ever created (rule 39).
"""
from __future__ import annotations

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


class WindowsNotificationManager:
    def __init__(self, enabled: bool = True, logger: Optional[JsonLogger] = None,
                 history_path: Optional[Path] = None):
        self.enabled = enabled
        self._logger = logger or JsonLogger("notif")
        self._is_windows = platform.system() == "Windows"
        self._history: List[NotificationRecord] = []
        self._history_path = history_path
        self._lock = threading.Lock()

    def notify(self, title: str, body: str, kind: str = "new") -> NotificationRecord:
        with Timer() as t:
            rec = NotificationRecord(title=title, body=body, kind=kind,
                                     timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
                                     delivered=False)
            if not self.enabled:
                self._logger.info("notif_disabled", kind=kind)
                return rec
            try:
                if self._is_windows:
                    self._powershell_toast(title, body)
                    rec.delivered = True
                else:
                    # stub: record + log (dispatch logic proven; real toast on Windows)
                    self._logger.info("notif_stub", kind=kind)
                    rec.delivered = True
            except Exception as e:
                self._logger.warn("notif_failed", error=str(e), kind=kind)
                rec.delivered = False
            with self._lock:
                self._history.append(rec)
                self._persist()
        self._logger.info("notif_sent", kind=kind,
                         notification_latency_ms=round(t.elapsed_ms, 2))
        return rec

    def notify_new_info(self, region: str, value_masked: str = "••••") -> NotificationRecord:
        return self.notify("🚨 Nouvelle information détectée",
                           f"Nouvelle zone révélée ({region}).", kind="new")

    def notify_correction(self, region: str, old: str, new: str) -> NotificationRecord:
        return self.notify("⚠️ INFORMATION CORRIGÉE",
                           f"ancienne valeur: {old} → nouvelle valeur: {new} ({region})",
                           kind="correction")

    def _powershell_toast(self, title: str, body: str) -> None:
        # Use BurntToast-free approach: Windows.Runtime via PowerShell
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
                 "timestamp": r.timestamp, "delivered": r.delivered}
                for r in self._history[-200:]]
        self._history_path.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                                      encoding="utf-8")

    def history(self) -> List[NotificationRecord]:
        with self._lock:
            return list(self._history)
