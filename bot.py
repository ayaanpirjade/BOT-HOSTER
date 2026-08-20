# bot.py
import os
import sys
import subprocess
import logging
import json
import shutil
import asyncio
import signal
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from dotenv import load_dotenv
import psutil

# Load environment variables
load_dotenv()

# Configuration
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USERS = [int(id.strip()) for id in os.getenv("ALLOWED_USER_IDS", "").split(",") if id.strip()]
BASE_DIR = Path(__file__).parent
SCRIPTS_DIR = BASE_DIR / "scripts"
LOGS_DIR = BASE_DIR / "logs"
PROCESSES_FILE = BASE_DIR / "processes.json"
UPTIME_FILE = BASE_DIR / "uptime.txt"

# Branding
BOT_NAME = "AYAAN HOSTER"
BOT_USERNAME = "@ayaanplugs"
BRANDING = f"""
╔══════════════════════════════════╗
║   🌟 {BOT_NAME} 🌟        ║
║   💫 Premium Bot Hosting        ║
║   📱 {BOT_USERNAME}       ║
╚══════════════════════════════════╝
"""

# Create necessary directories
SCRIPTS_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler(BASE_DIR / 'bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Store running processes
running_processes: Dict[int, subprocess.Popen] = {}
start_time = datetime.now()

class ScriptManager:
    """Manage Python scripts and their execution"""
    
    @staticmethod
    def save_script(filename: str, content: str) -> bool:
        """Save a Python script to the scripts directory"""
        try:
            if ".." in filename or "/" in filename or "\\" in filename:
                return False
            
            filepath = SCRIPTS_DIR / filename
            with open(filepath, 'w') as f:
                f.write(content)
            return True
        except Exception as e:
            logger.error(f"Error saving script: {e}")
            return False
    
    @staticmethod
    def list_scripts() -> List[str]:
        """List all Python scripts in the scripts directory"""
        try:
            return [f.name for f in SCRIPTS_DIR.glob("*.py") if f.is_file()]
        except Exception as e:
            logger.error(f"Error listing scripts: {e}")
            return []
    
    @staticmethod
    def get_script_content(filename: str) -> Optional[str]:
        """Get the content of a script"""
        try:
            filepath = SCRIPTS_DIR / filename
            if not filepath.exists():
                return None
            with open(filepath, 'r') as f:
                return f.read()
        except Exception as e:
            logger.error(f"Error reading script: {e}")
            return None
    
    @staticmethod
    def delete_script(filename: str) -> bool:
        """Delete a Python script"""
        try:
            filepath = SCRIPTS_DIR / filename
            if filepath.exists():
                filepath.unlink()
                return True
            return False
        except Exception as e:
            logger.error(f"Error deleting script: {e}")
            return False
    
    @staticmethod
    def execute_script(filename: str, args: List[str] = None) -> subprocess.Popen:
        """Execute a Python script and return the process"""
        try:
            filepath = SCRIPTS_DIR / filename
            if not filepath.exists():
                return None
            
            cmd = [sys.executable, str(filepath)]
            if args:
                cmd.extend(args)
            
            log_file = LOGS_DIR / f"{filename}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
            
            with open(log_file, 'w') as f:
                process = subprocess.Popen(
                    cmd,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    cwd=str(BASE_DIR),
                    text=True,
                    preexec_fn=os.setsid if os.name != 'nt' else None
                )
            
            return process
        except Exception as e:
            logger.error(f"Error executing script: {e}")
            return None
    
    @staticmethod
    def stop_process(pid: int) -> bool:
        """Stop a running process"""
        try:
            if pid in running_processes:
                process = running_processes[pid]
                if os.name != 'nt':
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                else:
                    process.terminate()
                process.wait(timeout=5)
                del running_processes[pid]
                return True
            return False
        except Exception as e:
            logger.error(f"Error stopping process: {e}")
            return False
    
    @staticmethod
    def get_process_info(pid: int) -> Optional[Dict]:
        """Get information about a running process"""
        try:
            process = psutil.Process(pid)
            return {
                'pid': pid,
                'name': process.name(),
                'cmdline': ' '.join(process.cmdline()),
                'cpu_percent': process.cpu_percent(interval=0.1),
                'memory_percent': process.memory_percent(),
                'status': process.status(),
                'create_time': datetime.fromtimestamp(process.create_time()).strftime('%Y-%m-%d %H:%M:%S')
            }
        except:
            return None

# Bot command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text(
            f"{BRANDING}\n\n❌ *Unauthorized Access!*\n\n"
            f"Contact {BOT_USERNAME} for access.",
            parse_mode='Markdown'
        )
        return
    
    uptime = datetime.now() - start_time
    uptime_str = str(uptime).split('.')[0]
    
    welcome_text = f"""
{BRANDING}

🤖 *Welcome to {BOT_NAME}!*

📊 *System Status:*
┌───────────────────
│ ⏱ Uptime: `{uptime_str}`
│ 📁 Scripts: `{len(ScriptManager.list_scripts())}`
│ 🔄 Running: `{len(running_processes)}`
└───────────────────

📚 *Available Commands:*
/start - Show this message
/upload - Upload Python script
/list - List all scripts
/run - Run a script
/stop - Stop running script
/delete - Delete a script
/view - View script content
/logs - View script logs
/stats - Show system stats
/restart - Restart bot
/help - Detailed help

💫 *Powered by {BOT_NAME}*
📱 {BOT_USERNAME}
"""
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text(f"❌ Unauthorized! Contact {BOT_USERNAME}")
        return
    
    help_text = f"""
{BRANDING}

📖 *Detailed Command Guide:*

📤 */upload* - Upload Python file
   Reply with .py file

📋 */list* - List all scripts

▶️ */run script_name [args]* - Run script
   Example: /run bot.py --debug

⏹ */stop [pid]* - Stop running script
   Example: /stop 12345

🗑 */delete script_name* - Delete script

👁 */view script_name* - View content

📄 */logs script_name* - View logs

📊 */stats* - System statistics

🔄 */restart* - Restart bot

💡 *Tips:*
• Scripts run in background
• Logs saved automatically
• Use inline buttons for quick actions

*Support:* {BOT_USERNAME}
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command"""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text(f"❌ Unauthorized! Contact {BOT_USERNAME}")
        return
    
    uptime = datetime.now() - start_time
    uptime_str = str(uptime).split('.')[0]
    
    # System stats
    cpu_usage = psutil.cpu_percent(interval=0.5)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    
    stats_text = f"""
{BRANDING}

📊 *System Statistics:*

⏱ *Uptime:* `{uptime_str}`
📁 *Total Scripts:* `{len(ScriptManager.list_scripts())}`
🔄 *Running Processes:* `{len(running_processes)}`

💻 *Server Resources:*
┌─────────────────────────────
│ 💾 CPU: `{cpu_usage}%`
│ 🧠 RAM: `{memory.percent}%` ({memory.used // (1024**3)}GB/{memory.total // (1024**3)}GB)
│ 💿 Disk: `{disk.percent}%` ({disk.used // (1024**3)}GB/{disk.total // (1024**3)}GB)
└─────────────────────────────

🔴 *Running Processes:*
"""
    
    if running_processes:
        for pid, process in running_processes.items():
            info = ScriptManager.get_process_info(pid)
            if info:
                stats_text += f"• PID `{pid}` - {info['name']} (CPU: {info['cpu_percent']}%)\n"
    else:
        stats_text += "• No running processes"
    
    stats_text += f"\n\n💫 *{BOT_NAME}* | {BOT_USERNAME}"
    
    await update.message.reply_text(stats_text, parse_mode='Markdown')

