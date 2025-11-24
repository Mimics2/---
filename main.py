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
SESSION_STRING = os.getenv('SESSION_STRING')
PORT = int(os.getenv('PORT', 8080))

# Проверка обязательных переменных
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен")
if not SESSION_STRING:
    raise ValueError("SESSION_STRING не установлен")

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

# Инициализация Telethon клиента
telethon_client = TelegramClient(
    StringSession(SESSION_STRING),
    api_id=2040,  # Стандартный API ID для Telethon
    api_hash='b18441a1ff607e10a989891a5462e627'  # Стандартный API Hash
)

def init_db():
    """Инициализация базы данных"""
    try:
        db_path = '/data/monitoring.db' if os.path.exists('/data') else 'monitoring.db'
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Ключевые слова для фильтрации
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS keywords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT UNIQUE,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Все найденные сообщения
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS all_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER,
                chat_id TEXT,
                chat_name TEXT,
                user_id TEXT,
                username TEXT,
                message_text TEXT,
                has_keywords BOOLEAN DEFAULT 0,
                keywords_found TEXT,
                message_type TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Статистика чатов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chat_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT UNIQUE,
                chat_name TEXT,
                message_count INTEGER DEFAULT 0,
                last_activity TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Добавляем начальные ключевые слова
        initial_keywords = [
            'тест', 'мониторинг', 'ключевое слово', 'проверка',
            'важно', 'срочно', 'внимание', 'алерт', 'тревога'
        ]
        for keyword in initial_keywords:
            cursor.execute("INSERT OR IGNORE INTO keywords (keyword) VALUES (?)", (keyword,))
        
        conn.commit()
        conn.close()
        logger.info("✅ База данных инициализирована")
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")

def get_db_connection():
    """Получение соединения с БД"""
    db_path = '/data/monitoring.db' if os.path.exists('/data') else 'monitoring.db'
    return sqlite3.connect(db_path, check_same_thread=False)

def get_keywords():
    """Получение всех ключевых слов"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT keyword FROM keywords WHERE is_active = 1")
        keywords = {row[0].lower() for row in cursor.fetchall()}
        conn.close()
        return keywords
    except Exception as e:
        logger.error(f"Ошибка получения ключевых слов: {e}")
        return set()

def save_message(message_data):
    """Сохранение сообщения в базу"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO all_messages 
            (message_id, chat_id, chat_name, user_id, username, message_text, has_keywords, keywords_found, message_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            message_data['message_id'],
            message_data['chat_id'],
            message_data['chat_name'],
            message_data['user_id'],
            message_data['username'],
            message_data['message_text'],
            message_data['has_keywords'],
            message_data['keywords_found'],
            message_data['message_type']
        ))
        
        # Обновляем статистику чата
        cursor.execute('''
            INSERT OR REPLACE INTO chat_stats 
            (chat_id, chat_name, message_count, last_activity)
            VALUES (?, ?, COALESCE((SELECT message_count FROM chat_stats WHERE chat_id = ?), 0) + 1, CURRENT_TIMESTAMP)
        ''', (message_data['chat_id'], message_data['chat_name'], message_data['chat_id']))
        
        conn.commit()
        conn.close()
        
    except Exception as e:
        logger.error(f"Ошибка сохранения сообщения: {e}")

async def check_keywords(text):
    """Проверка текста на ключевые слова"""
    if not text:
        return False, []
    
    keywords = get_keywords()
    text_lower = text.lower()
    found_keywords = [kw for kw in keywords if kw in text_lower]
    
    return len(found_keywords) > 0, found_keywords

