"""
Модуль транскрибации через AssemblyAI
Курс: CURSOR — Сотрудники под твой бизнес за вечер
Модуль 6: API Интеграции

ВАЖНО: Этот файл полностью готов к использованию.
Просто скопируй его в свой проект и убедись что ASSEMBLYAI_KEY указан в config.py

Как работает:
1. Загружает аудио/видео файл на сервер AssemblyAI
2. Запускает транскрибацию
3. Ожидает завершения
4. Возвращает текст

Первые 5 часов транскрибации бесплатно!
"""

import aiohttp
import asyncio
import os
from config import ASSEMBLYAI_KEY


# Базовый URL AssemblyAI API
ASSEMBLY_BASE_URL = "https://api.assemblyai.com/v2"

# Заголовки для запросов
HEADERS = {
    "authorization": ASSEMBLYAI_KEY,
    "content-type": "application/json"
}


async def upload_file(file_path: str) -> str:
    """
    Загружает файл на сервер AssemblyAI
    
    Args:
        file_path: Путь к локальному файлу
        
    Returns:
        URL загруженного файла
    """
    upload_url = f"{ASSEMBLY_BASE_URL}/upload"
    
    async with aiohttp.ClientSession() as session:
        with open(file_path, "rb") as f:
            async with session.post(
                upload_url,
                headers={"authorization": ASSEMBLYAI_KEY},
                data=f
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"Ошибка загрузки файла: {error_text}")
                
                result = await response.json()
                return result["upload_url"]


async def start_transcription(audio_url: str, language_code: str = "ru") -> str:
    """
    Запускает транскрибацию аудио
    
    Args:
        audio_url: URL аудиофайла на сервере AssemblyAI
        language_code: Код языка (ru, en и т.д.)
        
    Returns:
        ID задачи транскрибации
    """
    transcript_url = f"{ASSEMBLY_BASE_URL}/transcript"
    
    payload = {
        "audio_url": audio_url,
        "language_code": language_code
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(
            transcript_url,
            headers=HEADERS,
            json=payload
        ) as response:
            if response.status != 200:
                error_text = await response.text()
                raise Exception(f"Ошибка запуска транскрибации: {error_text}")
            
            result = await response.json()
            return result["id"]


async def get_transcription_result(transcript_id: str) -> dict:
    """
    Получает результат транскрибации
    
    Args:
        transcript_id: ID задачи транскрибации
        
    Returns:
        Словарь с результатом
    """
    result_url = f"{ASSEMBLY_BASE_URL}/transcript/{transcript_id}"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(result_url, headers=HEADERS) as response:
            return await response.json()


async def wait_for_transcription(transcript_id: str, poll_interval: int = 3) -> str:
    """
    Ожидает завершения транскрибации
    
    Args:
        transcript_id: ID задачи транскрибации
        poll_interval: Интервал проверки в секундах
        
    Returns:
        Текст транскрипта
    """
    while True:
        result = await get_transcription_result(transcript_id)
        status = result["status"]
        
        if status == "completed":
            return result["text"]
        elif status == "error":
            raise Exception(f"Ошибка транскрибации: {result.get('error', 'Unknown error')}")
        
        # Ждём и проверяем снова
        await asyncio.sleep(poll_interval)


async def transcribe_voice(file_path: str, language: str = "ru") -> str:
    """
    Транскрибирует голосовое сообщение
    
    Args:
        file_path: Путь к аудиофайлу
        language: Язык (ru, en, etc.)
        
    Returns:
        Текст транскрипта
        
    Example:
        transcript = await transcribe_voice("voice.ogg", "ru")
        print(transcript)
    """
    try:
        # 1. Загружаем файл
        print(f"📤 Загрузка файла: {file_path}")
        audio_url = await upload_file(file_path)
        
        # 2. Запускаем транскрибацию
        print("🎙️ Запуск транскрибации...")
        transcript_id = await start_transcription(audio_url, language)
        
        # 3. Ожидаем результат
        print("⏳ Ожидание результата...")
        transcript = await wait_for_transcription(transcript_id)
        
        print("✅ Транскрибация завершена!")
        return transcript
        
    except Exception as e:
        print(f"❌ Ошибка транскрибации: {e}")
        raise


async def transcribe_video(file_path: str, language: str = "ru") -> str:
    """
    Транскрибирует видео файл
    
    Args:
        file_path: Путь к видеофайлу
        language: Язык (ru, en, etc.)
        
    Returns:
        Текст транскрипта
        
    Note:
        AssemblyAI автоматически извлекает аудио из видео.
        Поддерживаемые форматы: MP4, MOV, AVI и другие.
    """
    # Для видео используем тот же процесс — AssemblyAI сам извлечёт аудио
    return await transcribe_voice(file_path, language)


# Пример использования (для тестирования)
if __name__ == "__main__":
    async def test():
        # Тест транскрибации (замени путь на реальный файл)
        test_file = "test_audio.ogg"
        
        if os.path.exists(test_file):
            transcript = await transcribe_voice(test_file)
            print(f"Транскрипт: {transcript}")
        else:
            print(f"Тестовый файл не найден: {test_file}")
            print("Создай тестовый аудиофайл для проверки")
    
    asyncio.run(test())