async def upload_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /upload command"""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text(f"❌ Unauthorized! Contact {BOT_USERNAME}")
        return
    
    await update.message.reply_text(
        f"{BRANDING}\n\n📤 Please upload a Python (.py) file.\n"
        f"Reply to this message with the file.\n\n"
        f"💫 {BOT_NAME} | {BOT_USERNAME}"
    )
    context.user_data['awaiting_upload'] = True

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle uploaded documents"""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text(f"❌ Unauthorized! Contact {BOT_USERNAME}")
        return
    
    if not context.user_data.get('awaiting_upload', False):
        return
    
    document = update.message.document
    if not document or not document.file_name.endswith('.py'):
        await update.message.reply_text(
            f"❌ Please upload a Python (.py) file!\n\n"
            f"💫 {BOT_NAME}"
        )
        context.user_data['awaiting_upload'] = False
        return
    
    # Send typing action
    await update.message.chat.send_action(action="typing")
    
    # Download the file
    file = await document.get_file()
    content = await file.download_as_bytearray()
    content_str = content.decode('utf-8')
    
    # Save the script
    if ScriptManager.save_script(document.file_name, content_str):
        await update.message.reply_text(
            f"✅ *Script '{document.file_name}' uploaded successfully!*\n\n"
            f"📁 Scripts: `{len(ScriptManager.list_scripts())}`\n"
            f"▶️ Run with: /run {document.file_name}\n\n"
            f"💫 {BOT_NAME} | {BOT_USERNAME}",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            f"❌ Failed to upload script '{document.file_name}'\n\n"
            f"💫 {BOT_NAME}"
        )
    
    context.user_data['awaiting_upload'] = False