# Команды бота
@dp.message(Command("start"))
async def cmd_start(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Доступ запрещен")
        return
    
    await message.answer(
        "🔍 <b>Система мониторинга всех сообщений</b>\n\n"
        "⚡ <b>Бот мониторит ВСЕ сообщения из ВСЕХ чатов и каналов</b>\n\n"
        "<b>Команды:</b>\n"
        "/add_keyword - добавить ключевое слово\n"
        "/keywords - список ключевых слов\n"
        "/stats - общая статистика\n"
        "/chats - активные чаты\n"
        "/alerts - сообщения с ключевыми словами\n"
        "/recent - последние сообщения\n"
        "/search - поиск по сообщениям\n\n"
        "🚨 <b>Мониторинг работает в реальном времени!</b>"
    )

@dp.message(Command("add_keyword"))
async def cmd_add_keyword(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Используйте: /add_keyword <слово или фраза>")
        return
    
    keyword = args[1].strip()
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO keywords (keyword) VALUES (?)", (keyword,))
        conn.commit()
        conn.close()
        
        await message.answer(f"✅ Ключевое слово добавлено: <code>{keyword}</code>")
        logger.info(f"Добавлено ключевое слово: {keyword}")
        
    except Exception as e:
        logger.error(f"Ошибка добавления ключевого слова: {e}")
        await message.answer("❌ Ошибка при добавлении")

@dp.message(Command("keywords"))
async def cmd_keywords(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    keywords = list(get_keywords())
    if keywords:
        text = "🔍 <b>Ключевые слова для мониторинга:</b>\n\n" + "\n".join(f"• {kw}" for kw in sorted(keywords))
        await message.answer(text)
    else:
        await message.answer("📝 Ключевые слова не добавлены")

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM all_messages")
        total_messages = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM all_messages WHERE has_keywords = 1")
        alert_messages = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM chat_stats")
        total_chats = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM keywords")
        total_keywords = cursor.fetchone()[0]
        
        cursor.execute("SELECT chat_name, message_count FROM chat_stats ORDER BY message_count DESC LIMIT 10")
        top_chats = cursor.fetchall()
        
        cursor.execute("SELECT keywords_found, COUNT(*) FROM all_messages WHERE has_keywords = 1 GROUP BY keywords_found ORDER BY COUNT(*) DESC LIMIT 10")
        top_keywords = cursor.fetchall()
        
        conn.close()
        
        stats_text = (
            f"📊 <b>Статистика мониторинга</b>\n\n"
            f"💬 <b>Всего сообщений:</b> {total_messages}\n"
            f"🚨 <b>Сообщений с ключами:</b> {alert_messages}\n"
            f"📁 <b>Активных чатов:</b> {total_chats}\n"
            f"🔍 <b>Ключевых слов:</b> {total_keywords}\n\n"
        )
        
        if top_chats:
            stats_text += "🏆 <b>Топ чатов по активности:</b>\n"
            for chat_name, count in top_chats:
                stats_text += f"• {chat_name}: {count} сообщ.\n"
        
        if top_keywords:
            stats_text += "\n🔝 <b>Топ ключевых слов:</b>\n"
            for keyword, count in top_keywords:
                stats_text += f"• {keyword}: {count}\n"
        
        await message.answer(stats_text)
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        await message.answer("❌ Ошибка получения статистики")

@dp.message(Command("chats"))
async def cmd_chats(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT chat_name, message_count, last_activity FROM chat_stats ORDER BY message_count DESC LIMIT 15")
        chats = cursor.fetchall()
        conn.close()
        
        if chats:
            text = "📁 <b>Активные чаты:</b>\n\n"
            for chat_name, count, last_active in chats:
                time_ago = datetime.now() - datetime.strptime(last_active, '%Y-%m-%d %H:%M:%S')
                hours_ago = int(time_ago.total_seconds() / 3600)
                text += f"• {chat_name}\n  📊 {count} сообщ. | ⏰ {hours_ago}ч. назад\n\n"
            await message.answer(text)
        else:
            await message.answer("📝 Чаты не найдены")
            
    except Exception as e:
        logger.error(f"Ошибка получения чатов: {e}")
        await message.answer("❌ Ошибка получения списка чатов")

@dp.message(Command("alerts"))
async def cmd_alerts(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT chat_name, username, message_text, keywords_found, timestamp 
            FROM all_messages 
            WHERE has_keywords = 1 
            ORDER BY timestamp DESC 
            LIMIT 10
        """)
        alerts = cursor.fetchall()
        conn.close()
        
        if alerts:
            text = "🚨 <b>Последние сообщения с ключевыми словами:</b>\n\n"
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
            await message.answer("📝 Сообщений с ключевыми словами не найдено")
            
    except Exception as e:
        logger.error(f"Ошибка получения алертов: {e}")
        await message.answer("❌ Ошибка получения алертов")

@dp.message(Command("recent"))
async def cmd_recent(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT chat_name, username, message_text, timestamp 
            FROM all_messages 
            ORDER BY timestamp DESC 
            LIMIT 8
        """)
        recent = cursor.fetchall()
        conn.close()
        
        if recent:
            text = "📋 <b>Последние сообщения:</b>\n\n"
            for chat, user, msg, time in recent:
                time_str = datetime.strptime(time, '%Y-%m-%d %H:%M:%S').strftime('%H:%M')
                has_keywords = any(kw in msg.lower() for kw in get_keywords())
                alert_flag = "🚨 " if has_keywords else ""
                text += f"{alert_flag}📁 <b>{chat}</b>\n"
                text += f"👤 {user or 'N/A'} | ⏰ {time_str}\n"
                text += f"💬 {msg[:50]}...\n"
                text += "────────────────────\n"
            await message.answer(text)
        else:
            await message.answer("📝 Сообщений не найдено")
            
    except Exception as e:
        logger.error(f"Ошибка получения recent: {e}")
        await message.answer("❌ Ошибка получения сообщений")

@dp.message(Command("search"))
async def cmd_search(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Используйте: /search <текст для поиска>")
        return
    
    search_text = args[1].strip().lower()
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT chat_name, username, message_text, timestamp 
            FROM all_messages 
            WHERE LOWER(message_text) LIKE ? 
            ORDER BY timestamp DESC 
            LIMIT 10
        """, (f'%{search_text}%',))
        results = cursor.fetchall()
        conn.close()
        
        if results:
            text = f"🔍 <b>Результаты поиска '{search_text}':</b>\n\n"
            for chat, user, msg, time in results:
                time_str = datetime.strptime(time, '%Y-%m-%d %H:%M:%S').strftime('%H:%M')
                # Подсветка найденного текста
                highlighted_msg = msg.replace(search_text, f"<b>{search_text}</b>")
                text += f"📁 <b>{chat}</b>\n"
                text += f"👤 {user or 'N/A'} | ⏰ {time_str}\n"
                text += f"💬 {highlighted_msg[:80]}...\n"
                text += "────────────────────\n"
            await message.answer(text)
        else:
            await message.answer(f"📝 По запросу '{search_text}' ничего не найдено")
            
    except Exception as e:
        logger.error(f"Ошибка поиска: {e}")
        await message.answer("❌ Ошибка поиска")

# Обработчик сообщений через aiogram (для ЛС бота)
@dp.message(F.text)
async def handle_private_messages(message: Message):
    """Обработка сообщений в ЛС бота"""
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        await message.answer("❌ Доступ запрещен")
        return
    
    # Сохраняем сообщения из ЛС с ботом
    message_data = {
        'message_id': message.message_id,
        'chat_id': str(message.chat.id),
        'chat_name': f"ЛС: {message.from_user.full_name}",
        'user_id': str(message.from_user.id),
        'username': message.from_user.username,
        'message_text': message.text,
        'has_keywords': False,
        'keywords_found': '',
        'message_type': 'private'
    }
    
    has_keywords, found_keywords = await check_keywords(message.text)
    if has_keywords:
        message_data['has_keywords'] = True
        message_data['keywords_found'] = ', '.join(found_keywords)
    
    save_message(message_data)

# Telethon обработчик для мониторинга всех сообщений
@telethon_client.on(events.NewMessage)
async def handle_all_telegram_messages(event):
    """Обработка ВСЕХ сообщений через Telethon"""
    try:
        # Пропускаем служебные сообщения
        if not event.message.text or event.message.text.strip() == '':
            return
        
        # Получаем информацию о чате
        chat = await event.get_chat()
        chat_id = str(chat.id)
        chat_name = getattr(chat, 'title', f"{getattr(chat, 'first_name', 'Unknown')} {getattr(chat, 'last_name', '')}").strip()
        
        # Получаем информацию об отправителе
        sender = await event.get_sender()
        user_id = str(getattr(sender, 'id', 'Unknown'))
        username = getattr(sender, 'username', 'Unknown')
        
        message_text = event.message.text
        
        # Проверяем ключевые слова
        has_keywords, found_keywords = await check_keywords(message_text)
        
        # Сохраняем сообщение
        message_data = {
            'message_id': event.message.id,
            'chat_id': chat_id,
            'chat_name': chat_name,
            'user_id': user_id,
            'username': username,
            'message_text': message_text,
            'has_keywords': has_keywords,
            'keywords_found': ', '.join(found_keywords) if found_keywords else '',
            'message_type': 'channel' if hasattr(chat, 'broadcast') and chat.broadcast else 'group'
        }
        
        save_message(message_data)
        
        # Логируем для отладки
        if has_keywords:
            logger.info(f"🚨 Найдены ключи в чате {chat_name}: {found_keywords}")
        else:
            logger.debug(f"📨 Сообщение из {chat_name}: {message_text[:50]}...")
        
        # Отправляем уведомление админам если есть ключевые слова
        if has_keywords and found_keywords:
            alert_text = (
                f"🚨 <b>Обнаружены ключевые слова!</b>\n\n"
                f"📁 <b>Чат:</b> {chat_name}\n"
                f"👤 <b>Пользователь:</b> {username} (ID: {user_id})\n"
                f"🔍 <b>Ключевые слова:</b> {', '.join(found_keywords)}\n"
                f"💬 <b>Сообщение:</b> {message_text[:150]}..."
            )
            
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(admin_id, alert_text)
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления админу {admin_id}: {e}")
                    
    except Exception as e:
        logger.error(f"❌ Ошибка обработки сообщения Telethon: {e}")

async def start_telethon_monitoring():
    """Запуск мониторинга через Telethon"""
    try:
        await telethon_client.start()
        logger.info("✅ Telethon мониторинг запущен - отслеживаются ВСЕ сообщения!")
        
        # Проверяем подключение
        me = await telethon_client.get_me()
        logger.info(f"✅ Подключено как: {me.first_name} (@{me.username})")
        
        # Запускаем прослушивание сообщений
        await telethon_client.run_until_disconnected()
        
    except Exception as e:
        logger.error(f"❌ Ошибка запуска Telethon: {e}")

# HTTP сервер для проверки здоровья
async def health_check(request):
    return web.Response(text="Full Monitoring Bot is running!")

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
    logger.info("🚀 Запуск системы мониторинга всех сообщений...")
    
    # Инициализация БД
    init_db()
    
    # Запуск HTTP сервера
    await start_http_server()
    
    # Запуск Telethon мониторинга в фоне
    asyncio.create_task(start_telethon_monitoring())
    
    # Запуск бота
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("✅ Бот запущен и мониторит ВСЕ сообщения!")
    
    # Запускаем поллинг
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
