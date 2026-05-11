#!/usr/bin/env python3
"""
Скрипт установки агента @модель для учеников курса "CURSOR: Экзоскелет для ума"

Использование:
    python3 install_agent.py
    python3 install_agent.py --path /path/to/project
"""

import os
import shutil
import argparse
from pathlib import Path
import sys


def find_project_root(start_path=None):
    """
    Найти корень проекта (где есть .cursor/)
    
    Args:
        start_path: Путь для начала поиска (по умолчанию текущая директория)
    
    Returns:
        Path: Путь к корню проекта
        None: Если корень не найден
    """
    if start_path:
        current = Path(start_path).resolve()
    else:
        current = Path.cwd()
    
    # Поднимаемся вверх до 5 уровней
    for _ in range(5):
        cursor_dir = current / ".cursor"
        if cursor_dir.exists() and cursor_dir.is_dir():
            print(f"✅ Найден корень проекта: {current}")
            return current
        
        parent = current.parent
        if parent == current:  # Достигли корня файловой системы
            break
        current = parent
    
    return None


def install_agent_files(project_root, source_dir):
    """
    Установить файлы агента в проект
    
    Args:
        project_root: Корень проекта
        source_dir: Директория с файлами агента
    """
    print("\n📦 Установка файлов агента...")
    
    # 1. Проверка и создание .cursor/rules/
    rules_dir = project_root / ".cursor" / "rules"
    if not rules_dir.exists():
        rules_dir.mkdir(parents=True)
        print(f"   ✅ Создана директория: {rules_dir}")
    
    # 2. Копирование .cursor/rules/модель.mdc
    source_mdc = source_dir / "файлы_агента" / ".cursor" / "rules" / "модель.mdc"
    target_mdc = rules_dir / "модель.mdc"
    
    if target_mdc.exists():
        response = input(f"\n⚠️  Файл {target_mdc.name} уже существует. Перезаписать? (y/n): ").strip().lower()
        if response != 'y':
            # Создать backup
            backup_path = target_mdc.with_suffix('.mdc.backup')
            shutil.copy2(target_mdc, backup_path)
            print(f"   💾 Создан backup: {backup_path.name}")
        else:
            print(f"   ⏭️  Пропущено: {target_mdc.name}")
            return False
    
    shutil.copy2(source_mdc, target_mdc)
    print(f"   ✅ Установлено: .cursor/rules/модель.mdc")
    
    # 3. Создание папки агенты/МОДЕЛЬ/
    agents_dir = project_root / "агенты" / "МОДЕЛЬ"
    
    if agents_dir.exists():
        response = input(f"\n⚠️  Папка агента уже существует. Обновить файлы? (y/n): ").strip().lower()
        if response != 'y':
            print(f"   ⏭️  Пропущено обновление папки агента")
            return True
    else:
        agents_dir.mkdir(parents=True)
        print(f"   ✅ Создана директория: агенты/МОДЕЛЬ/")
    
    # 4. Копирование файлов агента
    source_agent = source_dir / "файлы_агента" / "агенты" / "МОДЕЛЬ"
    
    files_to_copy = [
        "README.md",
        "БЫСТРЫЙ_СТАРТ.md",
        "ПРИМЕРЫ_ИСПОЛЬЗОВАНИЯ.md",
        "ЧАСТЫЕ_ВОПРОСЫ.md"
    ]
    
    for file in files_to_copy:
        source_file = source_agent / file
        target_file = agents_dir / file
        if source_file.exists():
            shutil.copy2(source_file, target_file)
            print(f"   ✅ Установлено: агенты/МОДЕЛЬ/{file}")
    
    # 5. Копирование папки _КОНТЕКСТ
    source_context = source_agent / "_КОНТЕКСТ"
    target_context = agents_dir / "_КОНТЕКСТ"
    
    if target_context.exists():
        shutil.rmtree(target_context)
    shutil.copytree(source_context, target_context)
    print(f"   ✅ Установлена папка: агенты/МОДЕЛЬ/_КОНТЕКСТ/")
    
    # 6. Копирование папки _ДАННЫЕ
    source_data = source_agent / "_ДАННЫЕ"
    target_data = agents_dir / "_ДАННЫЕ"
    
    if target_data.exists():
        shutil.rmtree(target_data)
    shutil.copytree(source_data, target_data)
    print(f"   ✅ Установлена папка: агенты/МОДЕЛЬ/_ДАННЫЕ/")
    
    return True


