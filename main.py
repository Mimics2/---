import os
import asyncio
import logging
import sqlite3
import json
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

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

def init_db():
    """Инициализация базы данных для многопользовательской работы"""
    try:
        db_path = '/data/monitoring.db' if os.path.exists('/data') else 'monitoring.db'
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Пользователи и их сессии
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE,
                username TEXT,
                first_name TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Сессии пользователей (аккаунты для мониторинга)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                session_name TEXT,
                api_id INTEGER,
                api_hash TEXT,
                phone_number TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        # Ключевые слова для каждого пользователя
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_keywords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                keyword TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, keyword),
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        # Отслеживаемые чаты для каждого пользователя
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_monitored_chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id TEXT,
                chat_name TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, chat_id),
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        # Найденные сообщения
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS found_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                session_id INTEGER,
                chat_name TEXT,
                username TEXT,
                message_text TEXT,
                keywords_found TEXT,
                source_type TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        # Добавляем админов как пользователей
        for admin_id in ADMIN_IDS:
            cursor.execute(
                "INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
                (admin_id, f"admin_{admin_id}", "Administrator")
            )
        
        conn.commit()
        conn.close()
        logger.info("✅ Многопользовательская БД инициализирована")
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")

def get_db_connection():
    """Получение соединения с БД"""
    db_path = '/data/monitoring.db' if os.path.exists('/data') else 'monitoring.db'
    return sqlite3.connect(db_path, check_same_thread=False)

def get_user_keywords(user_id: int):
    """Получение ключевых слов пользователя"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT keyword FROM user_keywords WHERE user_id = ? AND is_active = 1", (user_id,))
        keywords = {row[0].lower() for row in cursor.fetchall()}
        conn.close()
        return keywords
    except Exception as e:
        logger.error(f"Ошибка получения ключевых слов для {user_id}: {e}")
        return set()

def get_user_chats(user_id: int):
    """Получение чатов пользователя"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT chat_id FROM user_monitored_chats WHERE user_id = ? AND is_active = 1", (user_id,))
        chats = {row[0] for row in cursor.fetchall()}
        conn.close()
        return chats
    except Exception as e:
        logger.error(f"Ошибка получения чатов для {user_id}: {e}")
        return set()

