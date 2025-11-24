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

# Словарь для хранения активных клиентов Telethon и их задач
active_clients = {}
client_tasks = {}

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
                      (user_id, username, username))
        cursor.execute("INSERT OR IGNORE INTO allowed_users (user_id, username, added_by) VALUES (?, ?, ?)", 
                      (user_id, username, added_by))
        conn.commit()
        conn.close()
        logger.info(f"✅ Пользователь {user_id} добавлен в белый список")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка добавления в белый список {user_id}: {e}")
        return False

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
        logger.error(f"❌ Ошибка получения ключевых слов для {user_id}: {e}")
        return set()

def get_user_exceptions(user_id: int):
    """Получение слов-исключений пользователя"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT exception_word FROM user_exceptions WHERE user_id = ? AND is_active = 1", (user_id,))
        exceptions = {row[0].lower() for row in cursor.fetchall()}
        conn.close()
        return exceptions
    except Exception as e:
        logger.error(f"❌ Ошибка получения исключений для {user_id}: {e}")
        return set()

def save_user_message(user_id: int, message_data: dict):
    """Сохранение сообщения пользователя"""
    try:
        # Очищаем текст от *** и других нежелательных символов
        clean_text = re.sub(r'\*{2,}', '', message_data['message_text'])  # Удаляем ***
        clean_text = re.sub(r'[^\w\sа-яА-ЯёЁ@#.,!?]', '', clean_text)  # Удаляем эмодзи и спецсимволы
        
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
            clean_text,  # Используем очищенный текст
            message_data['has_keywords'],
            message_data['keywords_found'],
            message_data['message_type']
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения сообщения для {user_id}: {e}")

async def check_keywords_for_user(user_id: int, text: str):
    """Проверка ключевых слов и исключений для конкретного пользователя"""
    if not text:
        return False, []
    
    # Очищаем текст от *** перед проверкой
    clean_text = re.sub(r'\*{2,}', '', text)
    
    keywords = get_user_keywords(user_id)
    exceptions = get_user_exceptions(user_id)
    text_lower = clean_text.lower()
    
    # Проверяем есть ли слова-исключения
    has_exceptions = any(exc in text_lower for exc in exceptions)
    if has_exceptions:
        return False, []  # Игнорируем сообщение если есть исключения
    
    # Проверяем ключевые слова
    found_keywords = [kw for kw in keywords if kw in text_lower]
    
    return len(found_keywords) > 0, found_keywords

async def start_user_session(user_id: int, session_id: int, session_name: str, session_string: str):
    """Запуск мониторинга для сессии пользователя"""
    try:
        # Создаем клиента Telethon
        client = TelegramClient(
            StringSession(session_string),
            api_id=2040,
            api_hash='b18441a1ff607e10a989891a5462e627'
        )
        
        @client.on(events.NewMessage)
        async def handle_user_messages(event):
            """Обработчик сообщений для конкретного пользователя"""
            try:
                if not event.message.text or event.message.text.strip() == '':
                    return
                
                # Получаем информацию о чате
                chat = await event.get_chat()
                chat_id = str(chat.id)
                chat_name = getattr(chat, 'title', f"{getattr(chat, 'first_name', 'Unknown')} {getattr(chat, 'last_name', '')}").strip()
                
                # Получаем информацию об отправителе
                sender = await event.get_sender()
                user_id_str = str(getattr(sender, 'id', 'Unknown'))
                username = getattr(sender, 'username', 'Unknown')
                
                message_text = event.message.text
                
                # Проверяем ключевые слова пользователя (учитывая исключения)
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
                    'message_type': 'channel' if hasattr(chat, 'broadcast') and chat.broadcast else 'group'
                }
                
                save_user_message(user_id, message_data)
                
                # Отправляем уведомление если есть ключевые слова и нет исключений
                if has_keywords and found_keywords:
                    # Очищаем текст для уведомления
                    clean_message = re.sub(r'\*{2,}', '', message_text)
                    
                    alert_text = (
                        f"🚨 Найдено ключевое слово!\n\n"
                        f"📱 Чат: {chat_name}\n"
                        f"👤 Отправитель: {username}\n"
                        f"🔍 Ключевые слова: {', '.join(found_keywords)}\n"
                        f"💬 Сообщение: {clean_message[:150]}...\n"
                        f"🔐 Сессия: {session_name}"
                    )
                    
                    try:
                        await bot.send_message(user_id, alert_text)
                        logger.info(f"🔔 Уведомление отправлено пользователю {user_id}: {found_keywords}")
                    except Exception as e:
                        logger.error(f"❌ Ошибка отправки уведомления пользователю {user_id}: {e}")
                        
            except Exception as e:
                logger.error(f"❌ Ошибка обработки сообщения для пользователя {user_id}: {e}")
        
        # Запускаем клиента в отдельной задаче
        async def run_client():
            try:
                await client.start()
                me = await client.get_me()
                logger.info(f"✅ Сессия запущена для {user_id}: {session_name} (@{me.username})")
                await client.run_until_disconnected()
            except Exception as e:
                logger.error(f"❌ Ошибка в сессии {session_name}: {e}")
            finally:
                # Удаляем из активных при завершении
                client_key = f"{user_id}_{session_id}"
                if client_key in active_clients:
                    del active_clients[client_key]
                if client_key in client_tasks:
                    del client_tasks[client_key]
        
        # Сохраняем клиент и запускаем задачу
        client_key = f"{user_id}_{session_id}"
        active_clients[client_key] = client
        client_tasks[client_key] = asyncio.create_task(run_client())
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка запуска сессии для {user_id}: {e}")
        return False

async def stop_user_session(user_id: int, session_id: int):
    """Остановка сессии пользователя"""
    try:
        client_key = f"{user_id}_{session_id}"
        
        if client_key in active_clients:
            client = active_clients[client_key]
            await client.disconnect()
            
            # Отменяем задачу если она существует
            if client_key in client_tasks:
                client_tasks[client_key].cancel()
                try:
                    await client_tasks[client_key]
                except asyncio.CancelledError:
                    pass
                del client_tasks[client_key]
            
            del active_clients[client_key]
            logger.info(f"⏹️ Сессия остановлена: {client_key}")
            return True
        
        return False
    except Exception as e:
        logger.error(f"❌ Ошибка остановки сессии {user_id}_{session_id}: {e}")
        return False

# Middleware для проверки доступа
@dp.message.middleware()
async def check_access_middleware(handler, event: Message, data):
    """Проверка доступа пользователя"""
    user_id = event.from_user.id
    
    # Автоматически добавляем пользователя при первом обращении
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
                      (user_id, event.from_user.username, event.from_user.first_name))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Ошибка добавления пользователя {user_id}: {e}")
    
    # Проверяем доступ для всех команд кроме start
    if event.text and not event.text.startswith('/start'):
        if not is_user_allowed(user_id):
            await event.answer("❌ Доступ запрещен. Обратитесь к администратору.")
            return
    
    return await handler(event, data)

# Команды бота
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    
    welcome_text = (
        "🔍 Система мониторинга сообщений\n\n"
        "📋 Основные команды:\n"
        "➕ /add_session - добавить сессию\n"
        "📁 /my_sessions - мои сессии\n"
        "▶️ /start_session - запустить мониторинг\n"
        "⏹️ /stop_session - остановить мониторинг\n"
        "🔍 /add_keyword - добавить ключевое слово\n"
        "🚫 /add_exception - добавить исключение\n"
        "📊 /my_stats - моя статистика\n"
        "🚨 /my_alerts - мои уведомления\n"
        "👥 /add_user - добавить пользователя (админ)\n"
        "📋 /users - список пользователей (админ)"
    )
    
    await message.answer(welcome_text)

@dp.message(Command("add_user"))
async def cmd_add_user(message: Message):
    user_id = message.from_user.id
    
    # Проверяем права администратора
    if user_id not in ADMIN_IDS:
        await message.answer("❌ Недостаточно прав")
        return
    
    args = message.text.split()
    if len(args) < 2:
        await message.answer("📝 Используйте: /add_user user_id")
        return
    
    try:
        target_user_id = int(args[1])
        if add_user_to_whitelist(target_user_id, f"user_{target_user_id}", user_id):
            await message.answer(f"✅ Пользователь {target_user_id} добавлен в белый список")
        else:
            await message.answer("❌ Ошибка добавления пользователя")
    except ValueError:
        await message.answer("❌ Неверный user_id")

@dp.message(Command("users"))
async def cmd_users(message: Message):
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        await message.answer("❌ Недостаточно прав")
        return
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username FROM allowed_users")
        users = cursor.fetchall()
        conn.close()
        
        if users:
            text = "👥 Пользователи с доступом:\n\n"
            for user_id, username in users:
                status = "🟢" if any(str(user_id) in key for key in active_clients.keys()) else "⚪"
                text += f"{status} {user_id} - {username}\n"
            await message.answer(text)
        else:
            await message.answer("📝 Пользователи не найдены")
    except Exception as e:
        logger.error(f"❌ Ошибка получения пользователей: {e}")
        await message.answer("❌ Ошибка получения списка")

@dp.message(Command("add_session"))
async def cmd_add_session(message: Message):
    user_id = message.from_user.id
    
    if not is_user_allowed(user_id):
        return
    
    help_text = (
        "🔐 Добавление сессии для мониторинга\n\n"
        "Для создания сессии используйте нашего бота:\n"
        "@testses_ses_bot\n\n"
        "После получения строки сессии отправьте команду:\n\n"
        "/session_data название_сессии ваша_строка_сессии\n\n"
        "Пример:\n"
        "/session_data моя_сессия 1ApWapzMBu4qU7..."
    )
    
    await message.answer(help_text)

@dp.message(Command("session_data"))
async def cmd_session_data(message: Message):
    user_id = message.from_user.id
    
    if not is_user_allowed(user_id):
        return
    
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("❌ Используйте: /session_data название_сессии строка_сессии")
        return
    
    session_name = args[1]
    session_string = args[2]
    
    if len(session_string) < 50:
        await message.answer("❌ Неверная строка сессии")
        return
    
    if save_user_session(user_id, session_name, session_string):
        response_text = (
            f"✅ Сессия добавлена!\n\n"
            f"📝 Название: {session_name}\n"
            f"💾 Статус: Сохранена\n\n"
            f"Запустите мониторинг:\n"
            f"▶️ /start_session {session_name}"
        )
        await message.answer(response_text)
    else:
        await message.answer("❌ Ошибка при сохранении сессии")

@dp.message(Command("my_sessions"))
async def cmd_my_sessions(message: Message):
    user_id = message.from_user.id
    
    if not is_user_allowed(user_id):
        return
    
    sessions = get_user_sessions(user_id)
    
    if not sessions:
        await message.answer("📝 У вас пока нет сессий\n\nДобавьте сессию: ➕ /add_session")
        return
    
    text = "🔐 Ваши сессии:\n\n"
    for session_id, session_name, session_string, is_active in sessions:
        client_key = f"{user_id}_{session_id}"
        is_running = client_key in active_clients
        
        status = "🟢 Запущена" if is_running else "⚪ Остановлена"
        session_preview = session_string[:20] + "..." if len(session_string) > 20 else session_string
        
        text += f"📝 {session_name}\n"
        text += f"🆔 ID: {session_id}\n"
        text += f"📡 Статус: {status}\n"
        
        if is_running:
            text += f"⏹️ Остановить: /stop_session {session_id}\n"
        else:
            text += f"▶️ Запустить: /start_session {session_id}\n"
        
        text += "──────\n"
    
    await message.answer(text)

@dp.message(Command("start_session"))
async def cmd_start_session(message: Message):
    user_id = message.from_user.id
    
    if not is_user_allowed(user_id):
        return
    
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Используйте: /start_session id_сессии")
        return
    
    session_identifier = args[1]
    sessions = get_user_sessions(user_id)
    
    target_session = None
    for session_id, session_name, session_string, is_active in sessions:
        if str(session_id) == session_identifier or session_name == session_identifier:
            target_session = (session_id, session_name, session_string)
            break
    
    if not target_session:
        await message.answer("❌ Сессия не найдена")
        return
    
    session_id, session_name, session_string = target_session
    
    success = await start_user_session(user_id, session_id, session_name, session_string)
    
    if success:
        response_text = (
            f"✅ Мониторинг запущен!\n\n"
            f"📝 Сессия: {session_name}\n"
            f"🆔 ID: {session_id}\n"
            f"📡 Статус: Активен\n\n"
            f"🔍 Бот отслеживает все сообщения\n"
            f"🚨 Уведомляет о ключевых словах\n"
            f"🚫 Игнорирует сообщения с исключениями"
        )
        await message.answer(response_text)
    else:
        await message.answer("❌ Ошибка запуска сессии")

@dp.message(Command("stop_session"))
async def cmd_stop_session(message: Message):
    user_id = message.from_user.id
    
    if not is_user_allowed(user_id):
        return
    
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Используйте: /stop_session id_сессии")
        return
    
    session_id = args[1]
    
    try:
        success = await stop_user_session(user_id, int(session_id))
        if success:
            await message.answer(f"⏹️ Сессия {session_id} остановлена")
        else:
            await message.answer("❌ Сессия не найдена или уже остановлена")
    except ValueError:
        await message.answer("❌ Неверный ID сессии")

@dp.message(Command("add_keyword"))
async def cmd_add_keyword(message: Message):
    user_id = message.from_user.id
    
    if not is_user_allowed(user_id):
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Используйте: /add_keyword слово")
        return
    
    keyword = args[1].strip()
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO user_keywords (user_id, keyword) VALUES (?, ?)",
            (user_id, keyword)
        )
        conn.commit()
        conn.close()
        
        await message.answer(f"✅ Ключевое слово добавлено: {keyword}")
        logger.info(f"🔍 Пользователь {user_id} добавил ключевое слово: {keyword}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка добавления ключевого слова для {user_id}: {e}")
        await message.answer("❌ Ошибка при добавлении")

@dp.message(Command("add_exception"))
async def cmd_add_exception(message: Message):
    user_id = message.from_user.id
    
    if not is_user_allowed(user_id):
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Используйте: /add_exception слово")
        return
    
    exception_word = args[1].strip()
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO user_exceptions (user_id, exception_word) VALUES (?, ?)",
            (user_id, exception_word)
        )
        conn.commit()
        conn.close()
        
        await message.answer(f"✅ Исключение добавлено: {exception_word}")
        logger.info(f"🚫 Пользователь {user_id} добавил исключение: {exception_word}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка добавления исключения для {user_id}: {e}")
        await message.answer("❌ Ошибка при добавлении")

@dp.message(Command("my_keywords"))
async def cmd_my_keywords(message: Message):
    user_id = message.from_user.id
    
    if not is_user_allowed(user_id):
        return
    
    keywords = list(get_user_keywords(user_id))
    exceptions = list(get_user_exceptions(user_id))
    
    text = ""
    if keywords:
        text += "🔍 Ваши ключевые слова:\n" + "\n".join(f"• {kw}" for kw in sorted(keywords)) + "\n\n"
    
    if exceptions:
        text += "🚫 Ваши исключения:\n" + "\n".join(f"• {exc}" for exc in sorted(exceptions))
    
    if text:
        await message.answer(text)
    else:
        await message.answer("📝 У вас пока нет ключевых слов или исключений\n\nДобавьте: 🔍 /add_keyword или 🚫 /add_exception")

@dp.message(Command("my_stats"))
async def cmd_my_stats(message: Message):
    user_id = message.from_user.id
    
    if not is_user_allowed(user_id):
        return
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM user_keywords WHERE user_id = ?", (user_id,))
        kw_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM user_exceptions WHERE user_id = ?", (user_id,))
        exc_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM user_sessions WHERE user_id = ?", (user_id,))
        sessions_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM user_messages WHERE user_id = ?", (user_id,))
        messages_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM user_messages WHERE user_id = ? AND has_keywords = 1", (user_id,))
        alerts_count = cursor.fetchone()[0]
        
        cursor.execute(
            "SELECT keywords_found, COUNT(*) FROM user_messages WHERE user_id = ? AND has_keywords = 1 GROUP BY keywords_found ORDER BY COUNT(*) DESC LIMIT 5",
            (user_id,)
        )
        top_keywords = cursor.fetchall()
        
        conn.close()
        
        stats_text = (
            f"📊 Ваша статистика\n\n"
            f"🔍 Ключевых слов: {kw_count}\n"
            f"🚫 Исключений: {exc_count}\n"
            f"🔐 Сессий: {sessions_count}\n"
            f"💬 Всего сообщений: {messages_count}\n"
            f"🚨 Найдено совпадений: {alerts_count}\n"
        )
        
        if top_keywords:
            stats_text += "\n🏆 Топ ключевых слов:\n"
            for keyword, count in top_keywords:
                stats_text += f"• {keyword}: {count}\n"
        
        await message.answer(stats_text)
        
    except Exception as e:
        logger.error(f"❌ Ошибка получения статистики для {user_id}: {e}")
        await message.answer("❌ Ошибка получения статистики")

@dp.message(Command("my_alerts"))
async def cmd_my_alerts(message: Message):
    user_id = message.from_user.id
    
    if not is_user_allowed(user_id):
        return
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT chat_name, username, message_text, keywords_found, timestamp 
            FROM user_messages 
            WHERE user_id = ? AND has_keywords = 1
            ORDER BY timestamp DESC 
            LIMIT 10
        """, (user_id,))
        alerts = cursor.fetchall()
        conn.close()
        
        if alerts:
            text = "🚨 Последние уведомления:\n\n"
            for chat, user, msg, keywords, time in alerts:
                time_str = datetime.strptime(time, '%Y-%m-%d %H:%M:%S').strftime('%H:%M')
                # Очищаем текст от ***
                clean_msg = re.sub(r'\*{2,}', '', msg)
                text += f"📱 Чат: {chat}\n"
                text += f"👤 Юзер: {user or 'N/A'}\n"
                text += f"🔍 Ключи: {keywords}\n"
                text += f"💬 Текст: {clean_msg[:60]}...\n"
                text += f"⏰ Время: {time_str}\n"
                text += "──────\n"
            await message.answer(text)
        else:
            await message.answer("📝 У вас пока нет уведомлений")
            
    except Exception as e:
        logger.error(f"❌ Ошибка получения уведомлений для {user_id}: {e}")
        await message.answer("❌ Ошибка получения уведомлений")

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
                await start_user_session(user_id, session_id, session_name, session_string)
                await asyncio.sleep(2)  # Увеличиваем задержку между запусками
        
        conn.close()
        logger.info("✅ Все сессии пользователей запущены")
        
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
    
    # Запуск всех сессий пользователей
    asyncio.create_task(start_all_sessions())
    
    logger.info("✅ Бот запущен!")
    
    # Запускаем поллинг
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
