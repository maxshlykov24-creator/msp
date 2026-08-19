#!/usr/bin/env python3
"""
Скрипт проверки корректности установки агента @модель

Использование:
    python3 проверка_установки.py
    python3 проверка_установки.py --path /path/to/project
"""

import argparse
from pathlib import Path
import sys


def find_project_root(start_path=None):
    """Найти корень проекта (где есть .cursor/)"""
    if start_path:
        current = Path(start_path).resolve()
    else:
        current = Path.cwd()
    
    for _ in range(5):
        cursor_dir = current / ".cursor"
        if cursor_dir.exists() and cursor_dir.is_dir():
            return current
        
        parent = current.parent
        if parent == current:
            break
        current = parent
    
    return None


def check_file_exists(project_root, file_path, description=""):
    """Проверить наличие файла"""
    full_path = project_root / file_path
    exists = full_path.exists()
    
    status = "✅" if exists else "❌"
    desc = f" ({description})" if description else ""
    
    print(f"   {status} {file_path}{desc}")
    
    return exists


def check_installation(project_root):
    """Проверить установку агента"""
    print("\n" + "="*60)
    print("🔍 ПРОВЕРКА УСТАНОВКИ АГЕНТА @модель")
    print("="*60)
    
    print(f"\n📁 Корень проекта: {project_root}\n")
    
    all_ok = True
    
    # Проверка ключевых файлов
    print("📋 Проверка ключевых файлов:\n")
    
    required_files = [
        (".cursor/rules/модель.mdc", "Файл правил агента"),
        ("агенты/МОДЕЛЬ/README.md", "Основная документация"),
        ("агенты/МОДЕЛЬ/БЫСТРЫЙ_СТАРТ.md", "Быстрый старт"),
        ("агенты/МОДЕЛЬ/ПРИМЕРЫ_ИСПОЛЬЗОВАНИЯ.md", "Примеры использования"),
        ("агенты/МОДЕЛЬ/ЧАСТЫЕ_ВОПРОСЫ.md", "FAQ"),
        ("агенты/МОДЕЛЬ/_КОНТЕКСТ/таблица_выбора_моделей.md", "Таблица выбора моделей"),
        ("агенты/МОДЕЛЬ/_КОНТЕКСТ/методология_оценки.md", "Методология оценки"),
        ("агенты/МОДЕЛЬ/_КОНТЕКСТ/источники_данных.md", "Источники данных"),
        ("агенты/МОДЕЛЬ/_ДАННЫЕ/models_cache.json", "Кэш моделей"),
        ("агенты/МОДЕЛЬ/_ДАННЫЕ/fast_track_rules.json", "Правила быстрого ответа"),
        ("агенты/МОДЕЛЬ/_ДАННЫЕ/metadata.json", "Метаданные"),
    ]
    
    for file_path, description in required_files:
        if not check_file_exists(project_root, file_path, description):
            all_ok = False
    
    # Проверка структуры папок
    print("\n📂 Проверка структуры папок:\n")
    
    required_dirs = [
        ".cursor/rules",
        "агенты/МОДЕЛЬ",
        "агенты/МОДЕЛЬ/_КОНТЕКСТ",
        "агенты/МОДЕЛЬ/_ДАННЫЕ"
    ]
    
    for dir_path in required_dirs:
        full_path = project_root / dir_path
        exists = full_path.exists() and full_path.is_dir()
        status = "✅" if exists else "❌"
        print(f"   {status} {dir_path}/")
        if not exists:
            all_ok = False
    
    # Итоговый результат
    print("\n" + "="*60)
    if all_ok:
        print("✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ!")
        print("="*60)
        print("\n🎉 Агент @модель установлен корректно!\n")
        print("📚 Следующие шаги:")
        print("   1. Открой Cursor")
        print("   2. В чате вызови: @модель какая модель для тестовой задачи")
        print("   3. Загляни в агенты/МОДЕЛЬ/БЫСТРЫЙ_СТАРТ.md\n")
        return True
    else:
        print("❌ ОБНАРУЖЕНЫ ПРОБЛЕМЫ")
        print("="*60)
        print("\n⚠️  Некоторые файлы не найдены!")
        print("\n🔧 Решение:")
        print("   1. Запусти установку заново:")
        print("      python3 install_agent.py")
        print("   2. Или установи вручную (см. ИНСТРУКЦИЯ_УСТАНОВКИ.md)\n")
        return False


def main():
    parser = argparse.ArgumentParser(
        description='Проверка установки агента @модель'
    )
    parser.add_argument(
        '--path',
        type=str,
        help='Путь к корню проекта (если не указан, ищется автоматически)'
    )
    
    args = parser.parse_args()
    
    # Найти корень проекта
    if args.path:
        project_root = Path(args.path).resolve()
        if not (project_root / ".cursor").exists():
            print(f"\n❌ Ошибка: {project_root} не является корнем проекта Cursor")
            sys.exit(1)
    else:
        project_root = find_project_root()
        if not project_root:
            print("\n❌ Не удалось найти корень проекта")
            print("\nУкажите путь явно:")
            print("  python3 проверка_установки.py --path /path/to/project")
            sys.exit(1)
    
    # Проверить установку
    success = check_installation(project_root)
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