async def list_scripts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list command"""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text(f"❌ Unauthorized! Contact {BOT_USERNAME}")
        return
    
    scripts = ScriptManager.list_scripts()
    if not scripts:
        await update.message.reply_text(
            f"📂 *No scripts found.*\n\n"
            f"Upload your first script with /upload\n\n"
            f"💫 {BOT_NAME} | {BOT_USERNAME}",
            parse_mode='Markdown'
        )
        return
    
    # Create keyboard with script names
    keyboard = []
    for i, script in enumerate(scripts):
        if i < 20:  # Limit to 20 scripts to avoid huge keyboard
            keyboard.append([InlineKeyboardButton(f"📄 {script}", callback_data=f"script_{script}")])
    
    if len(scripts) > 20:
        keyboard.append([InlineKeyboardButton(f"➕ {len(scripts)-20} more scripts", callback_data="list_all")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"📋 *Available Scripts:* ({len(scripts)})\n\n"
        f"Click a script to view options.\n\n"
        f"💫 {BOT_NAME} | {BOT_USERNAME}",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def run_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /run command"""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text(f"❌ Unauthorized! Contact {BOT_USERNAME}")
        return
    
    args = context.args
    if not args:
        await update.message.reply_text(
            f"⚠️ *Please specify a script name.*\n"
            f"Example: /run my_script.py\n\n"
            f"📋 Use /list to see available scripts\n\n"
            f"💫 {BOT_NAME} | {BOT_USERNAME}",
            parse_mode='Markdown'
        )
        return
    
    filename = args[0]
    script_args = args[1:] if len(args) > 1 else []
    
    # Check if script exists
    if filename not in ScriptManager.list_scripts():
        await update.message.reply_text(
            f"❌ *Script '{filename}' not found!*\n\n"
            f"📋 Use /list to see available scripts\n\n"
            f"💫 {BOT_NAME} | {BOT_USERNAME}",
            parse_mode='Markdown'
        )
        return
    
    # Send typing action
    await update.message.chat.send_action(action="typing")
    
    # Execute script
    process = ScriptManager.execute_script(filename, script_args)
    if process:
        running_processes[process.pid] = process
        await update.message.reply_text(
            f"✅ *Script '{filename}' started successfully!*\n\n"
            f"🆔 PID: `{process.pid}`\n"
            f"📊 Status: `Running`\n"
            f"📄 Logs: /logs {filename}\n"
            f"⏹ Stop: /stop {process.pid}\n\n"
            f"💫 {BOT_NAME} | {BOT_USERNAME}",
            parse_mode='Markdown'
        )
        
        # Check if process is still running after 1 second
        await asyncio.sleep(1)
        if process.poll() is not None:
            await update.message.reply_text(
                f"⚠️ *Script '{filename}' finished quickly.*\n"
                f"Check logs: /logs {filename}\n\n"
                f"💫 {BOT_NAME}",
                parse_mode='Markdown'
            )
            if process.pid in running_processes:
                del running_processes[process.pid]
    else:
        await update.message.reply_text(
            f"❌ *Failed to run script '{filename}'*\n\n"
            f"Please check the script for errors.\n\n"
            f"💫 {BOT_NAME} | {BOT_USERNAME}",
            parse_mode='Markdown'
        )

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stop command"""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text(f"❌ Unauthorized! Contact {BOT_USERNAME}")
        return
    
    args = context.args
    if not args:
        # Show running processes
        if running_processes:
            msg = f"🔄 *Running Processes:*\n\n"
            for pid, process in running_processes.items():
                info = ScriptManager.get_process_info(pid)
                if info:
                    msg += f"• PID `{pid}` - {info['name']}\n"
                    msg += f"  CPU: {info['cpu_percent']}% | RAM: {info['memory_percent']:.1f}%\n"
            msg += f"\n💫 {BOT_NAME} | {BOT_USERNAME}"
            await update.message.reply_text(msg, parse_mode='Markdown')
        else:
            await update.message.reply_text(
                f"ℹ️ *No running processes.*\n\n"
                f"💫 {BOT_NAME} | {BOT_USERNAME}",
                parse_mode='Markdown'
            )
        return
    
    # Stop specific process
    try:
        pid = int(args[0])
        if ScriptManager.stop_process(pid):
            await update.message.reply_text(
                f"✅ *Process {pid} stopped successfully!*\n\n"
                f"💫 {BOT_NAME} | {BOT_USERNAME}",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text(
                f"❌ *Process {pid} not found or already stopped.*\n\n"
                f"💫 {BOT_NAME} | {BOT_USERNAME}",
                parse_mode='Markdown'
            )
    except ValueError:
        await update.message.reply_text(
            f"❌ *Invalid PID.* Please provide a valid process ID.\n\n"
            f"💫 {BOT_NAME} | {BOT_USERNAME}",
            parse_mode='Markdown'
        )

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /delete command"""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text(f"❌ Unauthorized! Contact {BOT_USERNAME}")
        return
    
    args = context.args
    if not args:
        await update.message.reply_text(
            f"⚠️ *Please specify a script name.*\n"
            f"Example: /delete my_script.py\n\n"
            f"💫 {BOT_NAME} | {BOT_USERNAME}",
            parse_mode='Markdown'
        )
        return
    
    filename = args[0]
    if ScriptManager.delete_script(filename):
        await update.message.reply_text(
            f"✅ *Script '{filename}' deleted successfully!*\n\n"
            f"💫 {BOT_NAME} | {BOT_USERNAME}",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            f"❌ *Failed to delete script '{filename}'*\n\n"
            f"💫 {BOT_NAME} | {BOT_USERNAME}",
            parse_mode='Markdown'
        )

