# bot.py
import telebot
import subprocess
import os
import zipfile
import tempfile
import shutil
from telebot import types
import time
from datetime import datetime, timedelta
import psutil
import sqlite3
import logging
import threading
import re
import sys
import atexit

# --- Configuration ---
TOKEN = '8795008481:AAHVLyk6SKEShiuEi-tRB9zSjlorI47ifWQ'
OWNER_ID = 6312694584
ADMIN_ID = 8816494498
YOUR_USERNAME = '@ayaanplugs'
UPDATE_CHANNEL = '@ayaan_era'
BOT_NAME = 'AYAAN HOSTER'

# Folder setup
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
IROTECH_DIR = os.path.join(BASE_DIR, 'inf')
DATABASE_PATH = os.path.join(IROTECH_DIR, 'bot_data.db')

# File upload limits
FREE_USER_LIMIT = 2
SUBSCRIBED_USER_LIMIT = 20
ADMIN_LIMIT = 999
OWNER_LIMIT = float('inf')

# Create directories
os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(IROTECH_DIR, exist_ok=True)

# Initialize bot
bot = telebot.TeleBot(TOKEN)

# --- Data structures ---
bot_scripts = {}
user_subscriptions = {}
user_files = {}
active_users = set()
admin_ids = {ADMIN_ID, OWNER_ID}
banned_users = set()
user_limits = {}
mandatory_channels = {}
pending_zip_files = {}

# Global variable - declared at module level
bot_locked = False

# --- Logging ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Database Setup ---
DB_LOCK = threading.Lock()

