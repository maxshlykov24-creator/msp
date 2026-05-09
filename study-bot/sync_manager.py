"""
Синхронизация, логи, processed.json, admin.json.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


class SyncManager:
    def __init__(self, base_folder: str):
        self.base_folder = Path(base_folder)
        self.log_file = self.base_folder / "bot.log"
        self.processed_file = self.base_folder / "processed.json"
        self.admin_file = self.base_folder / "admin.json"
        self.startup_time = datetime.now()
        self.base_folder.mkdir(parents=True, exist_ok=True)
        self.processed_ids = self._load_processed()
        self.messages_count = 0
        self.files_count = 0
        self.transcripts_count = 0

    def _load_processed(self) -> set:
        if self.processed_file.exists():
            with open(self.processed_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("processed_ids", []))
        return set()

    def _save_processed(self):
        with open(self.processed_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "processed_ids": list(self.processed_ids),
                    "last_updated": datetime.now().isoformat(),
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

    def log_action(self, action_type: str, message: str):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] [{action_type}] {message}\n"
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(log_entry)
        if action_type == "TEXT":
            self.messages_count += 1
        elif action_type in ("DOCUMENT", "IMAGE"):
            self.files_count += 1
        elif action_type in ("VOICE", "VIDEO"):
            self.transcripts_count += 1

    def mark_as_processed(self, message_id: int):
        self.processed_ids.add(message_id)
        self._save_processed()

    def is_processed(self, message_id: int) -> bool:
        return message_id in self.processed_ids

    def get_admin_id(self) -> Optional[int]:
        if not self.admin_file.exists():
            return None
        try:
            with open(self.admin_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            aid = data.get("admin_id")
            return int(aid) if aid is not None else None
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None

    def register_admin_if_absent(self, user_id: int) -> bool:
        if self.get_admin_id() is not None:
            return False
        with open(self.admin_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "admin_id": user_id,
                    "registered_at": datetime.now().isoformat(),
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        self.log_action("ADMIN", f"Зарегистрирован владелец (admin_id={user_id})")
        return True

    def get_sync_stats(self) -> Dict:
        return {
            "startup_time": self.startup_time.strftime("%Y-%m-%d %H:%M:%S"),
            "messages_count": self.messages_count,
            "files_count": self.files_count,
            "transcripts_count": self.transcripts_count,
            "processed_total": len(self.processed_ids),
        }

    def get_last_logs(self, limit: int = 10) -> List[str]:
        if not self.log_file.exists():
            return []
        try:
            lines = self.log_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        if not lines:
            return []
        return lines[-limit:]

    def get_uptime(self) -> str:
        delta = datetime.now() - self.startup_time
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours}ч {minutes}мин {seconds}сек"

    def format_stats_message(self) -> str:
        stats = self.get_sync_stats()
        return f"""📊 *Статус синхронизации*

🕐 *Запущен:* {stats.get("startup_time", "N/A")}
⏱️ *Аптайм:* {self.get_uptime()}

📨 *Обработано сообщений:* {stats.get("messages_count", 0)}
📁 *Сохранено файлов:* {stats.get("files_count", 0)}
🎙️ *Транскрибировано:* {stats.get("transcripts_count", 0)}

📋 *Всего обработано:* {stats.get("processed_total", 0)} сообщений
"""

    def format_logs_message(self, limit: int = 10) -> str:
        logs = self.get_last_logs(limit)
        if not logs:
            return "📋 *Логи пусты*\n\nБот только что запущен или записей пока нет."
        logs_text = "\n".join(logs)
        return f"""📋 *Последние {len(logs)} записей логов:*

```
{logs_text}
```
"""