def add_user(user_id: int, username: str, first_name: str):
    """Добавление нового пользователя"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO users (user_id, username, first_name, is_active) VALUES (?, ?, ?, 1)",
            (user_id, username, first_name)
        )
        conn.commit()
        conn.close()
        logger.info(f"✅ Добавлен пользователь: {user_id} - {first_name}")
        return True
    except Exception as e:
        logger.error(f"Ошибка добавления пользователя {user_id}: {e}")
        return False

def is_user_allowed(user_id: int):
    """Проверка доступа пользователя"""
    return user_id in ADMIN_IDS  # Или можно сделать систему приглашений

# Команды бота
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    
    # Автоматически добавляем пользователя при старте
    add_user(user_id, message.from_user.username, message.from_user.first_name)
    
    if not is_user_allowed(user_id):
        await message.answer(
            "👋 <b>Добро пожаловать!</b>\n\n"
            "🔍 <b>Это бот мониторинга сообщений</b>\n\n"
            "Чтобы начать работу:\n"
            "1. Добавьте ключевые слова: /add_keyword\n"
            "2. Добавьте чаты для мониторинга: /add_chat\n"
            "3. Настройте сессии для мониторинга каналов: /add_session\n\n"
            "⚡ <b>Каждый пользователь работает со своими настройками!</b>"
        )
        return
    
    await message.answer(
        "👋 <b>Добро пожаловать, администратор!</b>\n\n"
        "🔍 <b>Система многопользовательского мониторинга</b>\n\n"
        "<b>Основные команды:</b>\n"
        "/add_keyword - добавить ключевое слово\n"
        "/keywords - мои ключевые слова\n"
        "/add_chat - добавить чат для мониторинга\n"
        "/chats - мои чаты\n"
        "/add_session - добавить сессию для мониторинга каналов\n"
        "/stats - моя статистика\n"
        "/logs - мои последние находки\n\n"
        "⚡ <b>Каждый пользователь имеет свои настройки!</b>"
    )

@dp.message(Command("add_keyword"))
async def cmd_add_keyword(message: Message):
    user_id = message.from_user.id
    add_user(user_id, message.from_user.username, message.from_user.first_name)
    
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ <b>Использование:</b> /add_keyword <слово или фраза>")
        return
    
    keyword = ' '.join(args[1:]).strip().lower()
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO user_keywords (user_id, keyword) VALUES (?, ?)",
            (user_id, keyword)
        )
        conn.commit()
        conn.close()
        
        await message.answer(f"✅ <b>Ключевое слово добавлено:</b> <code>{keyword}</code>")
        logger.info(f"Пользователь {user_id} добавил ключевое слово: {keyword}")
        
    except Exception as e:
        logger.error(f"Ошибка добавления ключевого слова для {user_id}: {e}")
        await message.answer("❌ Ошибка при добавлении ключевого слова")

@dp.message(Command("keywords"))
async def cmd_keywords(message: Message):
    user_id = message.from_user.id
    keywords = get_user_keywords(user_id)
    
    if keywords:
        text = "🔍 <b>Ваши ключевые слова:</b>\n\n" + "\n".join(f"• {kw}" for kw in sorted(keywords))
        await message.answer(text)
    else:
        await message.answer(
            "📝 <b>У вас пока нет ключевых слов</b>\n\n"
            "Добавьте их командой:\n"
            "<code>/add_keyword ваше_слово</code>"
        )

@dp.message(Command("add_chat"))
async def cmd_add_chat(message: Message):
    user_id = message.from_user.id
    add_user(user_id, message.from_user.username, message.from_user.first_name)
    
    chat_id = str(message.chat.id)
    chat_name = message.chat.title or f"ЛС: {message.from_user.full_name}"
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO user_monitored_chats (user_id, chat_id, chat_name) VALUES (?, ?, ?)",
            (user_id, chat_id, chat_name)
        )
        conn.commit()
        conn.close()
        
        await message.answer(
            f"✅ <b>Чат добавлен в мониторинг:</b>\n"
            f"📁 <b>Название:</b> {chat_name}\n"
            f"🆔 <b>ID:</b> <code>{chat_id}</code>"
        )
        logger.info(f"Пользователь {user_id} добавил чат: {chat_name}")
        
    except Exception as e:
        logger.error(f"Ошибка добавления чата для {user_id}: {e}")
        await message.answer("❌ Ошибка при добавлении чата")

@dp.message(Command("chats"))
async def cmd_chats(message: Message):
    user_id = message.from_user.id
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT chat_name, chat_id FROM user_monitored_chats WHERE user_id = ? AND is_active = 1",
            (user_id,)
        )
        chats = cursor.fetchall()
        conn.close()
        
        if chats:
            text = "📁 <b>Ваши чаты для мониторинга:</b>\n\n"
            for chat_name, chat_id in chats:
                text += f"• {chat_name}\n  <code>ID: {chat_id}</code>\n\n"
            await message.answer(text)
        else:
            await message.answer(
                "📝 <b>У вас пока нет чатов для мониторинга</b>\n\n"
                "Добавьте текущий чат командой:\n"
                "<code>/add_chat</code>"
            )
            
    except Exception as e:
        logger.error(f"Ошибка получения чатов для {user_id}: {e}")
        await message.answer("❌ Ошибка получения списка чатов")

@dp.message(Command("add_session"))
async def cmd_add_session(message: Message):
    user_id = message.from_user.id
    add_user(user_id, message.from_user.username, message.from_user.first_name)
    
    await message.answer(
        "🔐 <b>Добавление сессии для мониторинга каналов</b>\n\n"
        "Для мониторинга каналов нужно добавить сессию Telegram.\n\n"
        "📝 <b>Инструкция:</b>\n"
        "1. Получите API_ID и API_HASH на https://my.telegram.org\n"
        "2. Отправьте команду в формате:\n"
        "<code>/session_data название_сессии ваш_api_id ваш_api_hash ваш_номер_телефона</code>\n\n"
        "Пример:\n"
        "<code>/session_data моя_сессия 12345678 abcdef1234567890 +79123456789</code>\n\n"
        "⚠️ <b>Внимание:</b> Номер телефона должен быть с кодом страны"
    )

@dp.message(Command("session_data"))
async def cmd_session_data(message: Message):
    user_id = message.from_user.id
    
    args = message.text.split()
    if len(args) < 5:
        await message.answer("❌ <b>Недостаточно параметров</b>\n\nИспользуйте: /session_data название api_id api_hash номер_телефона")
        return
    
    session_name = args[1]
    api_id = args[2]
    api_hash = args[3]
    phone_number = args[4]
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO user_sessions (user_id, session_name, api_id, api_hash, phone_number) VALUES (?, ?, ?, ?, ?)",
            (user_id, session_name, api_id, api_hash, phone_number)
        )
        conn.commit()
        conn.close()
        
        await message.answer(
            f"✅ <b>Сессия добавлена!</b>\n\n"
            f"📝 <b>Название:</b> {session_name}\n"
            f"🆔 <b>API ID:</b> {api_id}\n"
            f"🔑 <b>API Hash:</b> {api_hash[:10]}...\n"
            f"📞 <b>Телефон:</b> {phone_number}\n\n"
            f"Теперь вы можете мониторить каналы через эту сессию!"
        )
        logger.info(f"Пользователь {user_id} добавил сессию: {session_name}")
        
    except Exception as e:
        logger.error(f"Ошибка добавления сессии для {user_id}: {e}")
        await message.answer("❌ Ошибка при добавлении сессии")

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    user_id = message.from_user.id
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Статистика пользователя
        cursor.execute("SELECT COUNT(*) FROM user_keywords WHERE user_id = ?", (user_id,))
        kw_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM user_monitored_chats WHERE user_id = ?", (user_id,))
        chats_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM user_sessions WHERE user_id = ?", (user_id,))
        sessions_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM found_messages WHERE user_id = ?", (user_id,))
        found_count = cursor.fetchone()[0]
        
        cursor.execute(
            "SELECT keywords_found, COUNT(*) FROM found_messages WHERE user_id = ? GROUP BY keywords_found ORDER BY COUNT(*) DESC LIMIT 5",
            (user_id,)
        )
        top_keywords = cursor.fetchall()
        
        conn.close()
        
        stats_text = (
            f"📊 <b>Ваша статистика мониторинга</b>\n\n"
            f"🔍 <b>Ключевых слов:</b> {kw_count}\n"
            f"📁 <b>Чатов:</b> {chats_count}\n"
            f"🔐 <b>Сессий:</b> {sessions_count}\n"
            f"💬 <b>Найдено сообщений:</b> {found_count}\n\n"
        )
        
        if top_keywords:
            stats_text += "🏆 <b>Ваши топ ключи:</b>\n"
            for keyword, count in top_keywords:
                stats_text += f"• {keyword}: {count}\n"
        
        await message.answer(stats_text)
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики для {user_id}: {e}")
        await message.answer("❌ Ошибка получения статистики")

@dp.message(Command("logs"))
async def cmd_logs(message: Message):
    user_id = message.from_user.id
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT chat_name, keywords_found, message_text, timestamp 
            FROM found_messages 
            WHERE user_id = ?
            ORDER BY timestamp DESC 
            LIMIT 8
        """, (user_id,))
        logs = cursor.fetchall()
        conn.close()
        
        if logs:
            text = "📋 <b>Ваши последние находки:</b>\n\n"
            for chat, keywords, msg, time in logs:
                time_str = datetime.strptime(time, '%Y-%m-%d %H:%M:%S').strftime('%H:%M')
                text += f"📁 {chat}\n🔍 {keywords}\n💬 {msg[:40]}...\n⏰ {time_str}\n━━━━━━━━━━━━━━\n"
            await message.answer(text)
        else:
            await message.answer("📝 <b>Пока ничего не найдено</b>\n\nСообщения появятся здесь, когда система найдет ваши ключевые слова.")
            
    except Exception as e:
        logger.error(f"Ошибка получения логов для {user_id}: {e}")
        await message.answer("❌ Ошибка получения логов")