def init_db():
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS subscriptions
                 (user_id INTEGER PRIMARY KEY, expiry TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_files
                 (user_id INTEGER, file_name TEXT, file_type TEXT,
                  PRIMARY KEY (user_id, file_name))''')
    c.execute('''CREATE TABLE IF NOT EXISTS active_users
                 (user_id INTEGER PRIMARY KEY, join_date TEXT, last_seen TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS admins
                 (user_id INTEGER PRIMARY KEY, added_by INTEGER, added_date TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS banned_users
                 (user_id INTEGER PRIMARY KEY, reason TEXT, banned_by INTEGER, ban_date TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_limits
                 (user_id INTEGER PRIMARY KEY, file_limit INTEGER, set_by INTEGER, set_date TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS mandatory_channels
                 (channel_id TEXT PRIMARY KEY, channel_username TEXT, channel_name TEXT,
                  added_by INTEGER, added_date TEXT)''')
    c.execute('CREATE TABLE IF NOT EXISTS install_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, module_name TEXT, package_name TEXT, status TEXT, log TEXT, install_date TEXT)')
    
    # Add owner and admin
    c.execute('INSERT OR IGNORE INTO admins (user_id, added_by, added_date) VALUES (?, ?, ?)',
              (OWNER_ID, OWNER_ID, datetime.now().isoformat()))
    if ADMIN_ID != OWNER_ID:
        c.execute('INSERT OR IGNORE INTO admins (user_id, added_by, added_date) VALUES (?, ?, ?)',
                  (ADMIN_ID, OWNER_ID, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def load_data():
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    c = conn.cursor()
    
    c.execute('SELECT user_id, expiry FROM subscriptions')
    for user_id, expiry in c.fetchall():
        try:
            user_subscriptions[user_id] = {'expiry': datetime.fromisoformat(expiry)}
        except:
            pass
    
    c.execute('SELECT user_id, file_name, file_type FROM user_files')
    for user_id, file_name, file_type in c.fetchall():
        if user_id not in user_files:
            user_files[user_id] = []
        user_files[user_id].append((file_name, file_type))
    
    c.execute('SELECT user_id FROM active_users')
    active_users.update(user_id for (user_id,) in c.fetchall())
    
    c.execute('SELECT user_id FROM admins')
    admin_ids.update(user_id for (user_id,) in c.fetchall())
    
    c.execute('SELECT user_id FROM banned_users')
    banned_users.update(user_id for (user_id,) in c.fetchall())
    
    c.execute('SELECT user_id, file_limit FROM user_limits')
    for user_id, file_limit in c.fetchall():
        user_limits[user_id] = file_limit
    
    conn.close()

init_db()
load_data()

# --- Helper Functions ---
def get_user_folder(user_id):
    user_folder = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
    os.makedirs(user_folder, exist_ok=True)
    return user_folder

def get_user_file_limit(user_id):
    if user_id == OWNER_ID:
        return OWNER_LIMIT
    if user_id in admin_ids:
        return ADMIN_LIMIT
    if user_id in user_limits:
        return user_limits[user_id]
    if user_id in user_subscriptions and user_subscriptions[user_id]['expiry'] > datetime.now():
        return SUBSCRIBED_USER_LIMIT
    return FREE_USER_LIMIT

def get_user_file_count(user_id):
    return len(user_files.get(user_id, []))

def is_user_banned(user_id):
    return user_id in banned_users

def is_bot_running(script_owner_id, file_name):
    script_key = f"{script_owner_id}_{file_name}"
    script_info = bot_scripts.get(script_key)
    if script_info and script_info.get('process'):
        try:
            proc = psutil.Process(script_info['process'].pid)
            return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        except:
            if script_key in bot_scripts:
                del bot_scripts[script_key]
            return False
    return False

def kill_process_tree(process_info):
    try:
        if 'log_file' in process_info and hasattr(process_info['log_file'], 'close'):
            try:
                process_info['log_file'].close()
            except:
                pass
        
        process = process_info.get('process')
        if process and hasattr(process, 'pid'):
            try:
                parent = psutil.Process(process.pid)
                children = parent.children(recursive=True)
                for child in children:
                    try:
                        child.terminate()
                    except:
                        pass
                gone, alive = psutil.wait_procs(children, timeout=1)
                for p in alive:
                    try:
                        p.kill()
                    except:
                        pass
                try:
                    parent.terminate()
                    parent.wait(timeout=1)
                except:
                    try:
                        parent.kill()
                    except:
                        pass
            except:
                pass
    except:
        pass

def check_mandatory_subscription(user_id):
    if not mandatory_channels:
        return True, []
    not_joined = []
    for channel_id, channel_info in mandatory_channels.items():
        try:
            chat_member = bot.get_chat_member(channel_id, user_id)
            if chat_member.status not in ['member', 'administrator', 'creator']:
                not_joined.append((channel_id, channel_info))
        except:
            not_joined.append((channel_id, channel_info))
    return not not_joined, not_joined

def create_subscription_check_message(not_joined_channels):
    message = "📢 **Join Our Channels First:**\n\n"
    markup = types.InlineKeyboardMarkup()
    for channel_id, channel_info in not_joined_channels:
        channel_username = channel_info.get('username', '')
        channel_name = channel_info.get('name', 'Channel')
        if channel_username:
            channel_link = f"https://t.me/{channel_username.replace('@', '')}"
        else:
            channel_link = f"https://t.me/c/{channel_id.replace('-100', '')}"
        message += f"• {channel_name}\n"
        markup.add(types.InlineKeyboardButton(f"Join {channel_name}", url=channel_link))
    markup.add(types.InlineKeyboardButton("✅ Verify", callback_data='check_subscription_status'))
    return message, markup

def create_main_menu_inline(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        types.InlineKeyboardButton('📢 Updates', url=f'https://t.me/{UPDATE_CHANNEL.replace("@", "")}'),
        types.InlineKeyboardButton('📤 Upload', callback_data='upload'),
        types.InlineKeyboardButton('📂 Files', callback_data='check_files'),
        types.InlineKeyboardButton('⚡ Speed', callback_data='speed'),
        types.InlineKeyboardButton('📞 Contact', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}')
    ]
    
    if user_id in admin_ids:
        admin_buttons = [
            types.InlineKeyboardButton('💳 Subs', callback_data='subscription'),
            types.InlineKeyboardButton('📊 Stats', callback_data='stats'),
            types.InlineKeyboardButton('🔒 Lock' if not bot_locked else '🔓 Unlock', 
                                     callback_data='lock_bot' if not bot_locked else 'unlock_bot'),
            types.InlineKeyboardButton('📢 Broadcast', callback_data='broadcast'),
            types.InlineKeyboardButton('👑 Admin', callback_data='admin_panel'),
            types.InlineKeyboardButton('🟢 Run All', callback_data='run_all_scripts'),
            types.InlineKeyboardButton('📢 Channel', callback_data='manage_mandatory_channels'),
            types.InlineKeyboardButton('👥 Users', callback_data='user_management'),
            types.InlineKeyboardButton('📦 Install', callback_data='admin_install'),
            types.InlineKeyboardButton('⚙️ Settings', callback_data='admin_settings')
        ]
        markup.add(buttons[0])
        markup.add(buttons[1], buttons[2])
        markup.add(buttons[3], admin_buttons[0])
        markup.add(admin_buttons[1], admin_buttons[2])
        markup.add(admin_buttons[3], admin_buttons[5])
        markup.add(admin_buttons[6], admin_buttons[8])
        markup.add(admin_buttons[7], admin_buttons[9])
        markup.add(admin_buttons[4])
        markup.add(buttons[4])
    else:
        markup.add(buttons[0])
        markup.add(buttons[1], buttons[2])
        markup.add(buttons[3])
        markup.add(types.InlineKeyboardButton('📊 Stats', callback_data='stats'))
        markup.add(buttons[4])
    return markup

def create_control_buttons(script_owner_id, file_name, is_running=True):
    markup = types.InlineKeyboardMarkup(row_width=2)
    if is_running:
        markup.row(
            types.InlineKeyboardButton("🔴 Stop", callback_data=f'stop_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🔄 Restart", callback_data=f'restart_{script_owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("📜 Logs", callback_data=f'logs_{script_owner_id}_{file_name}')
        )
    else:
        markup.row(
            types.InlineKeyboardButton("🟢 Start", callback_data=f'start_{script_owner_id}_{file_name}'),
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{script_owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("📜 Logs", callback_data=f'logs_{script_owner_id}_{file_name}')
        )
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='check_files'))
    return markup

def create_admin_panel():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add Admin', callback_data='add_admin'),
        types.InlineKeyboardButton('➖ Remove Admin', callback_data='remove_admin')
    )
    markup.row(types.InlineKeyboardButton('📋 List Admins', callback_data='list_admins'))
    markup.row(types.InlineKeyboardButton('🔙 Back', callback_data='back_to_main'))
    return markup

def create_user_management_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('🚫 Ban', callback_data='ban_user'),
        types.InlineKeyboardButton('✅ Unban', callback_data='unban_user')
    )
    markup.row(
        types.InlineKeyboardButton('👤 User Info', callback_data='user_info'),
        types.InlineKeyboardButton('👥 All Users', callback_data='all_users')
    )
    markup.row(
        types.InlineKeyboardButton('🔧 Set Limit', callback_data='set_user_limit'),
        types.InlineKeyboardButton('🗑️ Remove Limit', callback_data='remove_user_limit')
    )
    markup.row(types.InlineKeyboardButton('🔙 Back', callback_data='back_to_main'))
    return markup

def create_subscription_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add', callback_data='add_subscription'),
        types.InlineKeyboardButton('➖ Remove', callback_data='remove_subscription')
    )
    markup.row(types.InlineKeyboardButton('🔍 Check', callback_data='check_subscription'))
    markup.row(types.InlineKeyboardButton('🔙 Back', callback_data='back_to_main'))
    return markup

def create_admin_settings_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('📊 System Info', callback_data='system_info'),
        types.InlineKeyboardButton('📈 Performance', callback_data='bot_performance')
    )
    markup.row(
        types.InlineKeyboardButton('🧹 Cleanup', callback_data='cleanup_files'),
        types.InlineKeyboardButton('📋 Install Logs', callback_data='install_logs')
    )
    markup.row(types.InlineKeyboardButton('🔙 Back', callback_data='back_to_main'))
    return markup

# --- Script Running Functions ---
def log_reader(process, chat_id, file_name):
    try:
        sent_msg = bot.send_message(chat_id, f"📜 *Live Logs:* `{file_name}`\n`Starting...`", parse_mode='Markdown')
        full_log = ""
        last_update = time.time()
        for line in iter(process.stdout.readline, ''):
            if line:
                full_log += line
                if time.time() - last_update > 3.5:
                    display = "\n".join(full_log.splitlines()[-12:])
                    try:
                        bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=sent_msg.message_id,
                            text=f"📜 *Live Logs:* `{file_name}`\n```\n{display}\n```",
                            parse_mode='Markdown'
                        )
                        last_update = time.time()
                    except:
                        pass
        bot.send_message(chat_id, f"✅ `{file_name}` execution finished.")
    except Exception as e:
        print(f"Log Error: {e}")

def run_script(script_path, script_owner_id, user_folder, file_name, message_obj, attempt=1):
    max_attempts = 2
    if attempt > max_attempts:
        bot.reply_to(message_obj, f"❌ Failed to run '{file_name}' after {max_attempts} attempts.")
        return
    
    script_key = f"{script_owner_id}_{file_name}"
    
    try:
        if not os.path.exists(script_path):
            bot.reply_to(message_obj, f"❌ Script '{file_name}' not found!")
            return
        
        if attempt == 1:
            check_proc = subprocess.Popen(
                [sys.executable, script_path],
                cwd=user_folder,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                text=True,
                bufsize=1,
                encoding='utf-8',
                errors='ignore'
            )
            try:
                stdout, stderr = check_proc.communicate(timeout=5)
                if check_proc.returncode != 0 and stderr:
                    match = re.search(r"ModuleNotFoundError: No module named '(.+?)'", stderr)
                    if match:
                        module_name = match.group(1)
                        bot.reply_to(message_obj, f"📦 Installing module: `{module_name}`...", parse_mode='Markdown')
                        install_cmd = [sys.executable, '-m', 'pip', 'install', module_name]
                        install_result = subprocess.run(install_cmd, capture_output=True, text=True)
                        if install_result.returncode == 0:
                            bot.reply_to(message_obj, f"✅ Installed `{module_name}`. Retrying...", parse_mode='Markdown')
                            time.sleep(2)
                            threading.Thread(target=run_script, args=(script_path, script_owner_id, user_folder, file_name, message_obj, attempt + 1)).start()
                            return
                        else:
                            bot.reply_to(message_obj, f"❌ Failed to install `{module_name}`.")
                            return
                    else:
                        bot.reply_to(message_obj, f"❌ Error in script:\n```\n{stderr[:500]}\n```", parse_mode='Markdown')
                        return
            except subprocess.TimeoutExpired:
                if check_proc and check_proc.poll() is None:
                    check_proc.kill()
                    check_proc.communicate()
        
        log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = open(log_file_path, 'w', encoding='utf-8', errors='ignore')
        
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
        
        process = subprocess.Popen(
            [sys.executable, script_path],
            cwd=user_folder,
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.PIPE,
            startupinfo=startupinfo,
            encoding='utf-8',
            errors='ignore'
        )
        
        bot_scripts[script_key] = {
            'process': process,
            'log_file': log_file,
            'file_name': file_name,
            'chat_id': message_obj.chat.id,
            'script_owner_id': script_owner_id,
            'start_time': datetime.now(),
            'user_folder': user_folder,
            'type': 'py',
            'script_key': script_key
        }
        
        bot.reply_to(message_obj, f"✅ Script '{file_name}' started! (PID: {process.pid})")
        
    except Exception as e:
        error_msg = f"❌ Error running script '{file_name}': {str(e)}"
        logger.error(error_msg)
        bot.reply_to(message_obj, error_msg)
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]

# --- Database Functions ---
def save_user_file(user_id, file_name, file_type):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR REPLACE INTO user_files (user_id, file_name, file_type) VALUES (?, ?, ?)',
                      (user_id, file_name, file_type))
            conn.commit()
            if user_id not in user_files:
                user_files[user_id] = []
            user_files[user_id] = [(fn, ft) for fn, ft in user_files[user_id] if fn != file_name]
            user_files[user_id].append((file_name, file_type))
        except:
            pass
        finally:
            conn.close()

def add_active_user(user_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        try:
            c.execute('INSERT OR REPLACE INTO active_users (user_id, join_date, last_seen) VALUES (?, ?, ?)',
                      (user_id, datetime.now().isoformat(), datetime.now().isoformat()))
            conn.commit()
            active_users.add(user_id)
        except:
            pass
        finally:
            conn.close()

# --- Command Handlers ---
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    user_id = message.from_user.id
    
    if is_user_banned(user_id):
        bot.reply_to(message, "❌ You are banned from using this bot.")
        return
    
    is_subscribed, not_joined = check_mandatory_subscription(user_id)
    if not is_subscribed and user_id not in admin_ids:
        sub_msg, markup = create_subscription_check_message(not_joined)
        bot.reply_to(message, sub_msg, reply_markup=markup, parse_mode='Markdown')
        return
    
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Bot is locked. Try later.")
        return
    
    if user_id not in active_users:
        add_active_user(user_id)
        try:
            bot.send_message(OWNER_ID, f"🆕 New user: {message.from_user.first_name}\nID: `{user_id}`", parse_mode='Markdown')
        except:
            pass
    
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    limit_str = str(file_limit) if file_limit != float('inf') else "Unlimited"
    
    welcome_msg = f"""
╔══════════════════════════════════╗
║   🌟 {BOT_NAME} 🌟           ║
║   💫 Premium Bot Hosting        ║
║   📱 {YOUR_USERNAME}      ║
╚══════════════════════════════════╝

🤖 *Welcome, {message.from_user.first_name}!*

📊 *Your Status:*
• User ID: `{user_id}`
• Files: {current_files}/{limit_str}
• Status: {'⭐ Premium' if user_id in user_subscriptions else '🆓 Free'}

📚 *Commands:*
/start - Main menu
/upload - Upload file
/list - Check files
/help - Help

💫 *Powered by {BOT_NAME}*
    """
    
    bot.reply_to(message, welcome_msg, parse_mode='Markdown', reply_markup=create_main_menu_inline(user_id))

@bot.message_handler(commands=['upload'])
def upload_command(message):
    user_id = message.from_user.id
    
    if is_user_banned(user_id):
        bot.reply_to(message, "❌ You are banned.")
        return
    
    is_subscribed, not_joined = check_mandatory_subscription(user_id)
    if not is_subscribed and user_id not in admin_ids:
        sub_msg, markup = create_subscription_check_message(not_joined)
        bot.reply_to(message, sub_msg, reply_markup=markup, parse_mode='Markdown')
        return
    
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Bot is locked.")
        return
    
    file_limit = get_user_file_limit(user_id)
    current_files = get_user_file_count(user_id)
    if current_files >= file_limit:
        bot.reply_to(message, f"⚠️ File limit reached ({current_files}/{file_limit})")
        return
    
    bot.reply_to(message, "📤 Send your `.py`, `.js`, or `.zip` file.")

@bot.message_handler(commands=['list'])
def list_command(message):
    user_id = message.from_user.id
    
    if is_user_banned(user_id):
        bot.reply_to(message, "❌ You are banned.")
        return
    
    is_subscribed, not_joined = check_mandatory_subscription(user_id)
    if not is_subscribed and user_id not in admin_ids:
        sub_msg, markup = create_subscription_check_message(not_joined)
        bot.reply_to(message, sub_msg, reply_markup=markup, parse_mode='Markdown')
        return
    
    user_files_list = user_files.get(user_id, [])
    if not user_files_list:
        bot.reply_to(message, "📂 No files uploaded.")
        return
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    for file_name, file_type in sorted(user_files_list):
        is_running = is_bot_running(user_id, file_name)
        status = "🟢 Running" if is_running else "🔴 Stopped"
        markup.add(types.InlineKeyboardButton(f"{file_name} ({file_type}) - {status}", callback_data=f'file_{user_id}_{file_name}'))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='back_to_main'))
    
    bot.reply_to(message, "📂 Your files:", reply_markup=markup)

@bot.message_handler(commands=['ping'])
def ping_command(message):
    user_id = message.from_user.id
    if is_user_banned(user_id):
        bot.reply_to(message, "❌ You are banned.")
        return
    start = time.time()
    msg = bot.reply_to(message, "🏓 Pong!")
    latency = round((time.time() - start) * 1000, 2)
    bot.edit_message_text(f"🏓 Pong! Latency: {latency}ms\n💫 {BOT_NAME} | {YOUR_USERNAME}", 
                         message.chat.id, msg.message_id)

# --- Document Handler ---
@bot.message_handler(content_types=['document'])
def handle_file(message):
    user_id = message.from_user.id
    
    if is_user_banned(user_id):
        bot.reply_to(message, "❌ You are banned.")
        return
    
    is_subscribed, not_joined = check_mandatory_subscription(user_id)
    if not is_subscribed and user_id not in admin_ids:
        sub_msg, markup = create_subscription_check_message(not_joined)
        bot.reply_to(message, sub_msg, reply_markup=markup, parse_mode='Markdown')
        return
    
    if bot_locked and user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Bot is locked.")
        return
    
    doc = message.document
    file_name = doc.file_name
    
    if not file_name:
        bot.reply_to(message, "⚠️ No file name.")
        return
    
    file_ext = os.path.splitext(file_name)[1].lower()
    if file_ext not in ['.py', '.js', '.zip']:
        bot.reply_to(message, "⚠️ Only .py, .js, .zip allowed.")
        return
    
    if doc.file_size > 20 * 1024 * 1024:
        bot.reply_to(message, "⚠️ File too large (Max 20MB)")
        return
    
    try:
        bot.send_message(OWNER_ID, f"📤 File: {file_name}\n👤 User: {message.from_user.first_name}\n🆔 ID: `{user_id}`", parse_mode='Markdown')
    except:
        pass
    
    bot.reply_to(message, f"⏳ Downloading `{file_name}`...", parse_mode='Markdown')
    
    file_info = bot.get_file(doc.file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    
    user_folder = get_user_folder(user_id)
    file_path = os.path.join(user_folder, file_name)
    
    with open(file_path, 'wb') as f:
        f.write(downloaded_file)
    
    if file_ext == '.zip':
        process_zip(file_path, user_id, user_folder, file_name, message)
    elif file_ext == '.py':
        save_user_file(user_id, file_name, 'py')
        threading.Thread(target=run_script, args=(file_path, user_id, user_folder, file_name, message)).start()
    elif file_ext == '.js':
        save_user_file(user_id, file_name, 'js')
        bot.reply_to(message, f"✅ JS file '{file_name}' uploaded.")

def process_zip(zip_path, user_id, user_folder, file_name, message):
    try:
        temp_dir = tempfile.mkdtemp()
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)
        
        extracted = os.listdir(temp_dir)
        py_files = [f for f in extracted if f.endswith('.py')]
        
        if py_files:
            main_script = py_files[0]
            shutil.move(os.path.join(temp_dir, main_script), os.path.join(user_folder, main_script))
            save_user_file(user_id, main_script, 'py')
            bot.reply_to(message, f"✅ Extracted and running: `{main_script}`", parse_mode='Markdown')
            threading.Thread(target=run_script, args=(os.path.join(user_folder, main_script), user_id, user_folder, main_script, message)).start()
        else:
            bot.reply_to(message, "❌ No .py file found in zip.")
        
        shutil.rmtree(temp_dir)
    except Exception as e:
        bot.reply_to(message, f"❌ Error processing zip: {str(e)}")

# --- Callback Handlers ---
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.from_user.id
    data = call.data
    
    # Use global bot_locked
    global bot_locked
    
    if is_user_banned(user_id):
        bot.answer_callback_query(call.id, "❌ You are banned.", show_alert=True)
        return
    
    if data not in ['check_subscription_status', 'back_to_main']:
        is_subscribed, not_joined = check_mandatory_subscription(user_id)
        if not is_subscribed and user_id not in admin_ids:
            sub_msg, markup = create_subscription_check_message(not_joined)
            bot.answer_callback_query(call.id)
            try:
                bot.edit_message_text(sub_msg, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode='Markdown')
            except:
                pass
            return
    
    if bot_locked and user_id not in admin_ids and data not in ['back_to_main', 'speed', 'stats', 'check_subscription_status']:
        bot.answer_callback_query(call.id, "⚠️ Bot locked.", show_alert=True)
        return
    
    # --- User Callbacks ---
    if data == 'upload':
        upload_command(call.message)
        bot.answer_callback_query(call.id)
    
    elif data == 'check_files':
        list_command(call.message)
        bot.answer_callback_query(call.id)
    
    elif data == 'speed':
        bot.answer_callback_query(call.id)
        start = time.time()
        bot.edit_message_text("🏃 Testing speed...", call.message.chat.id, call.message.message_id)
        latency = round((time.time() - start) * 1000, 2)
        bot.edit_message_text(f"⚡ Speed: {latency}ms\n💫 {BOT_NAME} | {YOUR_USERNAME}", 
                             call.message.chat.id, call.message.message_id, 
                             reply_markup=create_main_menu_inline(user_id))
    
    elif data == 'stats':
        bot.answer_callback_query(call.id)
        stats_msg = f"""
📊 *Bot Statistics*
• Users: {len(active_users)}
• Files: {sum(len(f) for f in user_files.values())}
• Running: {len(bot_scripts)}
• Banned: {len(banned_users)}
• Admins: {len(admin_ids)}

💫 {BOT_NAME} | {YOUR_USERNAME}
        """
        bot.edit_message_text(stats_msg, call.message.chat.id, call.message.message_id, 
                             parse_mode='Markdown', reply_markup=create_main_menu_inline(user_id))
    
    elif data == 'back_to_main':
        bot.answer_callback_query(call.id)
        send_welcome(call.message)
    
    elif data == 'check_subscription_status':
        is_subscribed, not_joined = check_mandatory_subscription(user_id)
        if is_subscribed or user_id in admin_ids:
            bot.answer_callback_query(call.id, "✅ Subscribed!", show_alert=True)
            send_welcome(call.message)
        else:
            bot.answer_callback_query(call.id, "❌ Not subscribed!", show_alert=True)
    
    # --- File Control Callbacks ---
    elif data.startswith('file_'):
        try:
            _, owner_id, file_name = data.split('_', 2)
            owner_id = int(owner_id)
            if not (user_id == owner_id or user_id in admin_ids):
                bot.answer_callback_query(call.id, "⚠️ Not your file.", show_alert=True)
                return
            is_running = is_bot_running(owner_id, file_name)
            bot.edit_message_text(
                f"⚙️ *{file_name}*\nStatus: {'🟢 Running' if is_running else '🔴 Stopped'}\n💫 {BOT_NAME} | {YOUR_USERNAME}",
                call.message.chat.id, call.message.message_id,
                reply_markup=create_control_buttons(owner_id, file_name, is_running),
                parse_mode='Markdown'
            )
            bot.answer_callback_query(call.id)
        except:
            bot.answer_callback_query(call.id, "Error.", show_alert=True)
    
    elif data.startswith('start_'):
        try:
            _, owner_id, file_name = data.split('_', 2)
            owner_id = int(owner_id)
            if not (user_id == owner_id or user_id in admin_ids):
                bot.answer_callback_query(call.id, "⚠️ Not your file.", show_alert=True)
                return
            file_path = os.path.join(get_user_folder(owner_id), file_name)
            if os.path.exists(file_path):
                bot.answer_callback_query(call.id, "⏳ Starting...")
                threading.Thread(target=run_script, args=(file_path, owner_id, get_user_folder(owner_id), file_name, call.message)).start()
            else:
                bot.answer_callback_query(call.id, "❌ File not found.", show_alert=True)
        except:
            bot.answer_callback_query(call.id, "Error.", show_alert=True)
    
    elif data.startswith('stop_'):
        try:
            _, owner_id, file_name = data.split('_', 2)
            owner_id = int(owner_id)
            if not (user_id == owner_id or user_id in admin_ids):
                bot.answer_callback_query(call.id, "⚠️ Not your file.", show_alert=True)
                return
            script_key = f"{owner_id}_{file_name}"
            if script_key in bot_scripts:
                kill_process_tree(bot_scripts[script_key])
                del bot_scripts[script_key]
                bot.answer_callback_query(call.id, "✅ Stopped.")
                is_running = False
                bot.edit_message_text(
                    f"⚙️ *{file_name}*\nStatus: 🔴 Stopped\n💫 {BOT_NAME} | {YOUR_USERNAME}",
                    call.message.chat.id, call.message.message_id,
                    reply_markup=create_control_buttons(owner_id, file_name, False),
                    parse_mode='Markdown'
                )
            else:
                bot.answer_callback_query(call.id, "⚠️ Not running.", show_alert=True)
        except:
            bot.answer_callback_query(call.id, "Error.", show_alert=True)
    
    elif data.startswith('restart_'):
        try:
            _, owner_id, file_name = data.split('_', 2)
            owner_id = int(owner_id)
            if not (user_id == owner_id or user_id in admin_ids):
                bot.answer_callback_query(call.id, "⚠️ Not your file.", show_alert=True)
                return
            script_key = f"{owner_id}_{file_name}"
            if script_key in bot_scripts:
                kill_process_tree(bot_scripts[script_key])
                del bot_scripts[script_key]
            file_path = os.path.join(get_user_folder(owner_id), file_name)
            if os.path.exists(file_path):
                bot.answer_callback_query(call.id, "⏳ Restarting...")
                threading.Thread(target=run_script, args=(file_path, owner_id, get_user_folder(owner_id), file_name, call.message)).start()
            else:
                bot.answer_callback_query(call.id, "❌ File not found.", show_alert=True)
        except:
            bot.answer_callback_query(call.id, "Error.", show_alert=True)
    
    elif data.startswith('delete_'):
        try:
            _, owner_id, file_name = data.split('_', 2)
            owner_id = int(owner_id)
            if not (user_id == owner_id or user_id in admin_ids):
                bot.answer_callback_query(call.id, "⚠️ Not your file.", show_alert=True)
                return
            script_key = f"{owner_id}_{file_name}"
            if script_key in bot_scripts:
                kill_process_tree(bot_scripts[script_key])
                del bot_scripts[script_key]
            file_path = os.path.join(get_user_folder(owner_id), file_name)
            if os.path.exists(file_path):
                os.remove(file_path)
            bot.answer_callback_query(call.id, "🗑️ Deleted.")
            list_command(call.message)
        except:
            bot.answer_callback_query(call.id, "Error.", show_alert=True)
    
    elif data.startswith('logs_'):
        try:
            _, owner_id, file_name = data.split('_', 2)
            owner_id = int(owner_id)
            if not (user_id == owner_id or user_id in admin_ids):
                bot.answer_callback_query(call.id, "⚠️ Not your file.", show_alert=True)
                return
            log_path = os.path.join(get_user_folder(owner_id), f"{os.path.splitext(file_name)[0]}.log")
            if os.path.exists(log_path):
                with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                    log_content = f.read()
                if len(log_content) > 4096:
                    log_content = log_content[-4096:] + "\n... (truncated)"
                bot.send_message(call.message.chat.id, f"📜 *Logs for {file_name}*\n```\n{log_content}\n```\n💫 {BOT_NAME} | {YOUR_USERNAME}", parse_mode='Markdown')
                bot.answer_callback_query(call.id)
            else:
                bot.answer_callback_query(call.id, "❌ No logs found.", show_alert=True)
        except:
            bot.answer_callback_query(call.id, "Error.", show_alert=True)
    
    # --- Admin Callbacks ---
    elif data == 'subscription':
        if user_id in admin_ids:
            bot.edit_message_text("💳 Subscription Management", call.message.chat.id, call.message.message_id, reply_markup=create_subscription_menu())
            bot.answer_callback_query(call.id)
    
    elif data == 'admin_panel':
        if user_id in admin_ids:
            bot.edit_message_text("👑 Admin Panel", call.message.chat.id, call.message.message_id, reply_markup=create_admin_panel())
            bot.answer_callback_query(call.id)
    
    elif data == 'user_management':
        if user_id in admin_ids:
            bot.edit_message_text("👥 User Management", call.message.chat.id, call.message.message_id, reply_markup=create_user_management_menu())
            bot.answer_callback_query(call.id)
    
    elif data == 'admin_settings':
        if user_id in admin_ids:
            bot.edit_message_text("⚙️ Admin Settings", call.message.chat.id, call.message.message_id, reply_markup=create_admin_settings_menu())
            bot.answer_callback_query(call.id)
    
    elif data == 'lock_bot':
        if user_id in admin_ids:
            bot_locked = True
            bot.answer_callback_query(call.id, "🔒 Bot locked.")
            send_welcome(call.message)
    
    elif data == 'unlock_bot':
        if user_id in admin_ids:
            bot_locked = False
            bot.answer_callback_query(call.id, "🔓 Bot unlocked.")
            send_welcome(call.message)
    
    elif data == 'broadcast':
        if user_id in admin_ids:
            msg = bot.send_message(call.message.chat.id, "📢 Send message to broadcast:\n/cancel to cancel")
            bot.register_next_step_handler(msg, process_broadcast)
            bot.answer_callback_query(call.id)
    
    elif data == 'add_admin':
        if user_id == OWNER_ID:
            msg = bot.send_message(call.message.chat.id, "👑 Send user ID to add as admin:")
            bot.register_next_step_handler(msg, process_add_admin)
            bot.answer_callback_query(call.id)
    
    elif data == 'remove_admin':
        if user_id == OWNER_ID:
            msg = bot.send_message(call.message.chat.id, "👑 Send user ID to remove from admin:")
            bot.register_next_step_handler(msg, process_remove_admin)
            bot.answer_callback_query(call.id)
    
    elif data == 'list_admins':
        if user_id in admin_ids:
            admin_list = "\n".join([f"• `{aid}` {'👑 Owner' if aid == OWNER_ID else '🛡️ Admin'}" for aid in sorted(admin_ids)])
            bot.edit_message_text(f"👑 **Admins:**\n{admin_list}\n\n💫 {BOT_NAME} | {YOUR_USERNAME}", 
                                 call.message.chat.id, call.message.message_id, parse_mode='Markdown')
            bot.answer_callback_query(call.id)
    
    elif data == 'add_subscription':
        if user_id in admin_ids:
            msg = bot.send_message(call.message.chat.id, "💳 Send: `user_id days` (e.g., `12345678 30`)")
            bot.register_next_step_handler(msg, process_add_subscription)
            bot.answer_callback_query(call.id)
    
    elif data == 'remove_subscription':
        if user_id in admin_ids:
            msg = bot.send_message(call.message.chat.id, "💳 Send user ID to remove subscription:")
            bot.register_next_step_handler(msg, process_remove_subscription)
            bot.answer_callback_query(call.id)
    
    elif data == 'check_subscription':
        if user_id in admin_ids:
            msg = bot.send_message(call.message.chat.id, "🔍 Send user ID to check subscription:")
            bot.register_next_step_handler(msg, process_check_subscription)
            bot.answer_callback_query(call.id)
    
    elif data == 'ban_user':
        if user_id in admin_ids:
            msg = bot.send_message(call.message.chat.id, "🚫 Send `user_id reason` to ban:")
            bot.register_next_step_handler(msg, process_ban_user)
            bot.answer_callback_query(call.id)
    
    elif data == 'unban_user':
        if user_id in admin_ids:
            msg = bot.send_message(call.message.chat.id, "✅ Send user ID to unban:")
            bot.register_next_step_handler(msg, process_unban_user)
            bot.answer_callback_query(call.id)
    
    elif data == 'user_info':
        if user_id in admin_ids:
            msg = bot.send_message(call.message.chat.id, "👤 Send user ID for info:")
            bot.register_next_step_handler(msg, process_user_info)
            bot.answer_callback_query(call.id)
    
    elif data == 'all_users':
        if user_id in admin_ids:
            if active_users:
                users_list = "\n".join([f"• `{uid}` {'⭐' if uid in user_subscriptions else ''}" for uid in list(active_users)[:50]])
                bot.edit_message_text(f"👥 **Users ({len(active_users)})**\n{users_list}\n\n💫 {BOT_NAME} | {YOUR_USERNAME}", 
                                     call.message.chat.id, call.message.message_id, parse_mode='Markdown')
            else:
                bot.edit_message_text("👥 No users yet.", call.message.chat.id, call.message.message_id)
            bot.answer_callback_query(call.id)
    
    elif data == 'set_user_limit':
        if user_id in admin_ids:
            msg = bot.send_message(call.message.chat.id, "🔧 Send `user_id limit` to set:")
            bot.register_next_step_handler(msg, process_set_user_limit)
            bot.answer_callback_query(call.id)
    
    elif data == 'remove_user_limit':
        if user_id in admin_ids:
            msg = bot.send_message(call.message.chat.id, "🗑️ Send user ID to remove limit:")
            bot.register_next_step_handler(msg, process_remove_user_limit)
            bot.answer_callback_query(call.id)
    
    elif data == 'run_all_scripts':
        if user_id in admin_ids:
            bot.edit_message_text("⏳ Running all scripts...", call.message.chat.id, call.message.message_id)
            for uid, files in user_files.items():
                for fname, ftype in files:
                    fpath = os.path.join(get_user_folder(uid), fname)
                    if os.path.exists(fpath) and not is_bot_running(uid, fname):
                        threading.Thread(target=run_script, args=(fpath, uid, get_user_folder(uid), fname, call.message)).start()
            bot.edit_message_text(f"✅ Started all scripts!\n💫 {BOT_NAME} | {YOUR_USERNAME}", 
                                 call.message.chat.id, call.message.message_id)
            bot.answer_callback_query(call.id)
    
    elif data == 'admin_install':
        if user_id in admin_ids:
            msg = bot.send_message(call.message.chat.id, "📦 Send `user_id module_name` to install:")
            bot.register_next_step_handler(msg, process_admin_install)
            bot.answer_callback_query(call.id)
    
    elif data == 'manage_mandatory_channels':
        if user_id in admin_ids:
            if not mandatory_channels:
                msg_text = "📢 No mandatory channels.\nAdd a channel:"
            else:
                channels_list = "\n".join([f"• {info['name']} ({info['username']})" for info in mandatory_channels.values()])
                msg_text = f"📢 **Current Channels:**\n{channels_list}\n\nAdd a channel:"
            msg = bot.send_message(call.message.chat.id, msg_text)
            bot.register_next_step_handler(msg, process_add_mandatory_channel)
            bot.answer_callback_query(call.id)
    
    elif data == 'system_info':
        if user_id in admin_ids:
            info = f"""
📊 **System Info**
• Python: {sys.version.split()[0]}
• Users: {len(active_users)}
• Files: {sum(len(f) for f in user_files.values())}
• Running: {len(bot_scripts)}
• Banned: {len(banned_users)}
• Admins: {len(admin_ids)}
• Channels: {len(mandatory_channels)}

💫 {BOT_NAME} | {YOUR_USERNAME}
            """
            bot.edit_message_text(info, call.message.chat.id, call.message.message_id, parse_mode='Markdown')
            bot.answer_callback_query(call.id)
    
    elif data == 'bot_performance':
        if user_id in admin_ids:
            perf = f"""
📈 **Performance**
• Running Scripts: {len(bot_scripts)}
• Total Files: {sum(len(f) for f in user_files.values())}
• Active Users: {len(active_users)}
• DB Size: {os.path.getsize(DATABASE_PATH) // 1024} KB

💫 {BOT_NAME} | {YOUR_USERNAME}
            """
            bot.edit_message_text(perf, call.message.chat.id, call.message.message_id, parse_mode='Markdown')
            bot.answer_callback_query(call.id)
    
    elif data == 'cleanup_files':
        if user_id in admin_ids:
            cleaned = 0
            for uid in user_files.keys():
                folder = get_user_folder(uid)
                if os.path.exists(folder) and not os.listdir(folder):
                    try:
                        os.rmdir(folder)
                        cleaned += 1
                    except:
                        pass
            bot.edit_message_text(f"🧹 Cleaned {cleaned} empty folders.\n💫 {BOT_NAME} | {YOUR_USERNAME}", 
                                 call.message.chat.id, call.message.message_id)
            bot.answer_callback_query(call.id)
    
    elif data == 'install_logs':
        if user_id in admin_ids:
            log_msg = "📋 Install logs feature coming soon!"
            bot.edit_message_text(log_msg, call.message.chat.id, call.message.message_id)
            bot.answer_callback_query(call.id)

# --- Process Functions ---
def process_broadcast(message):
    user_id = message.from_user.id
    if user_id not in admin_ids:
        bot.reply_to(message, "⚠️ Not authorized.")
        return
    
    if message.text and message.text.lower() == '/cancel':
        bot.reply_to(message, "❌ Broadcast cancelled.")
        return
    
    content = message.text
    if not content:
        bot.reply_to(message, "❌ No content to broadcast.")
        return
    
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("✅ Confirm", callback_data='confirm_broadcast'),
        types.InlineKeyboardButton("❌ Cancel", callback_data='cancel_broadcast')
    )
    bot.reply_to(message, f"📢 Broadcast to {len(active_users)} users?\n\n```\n{content[:500]}\n```", parse_mode='Markdown', reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == 'confirm_broadcast')
def confirm_broadcast(call):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Not authorized.", show_alert=True)
        return
    
    original_msg = call.message.reply_to_message
    if not original_msg:
        bot.answer_callback_query(call.id, "❌ No message.", show_alert=True)
        return
    
    content = original_msg.text
    bot.edit_message_text("📢 Broadcasting...", call.message.chat.id, call.message.message_id)
    
    sent = 0
    failed = 0
    for uid in list(active_users):
        try:
            bot.send_message(uid, f"📢 {content}\n\n💫 {BOT_NAME} | {YOUR_USERNAME}")
            sent += 1
            time.sleep(0.1)
        except:
            failed += 1
    
    bot.edit_message_text(f"📢 Broadcast complete!\n✅ Sent: {sent}\n❌ Failed: {failed}\n💫 {BOT_NAME} | {YOUR_USERNAME}", 
                          call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == 'cancel_broadcast')
def cancel_broadcast(call):
    bot.answer_callback_query(call.id, "❌ Cancelled.")
    bot.delete_message(call.message.chat.id, call.message.message_id)

def process_add_admin(message):
    if message.from_user.id != OWNER_ID:
        return
    try:
        user_id = int(message.text.strip())
        if user_id <= 0:
            raise ValueError
        with DB_LOCK:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('INSERT OR IGNORE INTO admins (user_id, added_by, added_date) VALUES (?, ?, ?)',
                      (user_id, OWNER_ID, datetime.now().isoformat()))
            conn.commit()
            conn.close()
        admin_ids.add(user_id)
        bot.reply_to(message, f"✅ User `{user_id}` added as admin.\n💫 {BOT_NAME} | {YOUR_USERNAME}", parse_mode='Markdown')
        try:
            bot.send_message(user_id, f"🎉 You are now an admin of {BOT_NAME}!")
        except:
            pass
    except:
        bot.reply_to(message, "❌ Invalid user ID.")

def process_remove_admin(message):
    if message.from_user.id != OWNER_ID:
        return
    try:
        user_id = int(message.text.strip())
        if user_id == OWNER_ID:
            bot.reply_to(message, "⚠️ Cannot remove owner.")
            return
        with DB_LOCK:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('DELETE FROM admins WHERE user_id = ?', (user_id,))
            conn.commit()
            conn.close()
        admin_ids.discard(user_id)
        bot.reply_to(message, f"✅ User `{user_id}` removed from admin.\n💫 {BOT_NAME} | {YOUR_USERNAME}", parse_mode='Markdown')
    except:
        bot.reply_to(message, "❌ Invalid user ID.")

def process_add_subscription(message):
    if message.from_user.id not in admin_ids:
        return
    try:
        parts = message.text.split()
        user_id = int(parts[0])
        days = int(parts[1])
        expiry = datetime.now() + timedelta(days=days)
        
        with DB_LOCK:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('INSERT OR REPLACE INTO subscriptions (user_id, expiry) VALUES (?, ?)',
                      (user_id, expiry.isoformat()))
            conn.commit()
            conn.close()
        user_subscriptions[user_id] = {'expiry': expiry}
        bot.reply_to(message, f"✅ Sub added for `{user_id}` ({days} days)\nExpires: {expiry.strftime('%Y-%m-%d')}\n💫 {BOT_NAME} | {YOUR_USERNAME}", parse_mode='Markdown')
    except:
        bot.reply_to(message, "❌ Format: `user_id days`")

def process_remove_subscription(message):
    if message.from_user.id not in admin_ids:
        return
    try:
        user_id = int(message.text.strip())
        with DB_LOCK:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('DELETE FROM subscriptions WHERE user_id = ?', (user_id,))
            conn.commit()
            conn.close()
        if user_id in user_subscriptions:
            del user_subscriptions[user_id]
        bot.reply_to(message, f"✅ Sub removed for `{user_id}`\n💫 {BOT_NAME} | {YOUR_USERNAME}", parse_mode='Markdown')
    except:
        bot.reply_to(message, "❌ Invalid user ID.")

def process_check_subscription(message):
    if message.from_user.id not in admin_ids:
        return
    try:
        user_id = int(message.text.strip())
        if user_id in user_subscriptions:
            expiry = user_subscriptions[user_id]['expiry']
            days_left = (expiry - datetime.now()).days
            bot.reply_to(message, f"🔍 User `{user_id}`\nExpires: {expiry.strftime('%Y-%m-%d')}\nDays left: {days_left}\n💫 {BOT_NAME} | {YOUR_USERNAME}", parse_mode='Markdown')
        else:
            bot.reply_to(message, f"ℹ️ User `{user_id}` has no subscription.\n💫 {BOT_NAME} | {YOUR_USERNAME}", parse_mode='Markdown')
    except:
        bot.reply_to(message, "❌ Invalid user ID.")

def process_ban_user(message):
    if message.from_user.id not in admin_ids:
        return
    try:
        parts = message.text.split()
        user_id = int(parts[0])
        reason = ' '.join(parts[1:]) if len(parts) > 1 else "No reason"
        
        if user_id == OWNER_ID:
            bot.reply_to(message, "⚠️ Cannot ban owner.")
            return
        if user_id in admin_ids:
            bot.reply_to(message, "⚠️ Cannot ban admin.")
            return
        
        with DB_LOCK:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('INSERT OR REPLACE INTO banned_users (user_id, reason, banned_by, ban_date) VALUES (?, ?, ?, ?)',
                      (user_id, reason, message.from_user.id, datetime.now().isoformat()))
            conn.commit()
            conn.close()
        banned_users.add(user_id)
        bot.reply_to(message, f"🚫 Banned `{user_id}`\nReason: {reason}\n💫 {BOT_NAME} | {YOUR_USERNAME}", parse_mode='Markdown')
        try:
            bot.send_message(user_id, f"🚫 You have been banned from {BOT_NAME}.\nReason: {reason}")
        except:
            pass
    except:
        bot.reply_to(message, "❌ Format: `user_id reason`")

def process_unban_user(message):
    if message.from_user.id not in admin_ids:
        return
    try:
        user_id = int(message.text.strip())
        with DB_LOCK:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('DELETE FROM banned_users WHERE user_id = ?', (user_id,))
            conn.commit()
            conn.close()
        banned_users.discard(user_id)
        bot.reply_to(message, f"✅ Unbanned `{user_id}`\n💫 {BOT_NAME} | {YOUR_USERNAME}", parse_mode='Markdown')
        try:
            bot.send_message(user_id, f"✅ Your ban has been lifted from {BOT_NAME}!")
        except:
            pass
    except:
        bot.reply_to(message, "❌ Invalid user ID.")

def process_user_info(message):
    if message.from_user.id not in admin_ids:
        return
    try:
        user_id = int(message.text.strip())
        files = len(user_files.get(user_id, []))
        running = sum(1 for fname, _ in user_files.get(user_id, []) if is_bot_running(user_id, fname))
        is_admin = user_id in admin_ids
        is_banned = user_id in banned_users
        has_sub = user_id in user_subscriptions
        
        info = f"""
👤 **User Info: `{user_id}`**

📊 Stats:
• Files: {files}
• Running: {running}
• Admin: {'✅' if is_admin else '❌'}
• Banned: {'✅' if is_banned else '❌'}
• Premium: {'✅' if has_sub else '❌'}

💫 {BOT_NAME} | {YOUR_USERNAME}
        """
        bot.reply_to(message, info, parse_mode='Markdown')
    except:
        bot.reply_to(message, "❌ Invalid user ID.")

def process_set_user_limit(message):
    if message.from_user.id not in admin_ids:
        return
    try:
        parts = message.text.split()
        user_id = int(parts[0])
        limit = int(parts[1])
        
        with DB_LOCK:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('INSERT OR REPLACE INTO user_limits (user_id, file_limit, set_by, set_date) VALUES (?, ?, ?, ?)',
                      (user_id, limit, message.from_user.id, datetime.now().isoformat()))
            conn.commit()
            conn.close()
        user_limits[user_id] = limit
        bot.reply_to(message, f"✅ Set limit {limit} for `{user_id}`\n💫 {BOT_NAME} | {YOUR_USERNAME}", parse_mode='Markdown')
    except:
        bot.reply_to(message, "❌ Format: `user_id limit`")

def process_remove_user_limit(message):
    if message.from_user.id not in admin_ids:
        return
    try:
        user_id = int(message.text.strip())
        with DB_LOCK:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('DELETE FROM user_limits WHERE user_id = ?', (user_id,))
            conn.commit()
            conn.close()
        if user_id in user_limits:
            del user_limits[user_id]
        bot.reply_to(message, f"✅ Removed limit for `{user_id}`\n💫 {BOT_NAME} | {YOUR_USERNAME}", parse_mode='Markdown')
    except:
        bot.reply_to(message, "❌ Invalid user ID.")

def process_admin_install(message):
    if message.from_user.id not in admin_ids:
        return
    try:
        parts = message.text.split()
        user_id = int(parts[0])
        module_name = parts[1]
        
        install_cmd = [sys.executable, '-m', 'pip', 'install', module_name]
        result = subprocess.run(install_cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            bot.reply_to(message, f"✅ Installed `{module_name}` for `{user_id}`\n💫 {BOT_NAME} | {YOUR_USERNAME}", parse_mode='Markdown')
            try:
                bot.send_message(user_id, f"📦 Admin installed module: `{module_name}`")
            except:
                pass
        else:
            bot.reply_to(message, f"❌ Failed to install `{module_name}`\n```\n{result.stderr[:500]}\n```", parse_mode='Markdown')
    except:
        bot.reply_to(message, "❌ Format: `user_id module_name`")

def process_add_mandatory_channel(message):
    if message.from_user.id not in admin_ids:
        return
    try:
        channel_input = message.text.strip()
        chat = bot.get_chat(channel_input)
        channel_id = str(chat.id)
        
        mandatory_channels[channel_id] = {
            'username': f"@{chat.username}" if chat.username else '',
            'name': chat.title
        }
        
        with DB_LOCK:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('INSERT OR REPLACE INTO mandatory_channels (channel_id, channel_username, channel_name, added_by, added_date) VALUES (?, ?, ?, ?, ?)',
                      (channel_id, mandatory_channels[channel_id]['username'], mandatory_channels[channel_id]['name'], 
                       message.from_user.id, datetime.now().isoformat()))
            conn.commit()
            conn.close()
        
        bot.reply_to(message, f"✅ Added channel: {chat.title}\n💫 {BOT_NAME} | {YOUR_USERNAME}")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

# --- Cleanup ---
def cleanup():
    logger.info("Shutting down...")
    for script_key in list(bot_scripts.keys()):
        if script_key in bot_scripts:
            kill_process_tree(bot_scripts[script_key])
            del bot_scripts[script_key]

atexit.register(cleanup)

# --- Main ---
if __name__ == '__main__':
    logger.info(f"🚀 {BOT_NAME} Starting...")
    logger.info(f"👑 Owner: {OWNER_ID}")
    logger.info(f"🛡️ Admin: {ADMIN_ID}")
    logger.info(f"📱 {YOUR_USERNAME}")
    
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=30)
        except Exception as e:
            logger.error(f"Error: {e}")
            time.sleep(5)
