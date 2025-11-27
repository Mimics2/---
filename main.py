import os
import asyncio
import logging
import sqlite3
import re
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneNumberInvalidError
import aiohttp
from aiohttp import web
import time

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

# Словарь для хранения активных клиентов Telethon
active_clients = {}

# Простой флуд-контроль
user_last_message = {}

async def safe_send_message(user_id: int, text: str, reply_markup=None):
    """Безопасная отправка сообщения с базовым флуд-контролем"""
    try:
        current_time = time.time()
        last_time = user_last_message.get(user_id, 0)
        
        # Задержка 0.5 секунды между сообщениями одному пользователю
        time_since_last = current_time - last_time
        if time_since_last < 0.5:
            await asyncio.sleep(0.5 - time_since_last)
        
        await bot.send_message(user_id, text, reply_markup=reply_markup, parse_mode=None)
        user_last_message[user_id] = time.time()
        logger.debug(f"📤 Сообщение отправлено пользователю {user_id}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка отправки сообщения {user_id}: {e}")

def init_db():
    """Инициализация базы данных"""
    try:
        db_path = '/data/monitoring.db' if os.path.exists('/data') else 'monitoring.db'
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Пользователи
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
        
        # Белый список пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS allowed_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE,
                username TEXT,
                added_by INTEGER,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Сессии пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                session_name TEXT,
                session_string TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id),
                UNIQUE(user_id, session_name)
            )
        ''')
        
        # Ключевые слова пользователей
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
        
        # Исключения пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_exceptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                exception_word TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, exception_word),
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        # Сообщения пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                session_id INTEGER,
                chat_id TEXT,
                chat_name TEXT,
                username TEXT,
                message_text TEXT,
                has_keywords BOOLEAN DEFAULT 0,
                keywords_found TEXT,
                message_type TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        # Добавляем админов в белый список
        for admin_id in ADMIN_IDS:
            cursor.execute("INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)", 
                         (admin_id, f"admin_{admin_id}", "Administrator"))
            cursor.execute("INSERT OR IGNORE INTO allowed_users (user_id, username, added_by) VALUES (?, ?, ?)", 
                         (admin_id, f"admin_{admin_id}", admin_id))
        
        conn.commit()
        conn.close()
        logger.info("📊 База данных инициализирована")
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")

def get_db_connection():
    """Получение соединения с БД"""
    db_path = '/data/monitoring.db' if os.path.exists('/data') else 'monitoring.db'
    return sqlite3.connect(db_path, check_same_thread=False)

def is_user_allowed(user_id: int):
    """Проверка доступа пользователя"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM allowed_users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone() is not None
        conn.close()
        return result
    except Exception as e:
        logger.error(f"❌ Ошибка проверки доступа для {user_id}: {e}")
        return False

def add_user_to_whitelist(user_id: int, username: str, added_by: int):
    """Добавление пользователя в белый список"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)", 
                      (user_id, username, f"User_{user_id}"))
        cursor.execute("INSERT OR IGNORE INTO allowed_users (user_id, username, added_by) VALUES (?, ?, ?)", 
                      (user_id, username, added_by))
        conn.commit()
        conn.close()
        logger.info(f"✅ Пользователь {user_id} добавлен в белый список")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка добавления в белый список {user_id}: {e}")
        return False

def remove_user_from_whitelist(user_id: int):
    """Удаление пользователя из белого списка"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM allowed_users WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        logger.info(f"🗑️ Пользователь {user_id} удален из белого списка")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка удаления из белого списка {user_id}: {e}")
        return False

def get_allowed_users():
    """Получение списка всех пользователей с доступом"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT au.user_id, au.username, u.first_name, au.added_at 
            FROM allowed_users au 
            LEFT JOIN users u ON au.user_id = u.user_id
            ORDER BY au.added_at DESC
        """)
        users = cursor.fetchall()
        conn.close()
        return users
    except Exception as e:
        logger.error(f"❌ Ошибка получения списка пользователей: {e}")
        return []

def get_user_sessions(user_id: int):
    """Получение сессий пользователя"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, session_name, session_string, is_active FROM user_sessions WHERE user_id = ?",
            (user_id,)
        )
        sessions = cursor.fetchall()
        conn.close()
        return sessions
    except Exception as e:
        logger.error(f"❌ Ошибка получения сессий для {user_id}: {e}")
        return []

def save_user_session(user_id: int, session_name: str, session_string: str):
    """Сохранение сессии пользователя"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO user_sessions (user_id, session_name, session_string) VALUES (?, ?, ?)",
            (user_id, session_name, session_string)
        )
        conn.commit()
        conn.close()
        logger.info(f"💾 Сессия сохранена для {user_id}: {session_name}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения сессии для {user_id}: {e}")
        return False

def add_user_keywords(user_id: int, keywords_text: str):
    """Добавление ключевых слов через запятую"""
    try:
        # Разделяем текст по запятым и очищаем от пробелов
        keywords = [kw.strip() for kw in keywords_text.split(',') if kw.strip()]
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        added_count = 0
        for keyword in keywords:
            try:
                cursor.execute(
                    "INSERT OR IGNORE INTO user_keywords (user_id, keyword) VALUES (?, ?)",
                    (user_id, keyword)
                )
                added_count += 1
            except:
                continue
        
        conn.commit()
        conn.close()
        
        logger.info(f"🔍 Пользователь {user_id} добавил {added_count} ключевых слов")
        return added_count, keywords
        
    except Exception as e:
        logger.error(f"❌ Ошибка добавления ключевых слов для {user_id}: {e}")
        return 0, []

def add_user_exceptions(user_id: int, exceptions_text: str):
    """Добавление исключений через запятую"""
    try:
        # Разделяем текст по запятым и очищаем от пробелов
        exceptions = [exc.strip() for exc in exceptions_text.split(',') if exc.strip()]
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        added_count = 0
        for exception in exceptions:
            try:
                cursor.execute(
                    "INSERT OR IGNORE INTO user_exceptions (user_id, exception_word) VALUES (?, ?)",
                    (user_id, exception)
                )
                added_count += 1
            except:
                continue
        
        conn.commit()
        conn.close()
        
        logger.info(f"🚫 Пользователь {user_id} добавил {added_count} исключений")
        return added_count, exceptions
        
    except Exception as e:
        logger.error(f"❌ Ошибка добавления исключений для {user_id}: {e}")
        return 0, []

def get_user_keywords(user_id: int):
    """Получение ключевых слов пользователя с ID"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, keyword FROM user_keywords WHERE user_id = ? AND is_active = 1 ORDER BY id", (user_id,))
        keywords = cursor.fetchall()
        conn.close()
        return keywords
    except Exception as e:
        logger.error(f"❌ Ошибка получения ключевых слов для {user_id}: {e}")
        return []

def get_user_exceptions(user_id: int):
    """Получение слов-исключений пользователя с ID"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, exception_word FROM user_exceptions WHERE user_id = ? AND is_active = 1 ORDER BY id", (user_id,))
        exceptions = cursor.fetchall()
        conn.close()
        return exceptions
    except Exception as e:
        logger.error(f"❌ Ошибка получения исключений для {user_id}: {e}")
        return []

def delete_user_keyword(user_id: int, keyword_id: int):
    """Удаление ключевого слова"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_keywords WHERE id = ? AND user_id = ?", (keyword_id, user_id))
        conn.commit()
        conn.close()
        logger.info(f"🗑️ Пользователь {user_id} удалил ключевое слово ID: {keyword_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка удаления ключевого слова: {e}")
        return False

def delete_user_exception(user_id: int, exception_id: int):
    """Удаление исключения"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_exceptions WHERE id = ? AND user_id = ?", (exception_id, user_id))
        conn.commit()
        conn.close()
        logger.info(f"🗑️ Пользователь {user_id} удалил исключение ID: {exception_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка удаления исключения: {e}")
        return False

def clear_all_keywords(user_id: int):
    """Очистка всех ключевых слов"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_keywords WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        logger.info(f"🧹 Пользователь {user_id} очистил все ключевые слова")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка очистки ключевых слов: {e}")
        return False

def clear_all_exceptions(user_id: int):
    """Очистка всех исключений"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_exceptions WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        logger.info(f"🧹 Пользователь {user_id} очистил все исключения")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка очистки исключений: {e}")
        return False

def save_user_message(user_id: int, message_data: dict):
    """Сохранение сообщения пользователя"""
    try:
        clean_text = re.sub(r'\*{2,}', '', message_data['message_text'])
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO user_messages 
            (user_id, session_id, chat_id, chat_name, username, message_text, has_keywords, keywords_found, message_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            user_id,
            message_data.get('session_id', 0),
            message_data['chat_id'],
            message_data['chat_name'],
            message_data['username'],
            clean_text,
            message_data['has_keywords'],
            message_data['keywords_found'],
            message_data['message_type']
        ))
        conn.commit()
        conn.close()
        logger.info(f"💬 Сообщение сохранено для {user_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения сообщения для {user_id}: {e}")

async def check_keywords_for_user(user_id: int, text: str):
    """Проверка ключевых слов и исключений"""
    if not text:
        return False, []
    
    clean_text = re.sub(r'\*{2,}', '', text)
    keywords_data = get_user_keywords(user_id)
    exceptions_data = get_user_exceptions(user_id)
    
    keywords = {row[1].lower() for row in keywords_data}
    exceptions = {row[1].lower() for row in exceptions_data}
    
    text_lower = clean_text.lower()
    
    # Проверяем исключения
    has_exceptions = any(exc in text_lower for exc in exceptions)
    if has_exceptions:
        return False, []
    
    # Проверяем ключевые слова
    found_keywords = [kw for kw in keywords if kw in text_lower]
    return len(found_keywords) > 0, found_keywords

async def test_session(session_string: str):
    """Тестирование сессии перед запуском"""
    try:
        client = TelegramClient(
            StringSession(session_string),
            api_id=2040,
            api_hash='b18441a1ff607e10a989891a5462e627'
        )
        
        await client.start()
        me = await client.get_me()
        await client.disconnect()
        
        return True, f"✅ Сессия валидна: @{me.username}"
    except Exception as e:
        error_msg = str(e)
        if "EOF when reading a line" in error_msg:
            return False, "❌ Сессия невалидна или устарела. Получите новую сессию"
        else:
            return False, f"❌ Ошибка сессии: {error_msg}"

async def process_message_for_user(user_id: int, session_id: int, session_name: str, event):
    """Обработка сообщения для пользователя (вынесено в отдельную функцию)"""
    try:
        if not event.message.text:
            return
        
        # Получаем информацию о чате
        chat = await event.get_chat()
        chat_id = str(chat.id)
        chat_name = getattr(chat, 'title', 'Unknown Chat')
        
        # Получаем информацию об отправителе
        sender = await event.get_sender()
        username = getattr(sender, 'username', 'Unknown')
        
        message_text = event.message.text
        
        # Проверяем ключевые слова
        has_keywords, found_keywords = await check_keywords_for_user(user_id, message_text)
        
        # Сохраняем сообщение
        message_data = {
            'session_id': session_id,
            'chat_id': chat_id,
            'chat_name': chat_name,
            'username': username,
            'message_text': message_text,
            'has_keywords': has_keywords,
            'keywords_found': ', '.join(found_keywords) if found_keywords else '',
            'message_type': 'channel' if hasattr(chat, 'broadcast') else 'group'
        }
        
        save_user_message(user_id, message_data)
        
        # Отправляем уведомление если есть ключевые слова
        if has_keywords and found_keywords:
            clean_message = re.sub(r'\*{2,}', '', message_text)
            
            # Форматируем username с @ для удобного перехода
            username_display = f"@{username}" if username and username != "Unknown" else "Неизвестный"
            
            alert_text = (
                f"🚨 Найдено ключевое слово!\n\n"
                f"📱 Чат: {chat_name}\n"
                f"👤 Отправитель: {username_display}\n"
                f"🔍 Ключи: {', '.join(found_keywords)}\n"
                f"💬 Сообщение: {clean_message[:150]}...\n"
                f"🔐 Сессия: {session_name}"
            )
            
            try:
                await safe_send_message(user_id, alert_text)
                logger.info(f"🔔 Уведомление отправлено {user_id}: {found_keywords}")
            except Exception as e:
                logger.error(f"❌ Ошибка отправки: {e}")
                    
    except Exception as e:
        logger.error(f"❌ Ошибка обработки сообщения: {e}")

async def start_user_session(user_id: int, session_id: int, session_name: str, session_string: str):
    """Запуск мониторинга для сессии пользователя"""
    try:
        # Тестируем сессию перед запуском
        is_valid, message = await test_session(session_string)
        if not is_valid:
            await safe_send_message(user_id, f"❌ Не удалось запустить сессию '{session_name}': {message}")
            return False

        # Создаем клиента Telethon
        client = TelegramClient(
            StringSession(session_string),
            api_id=2040,
            api_hash='b18441a1ff607e10a989891a5462e627'
        )
        
        @client.on(events.NewMessage)
        async def handle_user_messages(event):
            """Обработчик сообщений - только добавляет задачу в event loop"""
            # Создаем задачу для обработки сообщения, не блокируя основной поток
            asyncio.create_task(
                process_message_for_user(user_id, session_id, session_name, event)
            )
        
        # Запускаем клиента в отдельной задаче
        async def run_client():
            try:
                await client.start()
                me = await client.get_me()
                
                # Сохраняем клиент
                client_key = f"{user_id}_{session_id}"
                active_clients[client_key] = client
                
                logger.info(f"✅ Сессия запущена для {user_id}: {session_name} (@{me.username})")
                await safe_send_message(user_id, f"✅ Мониторинг запущен для сессии '{session_name}' (@{me.username})")
                
                # Запускаем прослушивание
                await client.run_until_disconnected()
                
            except Exception as e:
                logger.error(f"❌ Ошибка в клиенте {session_name}: {e}")
                await safe_send_message(user_id, f"❌ Ошибка в сессии '{session_name}': {str(e)}")
        
        # Запускаем клиента в фоне
        asyncio.create_task(run_client())
        return True
        
    except SessionPasswordNeededError:
        error_msg = "❌ Сессия требует двухфакторную аутентификацию"
        await safe_send_message(user_id, error_msg)
        logger.error(f"❌ 2FA required for {session_name}")
        return False
    except PhoneNumberInvalidError:
        error_msg = "❌ Неверный номер телефона в сессии"
        await safe_send_message(user_id, error_msg)
        logger.error(f"❌ Invalid phone for {session_name}")
        return False
    except Exception as e:
        error_msg = f"❌ Ошибка запуска сессии: {str(e)}"
        await safe_send_message(user_id, error_msg)
        logger.error(f"❌ Ошибка запуска {session_name}: {e}")
        return False

async def stop_user_session(user_id: int, session_id: int):
    """Остановка сессии пользователя"""
    try:
        client_key = f"{user_id}_{session_id}"
        
        if client_key in active_clients:
            client = active_clients[client_key]
            await client.disconnect()
            del active_clients[client_key]
            logger.info(f"⏹️ Сессия остановлена: {client_key}")
            return True
        
        return False
    except Exception as e:
        logger.error(f"❌ Ошибка остановки сессии: {e}")
        return False

# Middleware для проверки доступа
@dp.message.middleware()
async def check_access_middleware(handler, event: Message, data):
    """Проверка доступа пользователя"""
    user_id = event.from_user.id
    
    # Автоматически добавляем пользователя
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
                      (user_id, event.from_user.username, event.from_user.first_name))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Ошибка добавления пользователя: {e}")
    
    # Проверяем доступ для команд кроме start
    if event.text and not event.text.startswith('/start'):
        if not is_user_allowed(user_id):
            await safe_send_message(user_id, "❌ Доступ запрещен. Обратитесь к администратору.")
            return
    
    return await handler(event, data)

# Команды бота (остаются без изменений)
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    
    welcome_text = (
        "🔍 Система мониторинга сообщений\n\n"
        "📋 Основные команды:\n"
        "🔐 /add_session - добавить сессию\n"
        "📁 /my_sessions - мои сессии\n"
        "▶️ /start_session - запустить мониторинг\n"
        "⏹️ /stop_session - остановить мониторинг\n"
        "🔍 /add_keyword - добавить ключевые слова\n"
        "🚫 /add_exception - добавить исключения\n"
        "📋 /keywords - список ключевых слов\n"
        "📋 /exceptions - список исключений\n"
        "🗑️ /del_keyword - удалить ключевое слово\n"
        "🗑️ /del_exception - удалить исключение\n"
        "🧹 /clear_keywords - очистить все ключевые слова\n"
        "🧹 /clear_exceptions - очистить все исключения\n"
        "📊 /my_stats - моя статистика\n"
        "🚨 /my_alerts - мои уведомления\n"
        "👥 /add_user - добавить пользователя (админ)\n"
        "👥 /remove_user - удалить пользователя (админ)\n"
        "📋 /users - список пользователей (админ)\n"
        "📡 /status - статус мониторинга"
    )
    
    await safe_send_message(user_id, welcome_text)

@dp.message(Command("add_session"))
async def cmd_add_session(message: Message):
    """Добавление сессии"""
    user_id = message.from_user.id
    
    if not is_user_allowed(user_id):
        return
    
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        help_text = (
            "🔐 Добавление сессии для мониторинга\n\n"
            "Для создания сессии используйте:\n\n"
            "/add_session название_сессии ваша_строка_сессии\n\n"
            "Пример:\n"
            "/add_session моя_сессия 1ApWapzMBu4qU7..."
        )
        await safe_send_message(user_id, help_text)
        return
    
    session_name = args[1]
    session_string = args[2]
    
    # Проверяем валидность сессии
    is_valid, validation_msg = await test_session(session_string)
    
    if not is_valid:
        await safe_send_message(user_id, f"❌ Невалидная сессия: {validation_msg}")
        return
    
    # Сохраняем сессию
    if save_user_session(user_id, session_name, session_string):
        await safe_send_message(user_id, f"✅ Сессия '{session_name}' успешно сохранена!\n\nТеперь вы можете запустить мониторинг: /start_session")
    else:
        await safe_send_message(user_id, "❌ Ошибка сохранения сессии")

@dp.message(Command("add_user"))
async def cmd_add_user(message: Message):
    """Добавление пользователя в белый список (только для админов)"""
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        await safe_send_message(user_id, "❌ Недостаточно прав")
        return
    
    args = message.text.split()
    if len(args) < 2:
        await safe_send_message(user_id, "❌ Используйте: /add_user <user_id>")
        return
    
    try:
        new_user_id = int(args[1])
        username = message.from_user.username or f"user_{new_user_id}"
        
        if add_user_to_whitelist(new_user_id, username, user_id):
            await safe_send_message(user_id, f"✅ Пользователь {new_user_id} добавлен в белый список")
        else:
            await safe_send_message(user_id, "❌ Ошибка добавления пользователя")
    except ValueError:
        await safe_send_message(user_id, "❌ Неверный user_id")

@dp.message(Command("remove_user"))
async def cmd_remove_user(message: Message):
    """Удаление пользователя из белого списка (только для админов)"""
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        await safe_send_message(user_id, "❌ Недостаточно прав")
        return
    
    args = message.text.split()
    if len(args) < 2:
        await safe_send_message(user_id, "❌ Используйте: /remove_user <user_id>")
        return
    
    try:
        remove_user_id = int(args[1])
        
        if remove_user_id in ADMIN_IDS:
            await safe_send_message(user_id, "❌ Нельзя удалить администратора")
            return
            
        if remove_user_from_whitelist(remove_user_id):
            await safe_send_message(user_id, f"✅ Пользователь {remove_user_id} удален из белого списка")
        else:
            await safe_send_message(user_id, "❌ Ошибка удаления пользователя")
    except ValueError:
        await safe_send_message(user_id, "❌ Неверный user_id")

@dp.message(Command("users"))
async def cmd_users(message: Message):
    """Список пользователей с доступом (только для админов)"""
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        await safe_send_message(user_id, "❌ Недостаточно прав")
        return
    
    users = get_allowed_users()
    
    if not users:
        await safe_send_message(user_id, "📝 Нет пользователей с доступом")
        return
    
    text = "👥 Пользователи с доступом:\n\n"
    for user_data in users:
        user_id_db, username, first_name, added_at = user_data
        admin_mark = " 👑" if user_id_db in ADMIN_IDS else ""
        text += f"🆔 {user_id_db} • @{username} • {first_name}{admin_mark}\n"
        text += f"   📅 Добавлен: {added_at}\n\n"
    
    await safe_send_message(user_id, text)

@dp.message(Command("keywords"))
async def cmd_keywords(message: Message):
    user_id = message.from_user.id
    if not is_user_allowed(user_id):
        return
    
    keywords = get_user_keywords(user_id)
    if keywords:
        text = f"🔍 Ваши ключевые слова ({len(keywords)}):\n\n"
        for keyword_id, keyword in keywords:
            text += f"ID {keyword_id} • {keyword}\n"
        text += "\n🗑️ Удалить: /del_keyword <ID>"
        text += "\n🧹 Очистить все: /clear_keywords"
    else:
        text = "📝 У вас пока нет ключевых слов\n\nДобавьте: /add_keyword слово1,слово2"
    
    await safe_send_message(user_id, text)

@dp.message(Command("my_sessions"))
async def cmd_my_sessions(message: Message):
    user_id = message.from_user.id
    if not is_user_allowed(user_id):
        return
    
    sessions = get_user_sessions(user_id)
    if not sessions:
        await safe_send_message(user_id, "📭 У вас нет сохраненных сессий\n\nДобавьте сессию: /add_session")
        return
    
    text = "📁 Ваши сессии:\n\n"
    for session_id, session_name, session_string, is_active in sessions:
        status = "🟢 Активна" if is_active else "🔴 Неактивна"
        text += f"ID {session_id} • {session_name} • {status}\n"
    
    text += "\n▶️ Запустить: /start_session <ID>"
    text += "\n⏹️ Остановить: /stop_session <ID>"
    
    await safe_send_message(user_id, text)

@dp.message(Command("start_session"))
async def cmd_start_session(message: Message):
    user_id = message.from_user.id
    if not is_user_allowed(user_id):
        return
    
    args = message.text.split()
    if len(args) < 2:
        await safe_send_message(user_id, "❌ Используйте: /start_session <ID_сессии>\n\nПосмотреть ID: /my_sessions")
        return
    
    try:
        session_id = int(args[1])
        sessions = get_user_sessions(user_id)
        
        target_session = None
        for sess in sessions:
            if sess[0] == session_id:
                target_session = sess
                break
        
        if not target_session:
            await safe_send_message(user_id, "❌ Сессия с таким ID не найдена")
            return
        
        session_id, session_name, session_string, is_active = target_session
        success = await start_user_session(user_id, session_id, session_name, session_string)
        
        if success:
            await safe_send_message(user_id, f"✅ Сессия '{session_name}' запущена!")
        else:
            await safe_send_message(user_id, f"❌ Не удалось запустить сессию '{session_name}'")
            
    except ValueError:
        await safe_send_message(user_id, "❌ Неверный ID. Используйте числовой ID")

@dp.message(Command("stop_session"))
async def cmd_stop_session(message: Message):
    user_id = message.from_user.id
    if not is_user_allowed(user_id):
        return
    
    args = message.text.split()
    if len(args) < 2:
        await safe_send_message(user_id, "❌ Используйте: /stop_session <ID_сессии>\n\nПосмотреть ID: /my_sessions")
        return
    
    try:
        session_id = int(args[1])
        success = await stop_user_session(user_id, session_id)
        
        if success:
            await safe_send_message(user_id, f"✅ Сессия ID {session_id} остановлена")
        else:
            await safe_send_message(user_id, "❌ Не удалось остановить сессию. Возможно, она не запущена")
            
    except ValueError:
        await safe_send_message(user_id, "❌ Неверный ID. Используйте числовой ID")

@dp.message(Command("add_keyword"))
async def cmd_add_keyword(message: Message):
    """Добавление ключевых слов"""
    user_id = message.from_user.id
    
    if not is_user_allowed(user_id):
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await safe_send_message(user_id, "❌ Используйте: /add_keyword слово1,слово2,слово3")
        return
    
    keywords_text = args[1]
    added_count, keywords = add_user_keywords(user_id, keywords_text)
    
    if added_count > 0:
        await safe_send_message(user_id, f"✅ Добавлено {added_count} ключевых слов: {', '.join(keywords)}")
    else:
        await safe_send_message(user_id, "❌ Не удалось добавить ключевые слова")

@dp.message(Command("add_exception"))
async def cmd_add_exception(message: Message):
    """Добавление исключений"""
    user_id = message.from_user.id
    
    if not is_user_allowed(user_id):
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await safe_send_message(user_id, "❌ Используйте: /add_exception слово1,слово2,слово3")
        return
    
    exceptions_text = args[1]
    added_count, exceptions = add_user_exceptions(user_id, exceptions_text)
    
    if added_count > 0:
        await safe_send_message(user_id, f"✅ Добавлено {added_count} исключений: {', '.join(exceptions)}")
    else:
        await safe_send_message(user_id, "❌ Не удалось добавить исключения")

@dp.message(Command("del_keyword"))
async def cmd_del_keyword(message: Message):
    """Удалить ключевое слово по ID"""
    user_id = message.from_user.id
    
    if not is_user_allowed(user_id):
        return
    
    args = message.text.split()
    if len(args) < 2:
        await safe_send_message(user_id, "❌ Используйте: /del_keyword <ID>\n\nПосмотреть ID: /keywords")
        return
    
    try:
        keyword_id = int(args[1])
        if delete_user_keyword(user_id, keyword_id):
            await safe_send_message(user_id, f"✅ Ключевое слово ID {keyword_id} удалено")
        else:
            await safe_send_message(user_id, "❌ Не удалось удалить ключевое слово. Проверьте ID")
    except ValueError:
        await safe_send_message(user_id, "❌ Неверный ID. Используйте числовой ID")

@dp.message(Command("del_exception"))
async def cmd_del_exception(message: Message):
    """Удалить исключение по ID"""
    user_id = message.from_user.id
    
    if not is_user_allowed(user_id):
        return
    
    args = message.text.split()
    if len(args) < 2:
        await safe_send_message(user_id, "❌ Используйте: /del_exception <ID>\n\nПосмотреть ID: /exceptions")
        return
    
    try:
        exception_id = int(args[1])
        if delete_user_exception(user_id, exception_id):
            await safe_send_message(user_id, f"✅ Исключение ID {exception_id} удалено")
        else:
            await safe_send_message(user_id, "❌ Не удалось удалить исключение. Проверьте ID")
    except ValueError:
        await safe_send_message(user_id, "❌ Неверный ID. Используйте числовой ID")

@dp.message(Command("clear_keywords"))
async def cmd_clear_keywords(message: Message):
    """Очистить все ключевые слова"""
    user_id = message.from_user.id
    
    if not is_user_allowed(user_id):
        return
    
    if clear_all_keywords(user_id):
        await safe_send_message(user_id, "✅ Все ключевые слова очищены")
    else:
        await safe_send_message(user_id, "❌ Ошибка при очистке ключевых слов")

@dp.message(Command("clear_exceptions"))
async def cmd_clear_exceptions(message: Message):
    """Очистить все исключения"""
    user_id = message.from_user.id
    
    if not is_user_allowed(user_id):
        return
    
    if clear_all_exceptions(user_id):
        await safe_send_message(user_id, "✅ Все исключения очищены")
    else:
        await safe_send_message(user_id, "❌ Ошибка при очистке исключений")

@dp.message(Command("my_stats"))
async def cmd_my_stats(message: Message):
    """Статистика пользователя"""
    user_id = message.from_user.id
    
    if not is_user_allowed(user_id):
        return
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Общая статистика
        cursor.execute("SELECT COUNT(*) FROM user_messages WHERE user_id = ?", (user_id,))
        total_messages = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM user_messages WHERE user_id = ? AND has_keywords = 1", (user_id,))
        alert_messages = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM user_keywords WHERE user_id = ?", (user_id,))
        total_keywords = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM user_sessions WHERE user_id = ?", (user_id,))
        total_sessions = cursor.fetchone()[0]
        
        # Активные сессии
        active_sessions = len([key for key in active_clients.keys() if key.startswith(f"{user_id}_")])
        
        conn.close()
        
        text = (
            f"📊 Ваша статистика:\n\n"
            f"💬 Всего сообщений: {total_messages}\n"
            f"🚨 Сообщений с ключами: {alert_messages}\n"
            f"🔍 Ключевых слов: {total_keywords}\n"
            f"📁 Сессий: {total_sessions}\n"
            f"🟢 Активных сессий: {active_sessions}"
        )
        
        await safe_send_message(user_id, text)
        
    except Exception as e:
        logger.error(f"❌ Ошибка получения статистики: {e}")
        await safe_send_message(user_id, "❌ Ошибка получения статистики")

@dp.message(Command("my_alerts"))
async def cmd_my_alerts(message: Message):
    """Последние уведомления пользователя"""
    user_id = message.from_user.id
    
    if not is_user_allowed(user_id):
        return
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT chat_name, username, keywords_found, message_text, timestamp 
            FROM user_messages 
            WHERE user_id = ? AND has_keywords = 1 
            ORDER BY timestamp DESC 
            LIMIT 10
        ''', (user_id,))
        
        alerts = cursor.fetchall()
        conn.close()
        
        if not alerts:
            await safe_send_message(user_id, "📭 У вас пока нет уведомлений")
            return
        
        text = "🚨 Последние уведомления:\n\n"
        for i, (chat_name, username, keywords, message_text, timestamp) in enumerate(alerts, 1):
            clean_message = re.sub(r'\*{2,}', '', message_text)
            text += f"{i}. 📱 {chat_name}\n"
            text += f"   👤 {username}\n"
            text += f"   🔍 {keywords}\n"
            text += f"   💬 {clean_message[:50]}...\n"
            text += f"   🕒 {timestamp}\n\n"
        
        await safe_send_message(user_id, text[:4000])  # Ограничение длины
        
    except Exception as e:
        logger.error(f"❌ Ошибка получения уведомлений: {e}")
        await safe_send_message(user_id, "❌ Ошибка получения уведомлений")

