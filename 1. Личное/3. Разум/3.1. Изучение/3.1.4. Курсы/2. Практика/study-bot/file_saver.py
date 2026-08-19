"""
Модуль сохранения файлов в папку Распределение.
"""

import shutil
from datetime import datetime
from pathlib import Path


class FileSaver:
    """Класс для сохранения файлов в структурированные папки"""

    def __init__(self, base_folder: str):
        self.base_folder = Path(base_folder)
        self.ensure_folders()

    def ensure_folders(self):
        folders = [
            "сообщения",
            "транскрипты",
            "документы",
            "изображения",
            "ссылки",
        ]
        for folder in folders:
            (self.base_folder / folder).mkdir(parents=True, exist_ok=True)
        print(f"Папка Распределение готова: {self.base_folder}")

    def get_timestamp(self) -> str:
        return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    def save_to_file(self, folder: str, filename: str, content: str) -> str:
        filepath = self.base_folder / folder / filename
        filepath.write_text(content, encoding="utf-8")
        return str(filepath)

    def save_message(self, text: str, username: str, user_id: int) -> str:
        filename = f"{self.get_timestamp()}.md"
        content = f"""# Сообщение из Telegram

**Дата:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**Пользователь:** @{username} [ID: {user_id}]
**Тип:** Текст

---

## Содержимое

{text}

---
"""
        return self.save_to_file("сообщения", filename, content)

    def save_transcript(self, transcript: str, source_type: str) -> str:
        filename = f"{self.get_timestamp()}_{source_type}.md"
        content = f"""# Транскрипт из Telegram

**Дата:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**Источник:** {source_type}

---

## Содержимое

{transcript}

---
"""
        return self.save_to_file("транскрипты", filename, content)

    def save_document(self, source_path: str, original_name: str) -> str:
        safe_name = Path(original_name).name or "file"
        filename = f"{self.get_timestamp()}_{safe_name}"
        dest = self.base_folder / "документы" / filename
        shutil.copy2(source_path, dest)
        return str(dest)

    def save_image(self, source_path: str) -> str:
        filename = f"image_{self.get_timestamp()}.jpg"
        dest = self.base_folder / "изображения" / filename
        shutil.copy2(source_path, dest)
        return str(dest)

    def save_link(self, url: str, title: str = "") -> str:
        filename = f"{self.get_timestamp()}.md"
        content = f"""# Ссылка из Telegram

**Дата:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**URL:** {url}
**Заголовок:** {title or "Не определён"}

---
"""
        return self.save_to_file("ссылки", filename, content)

    def get_recent_files(self, limit: int = 10) -> list:
        all_files = []
        for folder in [
            "сообщения",
            "транскрипты",
            "документы",
            "изображения",
            "ссылки",
        ]:
            folder_path = self.base_folder / folder
            if folder_path.exists():
                for file in folder_path.iterdir():
                    if file.is_file():
                        all_files.append(
                            {
                                "path": str(file),
                                "name": file.name,
                                "folder": folder,
                                "modified": file.stat().st_mtime,
                            }
                        )
        all_files.sort(key=lambda x: x["modified"], reverse=True)
        return all_files[:limit]
