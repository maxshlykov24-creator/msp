"""
Модуль сохранения файлов в папку Распределение
Курс: CURSOR — Сотрудники под твой бизнес за вечер
Модуль 6: API Интеграции

Это шаблон. Часть кода готова, часть нужно дописать по TODO.
"""

import os
import shutil
from datetime import datetime
from pathlib import Path


class FileSaver:
    """Класс для сохранения файлов в структурированные папки"""
    
    def __init__(self, base_folder: str):
        """
        Инициализация сохранителя файлов
        
        Args:
            base_folder: Путь к папке Распределение
        """
        self.base_folder = Path(base_folder)
        self.ensure_folders()
    
    def ensure_folders(self):
        """Создаёт структуру папок если её нет (ГОТОВО)"""
        folders = [
            "сообщения",
            "транскрипты", 
            "документы",
            "изображения",
            "ссылки"
        ]
        
        for folder in folders:
            folder_path = self.base_folder / folder
            folder_path.mkdir(parents=True, exist_ok=True)
        
        print(f"📁 Папка Распределение готова: {self.base_folder}")
    
    def get_timestamp(self) -> str:
        """Возвращает текущую дату-время в формате для имени файла (ГОТОВО)"""
        return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    
    def save_to_file(self, folder: str, filename: str, content: str) -> str:
        """
        Сохраняет текст в файл (ГОТОВО)
        
        Args:
            folder: Название подпапки (сообщения, транскрипты и т.д.)
            filename: Имя файла
            content: Содержимое файла
            
        Returns:
            Путь к созданному файлу
        """
        filepath = self.base_folder / folder / filename
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        
        return str(filepath)
    
    # TODO 1: Дописать метод save_message
    # ============================================================================
    # ПОДСКАЗКА:
    # 1. Сформируй имя файла с timestamp: f"{self.get_timestamp()}.md"
    # 2. Сформируй содержимое файла в формате markdown:
    #    - Заголовок: # Сообщение из Telegram
    #    - Дата: **Дата:** {datetime.now()}
    #    - Пользователь: **Пользователь:** {username} [ID: {user_id}]
    #    - Содержимое: {text}
    # 3. Используй self.save_to_file("сообщения", filename, content)
    # 4. Верни путь к файлу
    # ============================================================================
    def save_message(self, text: str, username: str, user_id: int) -> str:
        """
        Сохраняет текстовое сообщение в файл
        
        Args:
            text: Текст сообщения
            username: Username пользователя
            user_id: ID пользователя
            
        Returns:
            Путь к созданному файлу
        """
        # Формируем имя файла
        filename = f"{self.get_timestamp()}.md"
        
        # TODO 1: Сформируй содержимое файла и сохрани
        # Используй подсказку выше
        #
        # Пример содержимого:
        # # Сообщение из Telegram
        #
        # **Дата:** 2026-01-12 14:30:15
        # **Пользователь:** ivan (@ivan_user) [ID: 123456789]
        # **Тип:** Текст
        #
        # ---
        #
        # ## Содержимое
        #
        # Текст сообщения здесь...
        #
        # Твой код здесь:
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
        """
        Сохраняет транскрипт в файл (ГОТОВО)
        
        Args:
            transcript: Текст транскрипта
            source_type: Тип источника (voice/video)
            
        Returns:
            Путь к созданному файлу
        """
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
    
    # TODO 2: Дописать метод save_document
    # ============================================================================
    # ПОДСКАЗКА:
    # 1. Сформируй имя файла: f"{self.get_timestamp()}_{original_name}"
    # 2. Определи путь назначения: self.base_folder / "документы" / filename
    # 3. Скопируй файл: shutil.copy2(source_path, dest_path)
    # 4. Верни путь к сохранённому файлу
    # ============================================================================
    def save_document(self, source_path: str, original_name: str) -> str:
        """
        Сохраняет документ в папку документы
        
        Args:
            source_path: Путь к временному файлу
            original_name: Оригинальное имя файла
            
        Returns:
            Путь к сохранённому файлу
        """
        # TODO 2: Допиши код сохранения документа
        # Используй подсказку выше
        #
        # Твой код здесь:
        # ...
        
        return ""  # Замени на реальный путь
    
    # TODO 3: Дописать метод save_image
    # ============================================================================
    # ПОДСКАЗКА:
    # 1. Сформируй имя файла: f"image_{self.get_timestamp()}.jpg"
    # 2. Определи путь назначения: self.base_folder / "изображения" / filename
    # 3. Скопируй файл: shutil.copy2(source_path, dest_path)
    # 4. Верни путь к сохранённому файлу
    # ============================================================================
    def save_image(self, source_path: str) -> str:
        """
        Сохраняет изображение в папку изображения
        
        Args:
            source_path: Путь к временному файлу
            
        Returns:
            Путь к сохранённому файлу
        """
        # TODO 3: Допиши код сохранения изображения
        # Используй подсказку выше
        #
        # Твой код здесь:
        # ...
        
        return ""  # Замени на реальный путь
    
    def save_link(self, url: str, title: str = "") -> str:
        """
        Сохраняет ссылку в файл (ГОТОВО)
        
        Args:
            url: URL ссылки
            title: Заголовок страницы (опционально)
            
        Returns:
            Путь к созданному файлу
        """
        filename = f"{self.get_timestamp()}.md"
        
        content = f"""# Ссылка из Telegram

**Дата:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**URL:** {url}
**Заголовок:** {title or "Не определён"}

---
"""
        
        return self.save_to_file("ссылки", filename, content)
    
    def get_recent_files(self, limit: int = 10) -> list:
        """
        Возвращает список последних сохранённых файлов (ГОТОВО)
        
        Args:
            limit: Количество файлов
            
        Returns:
            Список путей к файлам
        """
        all_files = []
        
        for folder in ["сообщения", "транскрипты", "документы", "изображения", "ссылки"]:
            folder_path = self.base_folder / folder
            if folder_path.exists():
                for file in folder_path.iterdir():
                    if file.is_file():
                        all_files.append({
                            "path": str(file),
                            "name": file.name,
                            "folder": folder,
                            "modified": file.stat().st_mtime
                        })
        
        # Сортируем по времени изменения (новые первые)
        all_files.sort(key=lambda x: x["modified"], reverse=True)
        
        return all_files[:limit]


# Пример использования (для тестирования)
if __name__ == "__main__":
    saver = FileSaver("./Распределение")
    
    # Тест сохранения сообщения
    path = saver.save_message("Тестовое сообщение", "test_user", 123456)
    print(f"Сохранено: {path}")
    
    # Тест получения последних файлов
    files = saver.get_recent_files(5)
    print(f"Последние файлы: {files}")