@dp.message(Command("status"))
async def cmd_status(message: Message):
    """Статус мониторинга"""
    user_id = message.from_user.id
    
    if not is_user_allowed(user_id):
        return
    
    active_user_sessions = len([key for key in active_clients.keys() if key.startswith(f"{user_id}_")])
    total_active_sessions = len(active_clients)
    
    text = (
        f"📡 Статус мониторинга:\n\n"
        f"🟢 Ваших активных сессий: {active_user_sessions}\n"
        f"🌐 Всего активных сессий: {total_active_sessions}\n"
        f"👤 Ваш ID: {user_id}"
    )
    
    await safe_send_message(user_id, text)

# Запуск всех сессий при старте бота
async def start_all_sessions():
    """Запуск всех активных сессий при старте бота"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT user_id FROM user_sessions WHERE is_active = 1")
        users = cursor.fetchall()
        
        for (user_id,) in users:
            cursor.execute("SELECT id, session_name, session_string FROM user_sessions WHERE user_id = ? AND is_active = 1", (user_id,))
            sessions = cursor.fetchall()
            
            for session_id, session_name, session_string in sessions:
                # Проверяем сессию перед запуском
                is_valid, _ = await test_session(session_string)
                if is_valid:
                    # Запускаем каждую сессию в отдельной задаче с задержкой
                    asyncio.create_task(
                        start_user_session(user_id, session_id, session_name, session_string)
                    )
                    await asyncio.sleep(3)  # Задержка между запусками сессий
                else:
                    logger.error(f"❌ Невалидная сессия {session_name} для {user_id}")
        
        conn.close()
        logger.info("✅ Все валидные сессии запущены")
        
    except Exception as e:
        logger.error(f"❌ Ошибка запуска сессий при старте: {e}")

# HTTP сервер для проверки здоровья
async def health_check(request):
    return web.Response(text=f"Monitoring Bot is running! Active sessions: {len(active_clients)}")

async def start_http_server():
    """Запуск HTTP сервера для Railway"""
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"🌐 HTTP сервер запущен на порту {PORT}")

async def main():
    """Основная функция запуска"""
    logger.info("🚀 Запуск системы мониторинга...")
    
    # Инициализация БД
    init_db()
    
    # Запуск HTTP сервера
    await start_http_server()
    
    # Запуск бота
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Запуск всех сессий пользователей с задержкой
    await asyncio.sleep(3)  # Даем боту время запуститься
    asyncio.create_task(start_all_sessions())
    
    logger.info("✅ Бот запущен!")
    
    # Запускаем поллинг
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
