import os
import asyncio
import logging
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import aiohttp
from aiohttp import web

# Конфигурация для Railway
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_IDS = [int(x.strip()) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip()]
PORT = int(os.getenv('PORT', 8080))

# Проверка обязательных переменных
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен")
if not ADMIN_IDS:
    raise ValueError("ADMIN_IDS не установлены")

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Инициализация бота (ИСПРАВЛЕННАЯ СТРОКА)
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)  # ← ИСПРАВЛЕНО: HTML вместо HTHTML
)
dp = Dispatcher()

# Глобальный кеш для быстрого доступа
keywords_cache = set()
monitored_chats_cache = set()

def init_db():
    """Инициализация базы данных"""
    try:
        # Используем /data для постоянного хранения на Railway
        db_path = '/data/monitoring.db' if os.path.exists('/data') else 'monitoring.db'
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS keywords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS monitored_chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT UNIQUE,
                chat_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS found_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_name TEXT,
                username TEXT,
                message_text TEXT,
                keywords_found TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Добавляем начальные данные
        initial_keywords = ['тест', 'мониторинг', 'ключевое слово', 'проверка']
        for keyword in initial_keywords:
            cursor.execute("INSERT OR IGNORE INTO keywords (keyword) VALUES (?)", (keyword,))
        
        conn.commit()
        conn.close()
        
        update_cache()
        logger.info("✅ База данных инициализирована")
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")

def update_cache():
    """Обновление кеша данных"""
    global keywords_cache, monitored_chats_cache
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT keyword FROM keywords")
        keywords_cache = {row[0].lower() for row in cursor.fetchall()}
        
        cursor.execute("SELECT chat_id FROM monitored_chats")
        monitored_chats_cache = {row[0] for row in cursor.fetchall()}
        
        conn.close()
        logger.info(f"✅ Кеш обновлен: {len(keywords_cache)} ключей, {len(monitored_chats_cache)} чатов")
        
    except Exception as e:
        logger.error(f"❌ Ошибка обновления кеша: {e}")

def get_db_connection():
    """Получение соединения с БД"""
    db_path = '/data/monitoring.db' if os.path.exists('/data') else 'monitoring.db'
    return sqlite3.connect(db_path, check_same_thread=False)

