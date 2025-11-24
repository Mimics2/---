import os
import asyncio
import logging
import sqlite3
import base64
import json
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

# Словарь для хранения активных клиентов Telethon
active_clients = {}

def init_db():
    """Инициализация базы данных для многопользовательской работы"""
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
        
        # Сессии пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                session_name TEXT,
                session_string TEXT,
                api_id INTEGER DEFAULT 2040,
                api_hash TEXT DEFAULT 'b18441a1ff607e10a989891a5462e627',
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
        
        # Добавляем админов
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

def add_user(user_id: int, username: str, first_name: str):
    """Добавление/обновление пользователя"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO users (user_id, username, first_name, is_active) VALUES (?, ?, ?, 1)",
            (user_id, username, first_name)
        )
        conn.commit()
        conn.close()
        logger.info(f"✅ Пользователь добавлен: {user_id} - {first_name}")
        return True
    except Exception as e:
        logger.error(f"Ошибка добавления пользователя {user_id}: {e}")
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
        logger.error(f"Ошибка получения сессий для {user_id}: {e}")
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
        logger.info(f"✅ Сессия сохранена для {user_id}: {session_name}")
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения сессии для {user_id}: {e}")
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
        logger.error(f"Ошибка получения ключевых слов для {user_id}: {e}")
        return set()

def save_user_message(user_id: int, message_data: dict):
    """Сохранение сообщения пользователя"""
    try:
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
            message_data['message_text'],
            message_data['has_keywords'],
            message_data['keywords_found'],
            message_data['message_type']
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка сохранения сообщения для {user_id}: {e}")

async def check_keywords_for_user(user_id: int, text: str):
    """Проверка ключевых слов для конкретного пользователя"""
    if not text:
        return False, []
    
    keywords = get_user_keywords(user_id)
    text_lower = text.lower()
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
                
                # Проверяем ключевые слова пользователя
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
                
                # Отправляем уведомление если есть ключевые слова
                if has_keywords and found_keywords:
                    alert_text = (
                        f"🚨 <b>Ваше ключевое слово найдено!</b>\n\n"
                        f"📁 <b>Чат:</b> {chat_name}\n"
                        f"👤 <b>Отправитель:</b> {username}\n"
                        f"🔍 <b>Ключевые слова:</b> {', '.join(found_keywords)}\n"
                        f"💬 <b>Сообщение:</b> {message_text[:150]}...\n"
                        f"🔐 <b>Сессия:</b> {session_name}"
                    )
                    
                    try:
                        await bot.send_message(user_id, alert_text)
                        logger.info(f"🔍 Уведомление отправлено пользователю {user_id}: {found_keywords}")
                    except Exception as e:
                        logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")
                        
            except Exception as e:
                logger.error(f"❌ Ошибка обработки сообщения для пользователя {user_id}: {e}")
        
        # Запускаем клиента
        await client.start()
        
        # Сохраняем клиент в активных
        client_key = f"{user_id}_{session_id}"
        active_clients[client_key] = {
            'client': client,
            'session_name': session_name,
            'start_time': datetime.now()
        }
        
        # Получаем информацию об аккаунте
        me = await client.get_me()
        logger.info(f"✅ Сессия запущена для {user_id}: {session_name} (@{me.username})")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка запуска сессии для {user_id}: {e}")
        return False

async def stop_user_session(user_id: int, session_id: int):
    """Остановка сессии пользователя"""
    try:
        client_key = f"{user_id}_{session_id}"
        if client_key in active_clients:
            await active_clients[client_key]['client'].disconnect()
            del active_clients[client_key]
            logger.info(f"✅ Сессия остановлена: {client_key}")
            return True
        return False
    except Exception as e:
        logger.error(f"Ошибка остановки сессии {user_id}_{session_id}: {e}")
        return False

async def restart_user_sessions(user_id: int):
    """Перезапуск всех сессий пользователя"""
    try:
        # Останавливаем текущие сессии
        for client_key in list(active_clients.keys()):
            if client_key.startswith(f"{user_id}_"):
                await stop_user_session(user_id, int(client_key.split('_')[1]))
        
        # Запускаем сессии заново
        sessions = get_user_sessions(user_id)
        for session_id, session_name, session_string, is_active in sessions:
            if is_active:
                await start_user_session(user_id, session_id, session_name, session_string)
        
        logger.info(f"✅ Сессии перезапущены для пользователя {user_id}")
        
    except Exception as e:
        logger.error(f"Ошибка перезапуска сессий для {user_id}: {e}")

# Команды бота
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    add_user(user_id, message.from_user.username, message.from_user.first_name)
    
    await message.answer(
        "🔐 <b>Система многопользовательского мониторинга</b>\n\n"
        "⚡ <b>Каждый пользователь работает со своими сессиями и ключевыми словами!</b>\n\n"
        "<b>Основные команды:</b>\n"
        "/add_session - добавить сессию\n"
        "/my_sessions - мои сессии\n"
        "/start_session - запустить мониторинг\n"
        "/stop_session - остановить мониторинг\n"
        "/add_keyword - добавить ключевое слово\n"
        "/my_keywords - мои ключевые слова\n"
        "/my_stats - моя статистика\n"
        "/my_alerts - мои уведомления\n\n"
        "💡 <b>Сессия - это ваш аккаунт Telegram для мониторинга</b>"
    )

@dp.message(Command("add_session"))
async def cmd_add_session(message: Message):
    user_id = message.from_user.id
    add_user(user_id, message.from_user.username, message.from_user.first_name)
    
    await message.answer(
        "🔐 <b>Добавление сессии для мониторинга</b>\n\n"
        "Чтобы получить строку сессии:\n\n"
        "1. Перейдите в @genStr_robot\n"
        "2. Нажмите 'Start'\n" 
        "3. Выберите 'API Development Tools'\n"
        "4. Скопируйте строку сессии\n"
        "5. Отправьте её мне командой:\n\n"
        "<code>/session_data название_сессии ваша_строка_сессии</code>\n\n"
        "Пример:\n"
        "<code>/session_data моя_сессия 1ApWapzMBu4qU7q2keXJscuHeG7_kuxFytFGxKN-tc_gh4GiQGLgL8XI6gYspYtTTDGTiA9pqY53Dltv60sK5z6NoZW0Sn15eB7cihrVdnQPW22S7ZJ9kCkSrcb3my4OH2dxHt2SPH6gFDyh9lV9OCScdZLLBYqYiA-dw4fmF-ihSxwu5pKMRFf37dlzIqbuZPCclWLZ1-2LyHHmQqIlA2QAU19aw2cdooq_iIBpLTHdc-hd48j1xjXTHps4dOZT4qexqUhd4KsiEJafI9ppSAHHd6rhNZHwLI_PFxTNGn4ZES1pO0aoOwOXXFhz9fT8vd76dojGrMcFc0Q9b7w7qh3zG-CZDM7E=</code>"
    )

@dp.message(Command("session_data"))
async def cmd_session_data(message: Message):
    user_id = message.from_user.id
    add_user(user_id, message.from_user.username, message.from_user.first_name)
    
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("❌ <b>Неверный формат</b>\n\nИспользуйте: /session_data название_сессии строка_сессии")
        return
    
    session_name = args[1]
    session_string = args[2]
    
    # Проверяем валидность строки сессии
    if len(session_string) < 50:
        await message.answer("❌ <b>Неверная строка сессии</b>\n\nСтрока сессии должна быть длиннее 50 символов")
        return
    
    # Сохраняем сессию
    if save_user_session(user_id, session_name, session_string):
        await message.answer(
            f"✅ <b>Сессия добавлена!</b>\n\n"
            f"📝 <b>Название:</b> {session_name}\n"
            f"🔐 <b>Статус:</b> Сохранена\n\n"
            f"Запустите мониторинг командой:\n"
            f"<code>/start_session {session_name}</code>"
        )
    else:
        await message.answer("❌ Ошибка при сохранении сессии")

@dp.message(Command("my_sessions"))
async def cmd_my_sessions(message: Message):
    user_id = message.from_user.id
    sessions = get_user_sessions(user_id)
    
    if not sessions:
        await message.answer(
            "📝 <b>У вас пока нет сессий</b>\n\n"
            "Добавьте сессию командой:\n"
            "<code>/add_session</code>"
        )
        return
    
    text = "🔐 <b>Ваши сессии:</b>\n\n"
    for session_id, session_name, session_string, is_active in sessions:
        status = "🟢 Активна" if is_active else "🔴 Неактивна"
        client_key = f"{user_id}_{session_id}"
        is_running = client_key in active_clients
        
        running_status = "✅ Запущена" if is_running else "⏸️ Остановлена"
        session_preview = session_string[:30] + "..." if len(session_string) > 30 else session_string
        
        text += f"📝 <b>{session_name}</b>\n"
        text += f"🆔 ID: {session_id}\n"
        text += f"📡 Статус: {running_status}\n"
        text += f"🔑 Ключ: <code>{session_preview}</code>\n"
        
        if is_running:
            text += f"⏹️ Остановить: <code>/stop_session {session_id}</code>\n"
        else:
            text += f"▶️ Запустить: <code>/start_session {session_id}</code>\n"
        
        text += "━━━━━━━━━━━━━━━━━━\n"
    
    await message.answer(text)

@dp.message(Command("start_session"))
async def cmd_start_session(message: Message):
    user_id = message.from_user.id
    
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Используйте: /start_session <id_сессии_или_название>")
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
    
    # Запускаем сессию
    success = await start_user_session(user_id, session_id, session_name, session_string)
    
    if success:
        await message.answer(
            f"✅ <b>Мониторинг запущен!</b>\n\n"
            f"📝 <b>Сессия:</b> {session_name}\n"
            f"🆔 <b>ID:</b> {session_id}\n"
            f"⚡ <b>Статус:</b> Мониторинг всех сообщений\n\n"
            f"Теперь бот будет отслеживать все сообщения в этой сессии и уведомлять вас о ключевых словах!"
        )
    else:
        await message.answer("❌ Ошибка запуска сессии. Проверьте строку сессии.")

@dp.message(Command("stop_session"))
async def cmd_stop_session(message: Message):
    user_id = message.from_user.id
    
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Используйте: /stop_session <id_сессии>")
        return
    
    session_id = args[1]
    
    success = await stop_user_session(user_id, int(session_id))
    
    if success:
        await message.answer(f"✅ Сессия {session_id} остановлена")
    else:
        await message.answer("❌ Сессия не найдена или уже остановлена")

@dp.message(Command("add_keyword"))
async def cmd_add_keyword(message: Message):
    user_id = message.from_user.id
    add_user(user_id, message.from_user.username, message.from_user.first_name)
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Используйте: /add_keyword <слово или фраза>")
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
        
        await message.answer(f"✅ <b>Ключевое слово добавлено:</b> <code>{keyword}</code>")
        logger.info(f"Пользователь {user_id} добавил ключевое слово: {keyword}")
        
    except Exception as e:
        logger.error(f"Ошибка добавления ключевого слова для {user_id}: {e}")
        await message.answer("❌ Ошибка при добавлении")

@dp.message(Command("my_keywords"))
async def cmd_my_keywords(message: Message):
    user_id = message.from_user.id
    keywords = list(get_user_keywords(user_id))
    
    if keywords:
        text = "🔍 <b>Ваши ключевые слова:</b>\n\n" + "\n".join(f"• {kw}" for kw in sorted(keywords))
        await message.answer(text)
    else:
        await message.answer(
            "📝 <b>У вас пока нет ключевых слов</b>\n\n"
            "Добавьте их командой:\n"
            "<code>/add_keyword ваше_слово</code>"
        )

@dp.message(Command("my_stats"))
async def cmd_my_stats(message: Message):
    user_id = message.from_user.id
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM user_keywords WHERE user_id = ?", (user_id,))
        kw_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM user_sessions WHERE user_id = ?", (user_id,))
        sessions_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM user_messages WHERE user_id = ?", (user_id,))
        messages_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM user_messages WHERE user_id = ? AND has_keywords = 1", (user_id,))
        alerts_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(DISTINCT session_id) FROM user_messages WHERE user_id = ?", (user_id,))
        active_sessions = cursor.fetchone()[0]
        
        cursor.execute(
            "SELECT keywords_found, COUNT(*) FROM user_messages WHERE user_id = ? AND has_keywords = 1 GROUP BY keywords_found ORDER BY COUNT(*) DESC LIMIT 5",
            (user_id,)
        )
        top_keywords = cursor.fetchall()
        
        conn.close()
        
        stats_text = (
            f"📊 <b>Ваша статистика мониторинга</b>\n\n"
            f"🔍 <b>Ключевых слов:</b> {kw_count}\n"
            f"🔐 <b>Сессий:</b> {sessions_count}\n"
            f"💬 <b>Всего сообщений:</b> {messages_count}\n"
            f"🚨 <b>Найдено совпадений:</b> {alerts_count}\n"
            f"📡 <b>Активных сессий:</b> {active_sessions}\n\n"
        )
        
        if top_keywords:
            stats_text += "🏆 <b>Топ ключевых слов:</b>\n"
            for keyword, count in top_keywords:
                stats_text += f"• {keyword}: {count}\n"
        
        await message.answer(stats_text)
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики для {user_id}: {e}")
        await message.answer("❌ Ошибка получения статистики")

@dp.message(Command("my_alerts"))
async def cmd_my_alerts(message: Message):
    user_id = message.from_user.id
    
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
            text = "🚨 <b>Ваши последние уведомления:</b>\n\n"
            for chat, user, msg, keywords, time in alerts:
                time_str = datetime.strptime(time, '%Y-%m-%d %H:%M:%S').strftime('%H:%M')
                text += f"📁 <b>Чат:</b> {chat}\n"
                text += f"👤 <b>Юзер:</b> {user or 'N/A'}\n"
                text += f"🔍 <b>Ключи:</b> {keywords}\n"
                text += f"💬 <b>Текст:</b> {msg[:60]}...\n"
                text += f"⏰ <b>Время:</b> {time_str}\n"
                text += "━━━━━━━━━━━━━━━━━━\n"
            await message.answer(text)
        else:
            await message.answer("📝 У вас пока нет уведомлений")
            
    except Exception as e:
        logger.error(f"Ошибка получения уведомлений для {user_id}: {e}")
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
                await asyncio.sleep(1)  # Задержка между запусками
        
        conn.close()
        logger.info("✅ Все сессии пользователей запущены")
        
    except Exception as e:
        logger.error(f"Ошибка запуска сессий при старте: {e}")

# HTTP сервер для проверки здоровья
async def health_check(request):
    return web.Response(text=f"Multi-user Monitoring Bot is running! Active sessions: {len(active_clients)}")

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
    logger.info("🚀 Запуск многопользовательской системы мониторинга...")
    
    # Инициализация БД
    init_db()
    
    # Запуск HTTP сервера
    await start_http_server()
    
    # Запуск бота
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Запуск всех сессий пользователей
    await start_all_sessions()
    
    logger.info("✅ Многопользовательский бот запущен!")
    
    # Запускаем поллинг
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
