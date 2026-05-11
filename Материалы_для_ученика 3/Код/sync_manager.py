"""
Модуль синхронизации и логирования
Курс: CURSOR — Сотрудники под твой бизнес за вечер
Модуль 6: API Интеграции

Это шаблон. Часть кода готова, часть нужно дописать по TODO.

Что делает модуль:
- Логирует все действия бота в bot.log
- Отмечает обработанные сообщения в processed.json
- Предоставляет статистику для команд /sync и /log
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict


class SyncManager:
    """Класс для синхронизации и логирования работы бота"""
    
    def __init__(self, base_folder: str):
        """
        Инициализация менеджера синхронизации
        
        Args:
            base_folder: Путь к папке Распределение
        """
        self.base_folder = Path(base_folder)
        self.log_file = self.base_folder / "bot.log"
        self.processed_file = self.base_folder / "processed.json"
        self.startup_time = datetime.now()
        
        # Создаём папку если нет
        self.base_folder.mkdir(parents=True, exist_ok=True)
        
        # Загружаем список обработанных сообщений
        self.processed_ids = self._load_processed()
        
        # Счётчики для статистики
        self.messages_count = 0
        self.files_count = 0
        self.transcripts_count = 0
    
    def _load_processed(self) -> set:
        """Загружает список обработанных сообщений (ГОТОВО)"""
        if self.processed_file.exists():
            with open(self.processed_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("processed_ids", []))
        return set()
    
    def _save_processed(self):
        """Сохраняет список обработанных сообщений (ГОТОВО)"""
        with open(self.processed_file, "w", encoding="utf-8") as f:
            json.dump({
                "processed_ids": list(self.processed_ids),
                "last_updated": datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)
    
    def log_action(self, action_type: str, message: str):
        """
        Записывает действие в лог-файл (ГОТОВО)
        
        Args:
            action_type: Тип действия (TEXT, VOICE, DOCUMENT, STARTUP и т.д.)
            message: Описание действия
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] [{action_type}] {message}\n"
        
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(log_entry)
        
        # Обновляем счётчики
        if action_type == "TEXT":
            self.messages_count += 1
        elif action_type in ["DOCUMENT", "IMAGE"]:
            self.files_count += 1
        elif action_type in ["VOICE", "VIDEO"]:
            self.transcripts_count += 1
    
    # TODO 1: Дописать метод mark_as_processed
    # ============================================================================
    # ПОДСКАЗКА:
    # 1. Добавь message_id в self.processed_ids (это set)
    # 2. Вызови self._save_processed() чтобы сохранить изменения
    # ============================================================================
    def mark_as_processed(self, message_id: int):
        """
        Отмечает сообщение как обработанное
        
        Args:
            message_id: ID сообщения в Telegram
        """
        # TODO 1: Допиши код
        # Используй подсказку выше
        #
        # Твой код здесь:
        # ...
        
        pass  # Удали эту строку когда допишешь код
    
    def is_processed(self, message_id: int) -> bool:
        """Проверяет, было ли сообщение уже обработано (ГОТОВО)"""
        return message_id in self.processed_ids
    
    # TODO 2: Дописать метод get_sync_stats
    # ============================================================================
    # ПОДСКАЗКА:
    # 1. Сформируй словарь со статистикой:
    #    - startup_time: self.startup_time
    #    - messages_count: self.messages_count
    #    - files_count: self.files_count
    #    - transcripts_count: self.transcripts_count
    #    - processed_total: len(self.processed_ids)
    # 2. Верни этот словарь
    # ============================================================================
    def get_sync_stats(self) -> Dict:
        """
        Возвращает статистику синхронизации для команды /sync
        
        Returns:
            Словарь со статистикой
        """
        # TODO 2: Допиши код
        # Используй подсказку выше
        #
        # Пример результата:
        # {
        #     "startup_time": "2026-01-12 14:30:15",
        #     "messages_count": 10,
        #     "files_count": 5,
        #     "transcripts_count": 3,
        #     "processed_total": 18
        # }
        #
        # Твой код здесь:
        # ...
        
        return {}  # Замени на реальные данные
    
    # TODO 3: Дописать метод get_last_logs
    # ============================================================================
    # ПОДСКАЗКА:
    # 1. Открой файл self.log_file для чтения
    # 2. Прочитай все строки: lines = f.readlines()
    # 3. Возьми последние N строк: lines[-limit:]
    # 4. Верни список строк
    # Не забудь обработать случай когда файл не существует!
    # ============================================================================
    def get_last_logs(self, limit: int = 10) -> List[str]:
        """
        Возвращает последние записи из лога для команды /log
        
        Args:
            limit: Количество записей
            
        Returns:
            Список последних записей лога
        """
        # TODO 3: Допиши код
        # Используй подсказку выше
        #
        # Твой код здесь:
        # ...
        
        return []  # Замени на реальные данные
    
    def get_uptime(self) -> str:
        """Возвращает время работы бота (ГОТОВО)"""
        delta = datetime.now() - self.startup_time
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        
        return f"{hours}ч {minutes}мин {seconds}сек"
    
    def format_stats_message(self) -> str:
        """
        Форматирует статистику для отправки в Telegram (ГОТОВО)
        
        Returns:
            Отформатированное сообщение
        """
        stats = self.get_sync_stats()
        
        if not stats:
            return "❌ Статистика недоступна. Проверь TODO 2 в sync_manager.py"
        
        return f"""📊 *Статус синхронизации*

🕐 *Запущен:* {stats.get('startup_time', 'N/A')}
⏱️ *Аптайм:* {self.get_uptime()}

📨 *Обработано сообщений:* {stats.get('messages_count', 0)}
📁 *Сохранено файлов:* {stats.get('files_count', 0)}
🎙️ *Транскрибировано:* {stats.get('transcripts_count', 0)}

📋 *Всего обработано:* {stats.get('processed_total', 0)} сообщений
"""
    
    def format_logs_message(self, limit: int = 10) -> str:
        """
        Форматирует логи для отправки в Telegram (ГОТОВО)
        
        Args:
            limit: Количество записей
            
        Returns:
            Отформатированное сообщение
        """
        logs = self.get_last_logs(limit)
        
        if not logs:
            return "📋 *Логи пусты*\n\nВозможно, бот только что запущен или TODO 3 не реализован."
        
        logs_text = "\n".join(logs)
        
        return f"""📋 *Последние {len(logs)} записей логов:*

```
{logs_text}
```
"""


# Пример использования (для тестирования)
if __name__ == "__main__":
    manager = SyncManager("./Распределение")
    
    # Тест логирования
    manager.log_action("STARTUP", "Бот запущен")
    manager.log_action("TEXT", "Получено сообщение от user123")
    
    # Тест отметки обработанных
    manager.mark_as_processed(12345)
    print(f"Обработано: {manager.is_processed(12345)}")
    
    # Тест статистики
    stats = manager.get_sync_stats()
    print(f"Статистика: {stats}")
    
    # Тест логов
    logs = manager.get_last_logs(5)
    print(f"Логи: {logs}")