def validate_installation(project_root):
    """
    Проверить корректность установки
    
    Args:
        project_root: Корень проекта
    
    Returns:
        bool: True если установка корректна
    """
    print("\n🔍 Проверка установки...")
    
    required_files = [
        ".cursor/rules/модель.mdc",
        "агенты/МОДЕЛЬ/README.md",
        "агенты/МОДЕЛЬ/БЫСТРЫЙ_СТАРТ.md",
        "агенты/МОДЕЛЬ/_КОНТЕКСТ/таблица_выбора_моделей.md",
        "агенты/МОДЕЛЬ/_ДАННЫЕ/models_cache.json"
    ]
    
    all_ok = True
    for file_path in required_files:
        full_path = project_root / file_path
        if full_path.exists():
            print(f"   ✅ {file_path}")
        else:
            print(f"   ❌ {file_path} - НЕ НАЙДЕН")
            all_ok = False
    
    return all_ok


def print_usage_instructions():
    """Вывести инструкции по использованию"""
    print("\n" + "="*60)
    print("🎉 УСТАНОВКА ЗАВЕРШЕНА!")
    print("="*60)
    print("\n📚 Как использовать агента @модель:\n")
    print("1. Откройте проект в Cursor")
    print("2. В чате вызовите агента:")
    print("   @модель какая модель для [ваша задача]\n")
    print("Примеры:")
    print("   @модель какая модель для написания поста")
    print("   @модель оцени задачу: создать Python скрипт")
    print("   @модель напиши план автоматизации --detail\n")
    print("📖 Документация:")
    print("   - агенты/МОДЕЛЬ/README.md — полная документация")
    print("   - агенты/МОДЕЛЬ/БЫСТРЫЙ_СТАРТ.md — быстрый старт")
    print("   - агенты/МОДЕЛЬ/ПРИМЕРЫ_ИСПОЛЬЗОВАНИЯ.md — примеры\n")
    print("🆘 Нужна помощь? Загляни в агенты/МОДЕЛЬ/ЧАСТЫЕ_ВОПРОСЫ.md")
    print("="*60)


def main():
    parser = argparse.ArgumentParser(
        description='Установка агента @модель для выбора оптимальных AI-моделей'
    )
    parser.add_argument(
        '--path',
        type=str,
        help='Путь к корню проекта (если не указан, ищется автоматически)'
    )
    
    args = parser.parse_args()
    
    print("="*60)
    print("🤖 УСТАНОВКА АГЕНТА @модель")
    print("="*60)
    
    # Определить директорию со скриптом
    script_dir = Path(__file__).parent
    
    # Найти корень проекта
    if args.path:
        project_root = Path(args.path).resolve()
        if not (project_root / ".cursor").exists():
            print(f"\n❌ Ошибка: Директория {project_root} не является корнем проекта Cursor")
            print("   (не найдена папка .cursor/)")
            sys.exit(1)
    else:
        project_root = find_project_root()
        if not project_root:
            print("\n❌ Не удалось найти корень проекта автоматически")
            print("\nПожалуйста, укажите путь явно:")
            print("  python3 install_agent.py --path /path/to/your/project")
            sys.exit(1)
    
    # Установить файлы
    success = install_agent_files(project_root, script_dir)
    
    if not success:
        print("\n⚠️  Установка прервана пользователем")
        sys.exit(0)
    
    # Проверить установку
    if validate_installation(project_root):
        print("\n✅ Все файлы установлены корректно!")
        print_usage_instructions()
    else:
        print("\n❌ Установка завершена с ошибками")
        print("   Проверьте, все ли файлы скопировались корректно")
        sys.exit(1)


if __name__ == "__main__":
    main()