async def view_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /view command"""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text(f"❌ Unauthorized! Contact {BOT_USERNAME}")
        return
    
    args = context.args
    if not args:
        await update.message.reply_text(
            f"⚠️ *Please specify a script name.*\n"
            f"Example: /view my_script.py\n\n"
            f"💫 {BOT_NAME} | {BOT_USERNAME}",
            parse_mode='Markdown'
        )
        return
    
    filename = args[0]
    content = ScriptManager.get_script_content(filename)
    if content:
        # Truncate long content
        if len(content) > 3500:
            content = content[:3500] + "\n... (truncated)"
        await update.message.reply_text(
            f"📄 *{filename}*\n\n```python\n{content}\n```\n\n"
            f"💫 {BOT_NAME} | {BOT_USERNAME}",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            f"❌ *Script '{filename}' not found!*\n\n"
            f"💫 {BOT_NAME} | {BOT_USERNAME}",
            parse_mode='Markdown'
        )

async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /logs command"""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text(f"❌ Unauthorized! Contact {BOT_USERNAME}")
        return
    
    args = context.args
    if not args:
        await update.message.reply_text(
            f"⚠️ *Please specify a script name.*\n"
            f"Example: /logs my_script.py\n\n"
            f"💫 {BOT_NAME} | {BOT_USERNAME}",
            parse_mode='Markdown'
        )
        return
    
    filename = args[0]
    # Get the most recent log file
    log_files = sorted(LOGS_DIR.glob(f"{filename}_*.log"), reverse=True)
    
    if not log_files:
        await update.message.reply_text(
            f"ℹ️ *No logs found for '{filename}'*\n\n"
            f"💫 {BOT_NAME} | {BOT_USERNAME}",
            parse_mode='Markdown'
        )
        return
    
    # Read the most recent log
    with open(log_files[0], 'r') as f:
        content = f.read()
    
    if content:
        if len(content) > 3500:
            content = content[-3500:] + "\n... (truncated)"
        await update.message.reply_text(
            f"📄 *Logs for {filename}*\n\n```\n{content}\n```\n\n"
            f"💫 {BOT_NAME} | {BOT_USERNAME}",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            f"ℹ️ *Log file is empty for '{filename}'*\n\n"
            f"💫 {BOT_NAME} | {BOT_USERNAME}",
            parse_mode='Markdown'
        )