# Основной мониторинг сообщений
@dp.message(F.text)
async def monitor_all_messages(message: Message):
    """Мониторинг всех сообщений для всех пользователей"""
    
    try:
        user_id = message.from_user.id
        text = message.text.lower()
        
        # Получаем всех пользователей, которые отслеживают этот чат
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT u.user_id 
            FROM users u 
            JOIN user_monitored_chats umc ON u.user_id = umc.user_id 
            WHERE umc.chat_id = ? AND u.is_active = 1
        """, (str(message.chat.id),))
        
        users_to_check = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        # Проверяем для каждого пользователя
        for check_user_id in users_to_check:
            user_keywords = get_user_keywords(check_user_id)
            found_keywords = [kw for kw in user_keywords if kw in text]
            
            if found_keywords:
                # Сохраняем найденное сообщение для пользователя
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO found_messages (user_id, chat_name, username, message_text, keywords_found, source_type) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        check_user_id,
                        message.chat.title or f"ЛС: {message.from_user.full_name}",
                        message.from_user.username or "Нет username",
                        message.text,
                        ', '.join(found_keywords),
                        message.chat.type
                    )
                )
                conn.commit()
                conn.close()
                
                # Отправляем уведомление пользователю
                alert = (
                    f"🚨 <b>Найдено ключевое слово!</b>\n\n"
                    f"📁 <b>Чат:</b> {message.chat.title or 'ЛС'}\n"
                    f"👤 <b>Отправитель:</b> {message.from_user.full_name}\n"
                    f"🔍 <b>Ваши ключи:</b> {', '.join(found_keywords)}\n"
                    f"💬 <b>Текст:</b> {message.text[:80]}..."
                )
                
                try:
                    await bot.send_message(check_user_id, alert)
                    logger.info(f"🔍 Уведомление отправлено пользователю {check_user_id}: {found_keywords}")
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления пользователю {check_user_id}: {e}")
        
        # Логируем общую активность
        if users_to_check:
            logger.info(f"📨 Сообщение в чате {message.chat.id} проверено для {len(users_to_check)} пользователей")
            
    except Exception as e:
        logger.error(f"❌ Ошибка многопользовательского мониторинга: {e}")

# HTTP сервер для проверки здоровья
async def health_check(request):
    return web.Response(text="Multi-user Monitoring Bot is running!")

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
    logger.info("🚀 Запуск многопользовательского бота мониторинга...")
    
    # Инициализация БД
    init_db()
    
    # Запуск HTTP сервера
    await start_http_server()
    
    # Запуск бота
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("✅ Многопользовательский бот запущен и готов к работе!")
    
    # Запускаем поллинг
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