# Команды бота
@dp.message(Command("start"))
async def cmd_start(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Доступ запрещен")
        return
    
    await message.answer(
        "🔍 <b>Бот мониторинга запущен на Railway</b>\n\n"
        "<b>Команды:</b>\n"
        "/add_keyword - добавить слово\n"  
        "/keywords - список слов\n"
        "/add_chat - добавить чат\n"
        "/stats - статистика\n"
        "/logs - последние находки\n\n"
        "⚡ <b>Работает в реальном времени!</b>"
    )

@dp.message(Command("add_keyword"))
async def cmd_add_keyword(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Используйте: /add_keyword <слово>")
        return
    
    keyword = args[1].strip().lower()
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO keywords (keyword) VALUES (?)", (keyword,))
        conn.commit()
        conn.close()
        
        update_cache()
        await message.answer(f"✅ Добавлено: <code>{keyword}</code>")
        
    except Exception as e:
        logger.error(f"Ошибка добавления ключевого слова: {e}")
        await message.answer("❌ Ошибка добавления")

@dp.message(Command("keywords"))
async def cmd_keywords(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    keywords = list(keywords_cache)
    if keywords:
        text = "🔍 <b>Ключевые слова:</b>\n\n" + "\n".join(f"• {kw}" for kw in sorted(keywords))
        await message.answer(text)
    else:
        await message.answer("📝 Слова не добавлены")

@dp.message(Command("add_chat"))
async def cmd_add_chat(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    chat_id = str(message.chat.id)
    chat_name = message.chat.title or f"ЛС: {message.from_user.full_name}"
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO monitored_chats (chat_id, chat_name) VALUES (?, ?)",
            (chat_id, chat_name)
        )
        conn.commit()
        conn.close()
        
        update_cache()
        await message.answer(f"✅ Чат добавлен: <code>{chat_name}</code>")
        
    except Exception as e:
        logger.error(f"Ошибка добавления чата: {e}")
        await message.answer("❌ Ошибка добавления чата")

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM keywords")
        kw_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM monitored_chats")
        chats_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM found_messages")
        found_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT keywords_found, COUNT(*) FROM found_messages GROUP BY keywords_found ORDER BY COUNT(*) DESC LIMIT 5")
        top_keywords = cursor.fetchall()
        
        conn.close()
        
        stats_text = (
            f"📊 <b>Статистика мониторинга</b>\n\n"
            f"🔍 <b>Ключевых слов:</b> {kw_count}\n"
            f"📁 <b>Чатов:</b> {chats_count}\n"
            f"💬 <b>Найдено сообщений:</b> {found_count}\n\n"
            f"🏆 <b>Топ ключей:</b>\n"
        )
        
        for keyword, count in top_keywords:
            stats_text += f"• {keyword}: {count}\n"
            
        await message.answer(stats_text)
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        await message.answer("❌ Ошибка статистики")

@dp.message(Command("logs"))
async def cmd_logs(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT chat_name, keywords_found, message_text, timestamp 
            FROM found_messages 
            ORDER BY timestamp DESC 
            LIMIT 8
        """)
        logs = cursor.fetchall()
        conn.close()
        
        if logs:
            text = "📋 <b>Последние находки:</b>\n\n"
            for chat, keywords, msg, time in logs:
                time_str = datetime.strptime(time, '%Y-%m-%d %H:%M:%S').strftime('%H:%M')
                text += f"📁 {chat}\n🔍 {keywords}\n💬 {msg[:40]}...\n⏰ {time_str}\n━━━━━━━━━━━━━━\n"
            await message.answer(text)
        else:
            await message.answer("📝 Пока ничего не найдено")
            
    except Exception as e:
        logger.error(f"Ошибка получения логов: {e}")
        await message.answer("❌ Ошибка логов")

# Основной мониторинг
@dp.message(F.text)
async def monitor_all_messages(message: Message):
    """Мониторинг всех сообщений"""
    
    try:
        text = message.text.lower()
        found_keywords = [kw for kw in keywords_cache if kw in text]
        
        if found_keywords:
            # Сохраняем в БД
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO found_messages (chat_name, username, message_text, keywords_found) VALUES (?, ?, ?, ?)",
                (
                    message.chat.title or f"ЛС: {message.from_user.full_name}",
                    message.from_user.username or "Нет username",
                    message.text,
                    ', '.join(found_keywords)
                )
            )
            conn.commit()
            conn.close()
            
            # Уведомляем админов
            alert = (
                f"🚨 <b>Найдено ключевое слово!</b>\n\n"
                f"📁 <b>Чат:</b> {message.chat.title or 'ЛС'}\n"
                f"👤 <b>Юзер:</b> {message.from_user.full_name}\n"
                f"🔍 <b>Ключи:</b> {', '.join(found_keywords)}\n"
                f"💬 <b>Текст:</b> {message.text[:80]}..."
            )
            
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(admin_id, alert)
                except Exception as e:
                    logger.error(f"Ошибка отправки админу {admin_id}: {e}")
            
            logger.info(f"🔍 Найдены ключи: {found_keywords} в чате {message.chat.id}")
            
    except Exception as e:
        logger.error(f"❌ Ошибка мониторинга: {e}")

# HTTP сервер для проверки здоровья
async def health_check(request):
    return web.Response(text="Bot is running!")

async def start_http_server():
    """Запуск HTTP сервера для Railway"""
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"✅ HTTP сервер запущен на порту {PORT}")

async def main():
    """Основная функция запуска"""
    logger.info("🚀 Запуск бота на Railway...")
    
    # Инициализация БД
    init_db()
    
    # Запуск HTTP сервера
    await start_http_server()
    
    # Запуск бота
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("✅ Бот запущен и готов к мониторингу!")
    
    # Запускаем поллинг
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