async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /restart command"""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text(f"❌ Unauthorized! Contact {BOT_USERNAME}")
        return
    
    await update.message.reply_text(
        f"🔄 *Restarting {BOT_NAME}...*\n\n"
        f"⏳ Please wait a moment.\n"
        f"💫 {BOT_NAME} | {BOT_USERNAME}",
        parse_mode='Markdown'
    )
    logger.info(f"Bot restart initiated by user {user_id}")
    
    # Exit with code 0 - Render will restart the process
    sys.exit(0)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button callbacks"""
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("script_"):
        filename = query.data.replace("script_", "")
        content = ScriptManager.get_script_content(filename)
        
        if content:
            # Show script actions
            keyboard = [
                [InlineKeyboardButton("▶️ Run Script", callback_data=f"run_{filename}")],
                [InlineKeyboardButton("📄 View Content", callback_data=f"view_{filename}")],
                [InlineKeyboardButton("📋 View Logs", callback_data=f"logs_{filename}")],
                [InlineKeyboardButton("🗑 Delete Script", callback_data=f"delete_{filename}")],
                [InlineKeyboardButton("⬅️ Back", callback_data="back_to_list")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                f"📄 *{filename}*\n\n"
                f"📊 Size: `{len(content)}` characters\n"
                f"📅 Uploaded: `{datetime.fromtimestamp(Path(SCRIPTS_DIR/filename).stat().st_ctime).strftime('%Y-%m-%d %H:%M')}`\n\n"
                f"*Actions:*\n\n"
                f"💫 {BOT_NAME} | {BOT_USERNAME}",
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        else:
            await query.edit_message_text(
                f"❌ *Script '{filename}' not found!*\n\n"
                f"💫 {BOT_NAME}",
                parse_mode='Markdown'
            )
    
    elif query.data == "back_to_list":
        scripts = ScriptManager.list_scripts()
        keyboard = []
        for script in scripts[:20]:
            keyboard.append([InlineKeyboardButton(f"📄 {script}", callback_data=f"script_{script}")])
        
        if len(scripts) > 20:
            keyboard.append([InlineKeyboardButton(f"➕ {len(scripts)-20} more", callback_data="list_all")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"📋 *Available Scripts:* ({len(scripts)})\n\n"
            f"Click a script to view options.\n\n"
            f"💫 {BOT_NAME} | {BOT_USERNAME}",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    
    elif query.data.startswith("run_"):
        filename = query.data.replace("run_", "")
        process = ScriptManager.execute_script(filename)
        if process:
            running_processes[process.pid] = process
            await query.edit_message_text(
                f"✅ *Script '{filename}' started!*\n\n"
                f"🆔 PID: `{process.pid}`\n"
                f"📊 Status: `Running`\n"
                f"📄 Logs: /logs {filename}\n\n"
                f"💫 {BOT_NAME} | {BOT_USERNAME}",
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(
                f"❌ *Failed to run script '{filename}'*\n\n"
                f"💫 {BOT_NAME}",
                parse_mode='Markdown'
            )
    
    elif query.data.startswith("view_"):
        filename = query.data.replace("view_", "")
        content = ScriptManager.get_script_content(filename)
        if content:
            if len(content) > 3000:
                content = content[:3000] + "\n... (truncated)"
            await query.edit_message_text(
                f"📄 *{filename}*\n\n```python\n{content}\n```\n\n"
                f"💫 {BOT_NAME} | {BOT_USERNAME}",
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(
                f"❌ *Script '{filename}' not found!*\n\n"
                f"💫 {BOT_NAME}",
                parse_mode='Markdown'
            )
    
    elif query.data.startswith("delete_"):
        filename = query.data.replace("delete_", "")
        if ScriptManager.delete_script(filename):
            await query.edit_message_text(
                f"✅ *Script '{filename}' deleted!*\n\n"
                f"💫 {BOT_NAME} | {BOT_USERNAME}",
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(
                f"❌ *Failed to delete script '{filename}'*\n\n"
                f"💫 {BOT_NAME}",
                parse_mode='Markdown'
            )
    
    elif query.data.startswith("logs_"):
        filename = query.data.replace("logs_", "")
        log_files = sorted(LOGS_DIR.glob(f"{filename}_*.log"), reverse=True)
        
        if log_files:
            with open(log_files[0], 'r') as f:
                content = f.read()
            if content:
                if len(content) > 3000:
                    content = content[-3000:] + "\n... (truncated)"
                await query.edit_message_text(
                    f"📄 *Logs for {filename}*\n\n```\n{content}\n```\n\n"
                    f"💫 {BOT_NAME} | {BOT_USERNAME}",
                    parse_mode='Markdown'
                )
            else:
                await query.edit_message_text(
                    f"ℹ️ *Log file is empty for '{filename}'*\n\n"
                    f"💫 {BOT_NAME}",
                    parse_mode='Markdown'
                )
        else:
            await query.edit_message_text(
                f"ℹ️ *No logs found for '{filename}'*\n\n"
                f"💫 {BOT_NAME}",
                parse_mode='Markdown'
            )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f"Update {update} caused error {context.error}")
    if update and update.effective_message:
        await update.effective_message.reply_text(
            f"❌ *An error occurred.*\n\n"
            f"Please try again later or contact {BOT_USERNAME}\n\n"
            f"💫 {BOT_NAME}",
            parse_mode='Markdown'
        )

async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /ping command to check if bot is alive"""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text(f"❌ Unauthorized! Contact {BOT_USERNAME}")
        return
    
    uptime = datetime.now() - start_time
    uptime_str = str(uptime).split('.')[0]
    
    await update.message.reply_text(
        f"🏓 *Pong!*\n\n"
        f"🤖 Status: `🟢 Online`\n"
        f"⏱ Uptime: `{uptime_str}`\n"
        f"📁 Scripts: `{len(ScriptManager.list_scripts())}`\n"
        f"🔄 Processes: `{len(running_processes)}`\n\n"
        f"💫 {BOT_NAME} | {BOT_USERNAME}",
        parse_mode='Markdown'
    )

def main():
    """Main function to run the bot"""
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set!")
        sys.exit(1)
    
    if not ALLOWED_USERS:
        logger.error("ALLOWED_USER_IDS not set!")
        sys.exit(1)
    
    # Log startup
    logger.info(f"🚀 {BOT_NAME} starting...")
    logger.info(f"📱 {BOT_USERNAME}")
    logger.info(f"👥 Allowed Users: {ALLOWED_USERS}")
    
    # Create application
    application = Application.builder().token(TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("upload", upload_command))
    application.add_handler(CommandHandler("list", list_scripts))
    application.add_handler(CommandHandler("run", run_command))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CommandHandler("delete", delete_command))
    application.add_handler(CommandHandler("view", view_command))
    application.add_handler(CommandHandler("logs", logs_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("restart", restart_command))
    application.add_handler(CommandHandler("ping", ping_command))
    
    # Add document handler for uploads
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    # Add callback query handler
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Start the bot
    logger.info(f"✅ {BOT_NAME} is running!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
