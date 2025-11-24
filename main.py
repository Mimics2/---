import asyncio
import logging
import sqlite3
import os
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message, WebAppInfo
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import aiohttp

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация из переменных окружения (для хостинга)
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_IDS = list(map(int, os.getenv('ADMIN_IDS', '').split(','))) if os.getenv('ADMIN_IDS') else []

if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не установлен")
    exit(1)

# Инициализация бота с настройками для хостинга
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# Глобальные переменные для хранения данных в памяти (для скорости)
keywords_cache = []
monitored_chats_cache = []
last_update = None

# Инициализация базы данных
def init_db():
    try:
        conn = sqlite3.connect('monitoring.db', check_same_thread=False)
        cursor = conn.cursor()
        
        # Таблица ключевых слов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS keywords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT UNIQUE,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица отслеживаемых чатов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS monitored_chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT UNIQUE,
                chat_name TEXT,
                chat_type TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица найденных сообщений
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS found_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER,
                chat_id TEXT,
                chat_name TEXT,
                user_id TEXT,
                username TEXT,
                message_text TEXT,
                keywords_found TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                source_type TEXT
            )
        ''')
        
        # Добавляем тестовые данные если пусто
        cursor.execute("INSERT OR IGNORE INTO keywords (keyword) VALUES (?)", ('тест',))
        cursor.execute("INSERT OR IGNORE INTO keywords (keyword) VALUES (?)", ('мониторинг',))
        cursor.execute("INSERT OR IGNORE INTO keywords (keyword) VALUES (?)", ('ключевое слово',))
        
        conn.commit()
        conn.close()
        logger.info("✅ База данных инициализирована")
        
        # Обновляем кеш
        update_cache()
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")

# Функции для работы с кешем
def update_cache():
    """Обновление кеша данных из БД"""
    global keywords_cache, monitored_chats_cache, last_update
    
    try:
        conn = sqlite3.connect('monitoring.db', check_same_thread=False)
        cursor = conn.cursor()
        
        # Кешируем ключевые слова
        cursor.execute("SELECT keyword FROM keywords WHERE is_active = 1")
        keywords_cache = [row[0] for row in cursor.fetchall()]
        
        # Кешируем отслеживаемые чаты
        cursor.execute("SELECT chat_id, chat_name FROM monitored_chats WHERE is_active = 1")
        monitored_chats_cache = [{"id": row[0], "name": row[1]} for row in cursor.fetchall()]
        
        conn.close()
        last_update = datetime.now()
        logger.info(f"✅ Кеш обновлен: {len(keywords_cache)} ключ. слов, {len(monitored_chats_cache)} чатов")
        
    except Exception as e:
        logger.error(f"❌ Ошибка обновления кеша: {e}")

def get_keywords():
    """Получение ключевых слов из кеша"""
    return keywords_cache

def get_monitored_chats():
    """Получение списка чатов из кеша"""
    return monitored_chats_cache

# Фильтр для проверки администратора
async def admin_filter(message: Message) -> bool:
    return message.from_user.id in ADMIN_IDS

# Команды администратора
@dp.message(Command("start"))
async def cmd_start(message: Message):
    if not await admin_filter(message):
        await message.answer("❌ Доступ запрещен")
        return
        
    await message.answer(
        "🔍 <b>Система мониторинга сообщений</b>\n\n"
        "📊 <b>Команды:</b>\n"
        "/add_keyword - добавить ключевое слово\n"
        "/del_keyword - удалить ключевое слово\n"
        "/keywords - список ключевых слов\n"
        "/add_chat - добавить чат для мониторинга\n"
        "/chats - список чатов\n"
        "/stats - статистика\n"
        "/update - обновить кеш\n"
        "/logs - последние найденные сообщения\n\n"
        "⚡ <b>Система работает в реальном времени!</b>"
    )

@dp.message(Command("update"))
async def cmd_update(message: Message):
    if not await admin_filter(message):
        return
        
    update_cache()
    await message.answer("✅ Кеш обновлен!")

@dp.message(Command("keywords"))
async def cmd_keywords(message: Message):
    if not await admin_filter(message):
        return
        
    keywords = get_keywords()
    if keywords:
        keywords_text = "\n".join([f"• {kw}" for kw in keywords])
        await message.answer(f"🔍 <b>Ключевые слова:</b>\n\n{keywords_text}")
    else:
        await message.answer("📝 Ключевые слова не добавлены")

@dp.message(Command("add_keyword"))
async def cmd_add_keyword(message: Message):
    if not await admin_filter(message):
        return
        
    try:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.answer("📝 Использование: /add_keyword <слово>")
            return
            
        keyword = args[1].strip().lower()
        
        conn = sqlite3.connect('monitoring.db', check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO keywords (keyword) VALUES (?)",
            (keyword,)
        )
        conn.commit()
        conn.close()
        
        update_cache()  # Обновляем кеш
        await message.answer(f"✅ Ключевое слово '<code>{keyword}</code>' добавлено")
        
    except Exception as e:
        logger.error(f"Ошибка добавления ключевого слова: {e}")
        await message.answer("❌ Ошибка при добавлении")

@dp.message(Command("del_keyword"))
async def cmd_del_keyword(message: Message):
    if not await admin_filter(message):
        return
        
    try:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.answer("📝 Использование: /del_keyword <слово>")
            return
            
        keyword = args[1].strip().lower()
        
        conn = sqlite3.connect('monitoring.db', check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM keywords WHERE keyword = ?",
            (keyword,)
        )
        conn.commit()
        conn.close()
        
        update_cache()  # Обновляем кеш
        await message.answer(f"✅ Ключевое слово '<code>{keyword}</code>' удалено")
        
    except Exception as e:
        logger.error(f"Ошибка удаления ключевого слова: {e}")
        await message.answer("❌ Ошибка при удалении")

@dp.message(Command("add_chat"))
async def cmd_add_chat(message: Message):
    if not await admin_filter(message):
        return
        
    try:
        chat_id = str(message.chat.id)
        chat_name = message.chat.title or f"ЛС: {message.from_user.full_name}"
        chat_type = message.chat.type
        
        conn = sqlite3.connect('monitoring.db', check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO monitored_chats (chat_id, chat_name, chat_type) VALUES (?, ?, ?)",
            (chat_id, chat_name, chat_type)
        )
        conn.commit()
        conn.close()
        
        update_cache()  # Обновляем кеш
        await message.answer(f"✅ Чат '<code>{chat_name}</code>' добавлен в мониторинг")
        
    except Exception as e:
        logger.error(f"Ошибка добавления чата: {e}")
        await message.answer("❌ Ошибка при добавлении чата")

@dp.message(Command("chats"))
async def cmd_chats(message: Message):
    if not await admin_filter(message):
        return
        
    chats = get_monitored_chats()
    if chats:
        chats_text = "\n".join([f"• {chat['name']} (<code>{chat['id']}</code>)" for chat in chats])
        await message.answer(f"📊 <b>Отслеживаемые чаты:</b>\n\n{chats_text}")
    else:
        await message.answer("📝 Чаты не добавлены")

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if not await admin_filter(message):
        return
        
    try:
        conn = sqlite3.connect('monitoring.db', check_same_thread=False)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM keywords WHERE is_active = 1")
        keywords_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM monitored_chats WHERE is_active = 1")
        chats_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM found_messages")
        messages_count = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT keywords_found, COUNT(*) as count 
            FROM found_messages 
            GROUP BY keywords_found 
            ORDER BY count DESC 
            LIMIT 5
        """)
        top_keywords = cursor.fetchall()
        
        conn.close()
        
        stats_text = (
            f"📊 <b>Статистика мониторинга</b>\n\n"
            f"🔍 <b>Ключевых слов:</b> {keywords_count}\n"
            f"📁 <b>Отслеживаемых чатов:</b> {chats_count}\n"
            f"💬 <b>Найдено сообщений:</b> {messages_count}\n"
            f"🕒 <b>Последнее обновление:</b> {last_update.strftime('%H:%M:%S') if last_update else 'N/A'}\n\n"
            f"🏆 <b>Топ ключевых слов:</b>\n"
        )
        
        for keyword, count in top_keywords:
            stats_text += f"• {keyword}: {count}\n"
            
        await message.answer(stats_text)
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        await message.answer("❌ Ошибка получения статистики")

@dp.message(Command("logs"))
async def cmd_logs(message: Message):
    if not await admin_filter(message):
        return
        
    try:
        conn = sqlite3.connect('monitoring.db', check_same_thread=False)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT chat_name, keywords_found, message_text, timestamp 
            FROM found_messages 
            ORDER BY timestamp DESC 
            LIMIT 10
        """)
        logs = cursor.fetchall()
        conn.close()
        
        if logs:
            logs_text = "📋 <b>Последние найденные сообщения:</b>\n\n"
            for chat_name, keywords, msg_text, timestamp in logs:
                logs_text += (
                    f"📁 <b>Чат:</b> {chat_name}\n"
                    f"🔍 <b>Ключи:</b> {keywords}\n"
                    f"💬 <b>Сообщение:</b> {msg_text[:50]}...\n"
                    f"🕒 <b>Время:</b> {timestamp}\n"
                    f"────────────────────\n"
                )
            await message.answer(logs_text)
        else:
            await message.answer("📝 Сообщения не найдены")
            
    except Exception as e:
        logger.error(f"Ошибка получения логов: {e}")
        await message.answer("❌ Ошибка получения логов")

# Основной обработчик сообщений
@dp.message(F.text)
async def monitor_messages(message: Message):
    """Мониторинг всех сообщений на ключевые слова"""
    
    try:
        message_text = message.text.lower()
        keywords = get_keywords()
        found_keywords = [kw for kw in keywords if kw in message_text]
        
        if found_keywords:
            # Сохраняем найденное сообщение
            conn = sqlite3.connect('monitoring.db', check_same_thread=False)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO found_messages 
                (message_id, chat_id, chat_name, user_id, username, message_text, keywords_found, source_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                message.message_id,
                str(message.chat.id),
                message.chat.title or f"ЛС: {message.from_user.full_name}",
                str(message.from_user.id),
                message.from_user.username,
                message.text,
                ', '.join(found_keywords),
                message.chat.type
            ))
            
            conn.commit()
            conn.close()
            
            # Уведомляем администраторов
            alert_text = (
                f"🚨 <b>Найдено ключевое слово!</b>\n\n"
                f"📁 <b>Чат:</b> {message.chat.title or 'ЛС'}\n"
                f"👤 <b>Пользователь:</b> {message.from_user.full_name}\n"
                f"🔍 <b>Ключевые слова:</b> {', '.join(found_keywords)}\n"
                f"💬 <b>Сообщение:</b> {message.text[:100]}..."
            )
            
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(admin_id, alert_text)
                except Exception as e:
                    logger.error(f"Ошибка отправки админу {admin_id}: {e}")
            
            logger.info(f"🔍 Найдены ключи: {found_keywords} в чате {message.chat.id}")
            
    except Exception as e:
        logger.error(f"❌ Ошибка мониторинга: {e}")

# Обработчик для сервисных сообщений
@dp.message()
async def handle_other_messages(message: Message):
    """Обработка медиа и сервисных сообщений"""
    pass

# Функция для поддержания активности на хостинге
async def keep_alive():
    """Периодическая проверка активности"""
    while True:
        try:
            # Простая проверка что бот жив
            await bot.get_me()
            logger.info("🤖 Бот активен")
        except Exception as e:
            logger.error(f"❌ Ошибка активности: {e}")
        
        await asyncio.sleep(300)  # Проверка каждые 5 минут

async def main():
    """Основная функция запуска"""
    logger.info("🚀 Запуск бота мониторинга...")
    
    # Инициализация БД
    init_db()
    
    # Запуск фоновой задачи поддержания активности
    asyncio.create_task(keep_alive())
    
    # Удаляем вебхук (на всякий случай) и запускаем поллинг
    await bot.delete_webhook(drop_pending_updates=True)
    
    logger.info("✅ Бот запущен и готов к мониторингу!")
    
    # Запускаем поллинг
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
