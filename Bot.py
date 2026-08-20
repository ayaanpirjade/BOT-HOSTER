# bot.py
import os
import sys
import subprocess
import logging
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USERS = [int(id.strip()) for id in os.getenv("ALLOWED_USER_IDS", "").split(",") if id.strip()]
BASE_DIR = Path(__file__).parent
SCRIPTS_DIR = BASE_DIR / "scripts"
LOGS_DIR = BASE_DIR / "logs"
PROCESSES_FILE = BASE_DIR / "processes.json"

# Create necessary directories
SCRIPTS_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Store running processes
running_processes: Dict[int, subprocess.Popen] = {}

class ScriptManager:
    """Manage Python scripts and their execution"""
    
    @staticmethod
    def save_script(filename: str, content: str) -> bool:
        """Save a Python script to the scripts directory"""
        try:
            # Security check: prevent directory traversal
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
            
            # Create log file
            log_file = LOGS_DIR / f"{filename}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
            
            with open(log_file, 'w') as f:
                process = subprocess.Popen(
                    cmd,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    cwd=str(BASE_DIR),
                    text=True
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
                running_processes[pid].terminate()
                running_processes[pid].wait(timeout=5)
                del running_processes[pid]
                return True
            return False
        except Exception as e:
            logger.error(f"Error stopping process: {e}")
            return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text("❌ Unauthorized access!")
        return
    
    welcome_text = (
        "🤖 *Python Script Manager Bot*\n\n"
        "I can help you manage and run Python scripts on Render.\n\n"
        "*Available Commands:*\n"
        "/start - Show this message\n"
        "/upload - Upload a Python script\n"
        "/list - List all scripts\n"
        "/run - Run a script\n"
        "/stop - Stop a running script\n"
        "/delete - Delete a script\n"
        "/view - View script content\n"
        "/logs - View script logs\n"
        "/help - Show detailed help\n"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    help_text = (
        "*Detailed Commands:*\n\n"
        "📤 */upload* - Upload a Python file\n"
        "   Reply to this command with a .py file\n\n"
        "📋 */list* - List all uploaded scripts\n\n"
        "▶️ */run script_name [args]* - Run a script\n"
        "   Example: /run my_script.py arg1 arg2\n\n"
        "⏹ */stop pid* - Stop a running script\n"
        "   Example: /stop 12345\n\n"
        "🗑 */delete script_name* - Delete a script\n\n"
        "👁 */view script_name* - View script content\n\n"
        "📄 */logs script_name* - View recent logs\n\n"
        "🔄 */restart* - Restart the bot\n"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def upload_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /upload command"""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text("❌ Unauthorized access!")
        return
    
    await update.message.reply_text(
        "📤 Please upload a Python (.py) file.\n"
        "Reply to this message with the file."
    )
    context.user_data['awaiting_upload'] = True

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle uploaded documents"""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text("❌ Unauthorized access!")
        return
    
    # Check if user is in upload mode
    if not context.user_data.get('awaiting_upload', False):
        return
    
    document = update.message.document
    if not document or not document.file_name.endswith('.py'):
        await update.message.reply_text("❌ Please upload a Python (.py) file!")
        context.user_data['awaiting_upload'] = False
        return
    
    # Download the file
    file = await document.get_file()
    content = await file.download_as_bytearray()
    content_str = content.decode('utf-8')
    
    # Save the script
    if ScriptManager.save_script(document.file_name, content_str):
        await update.message.reply_text(f"✅ Script '{document.file_name}' uploaded successfully!")
    else:
        await update.message.reply_text(f"❌ Failed to upload script '{document.file_name}'")
    
    context.user_data['awaiting_upload'] = False

async def list_scripts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list command"""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text("❌ Unauthorized access!")
        return
    
    scripts = ScriptManager.list_scripts()
    if not scripts:
        await update.message.reply_text("📂 No scripts found.")
        return
    
    # Create keyboard with script names
    keyboard = []
    for script in scripts:
        keyboard.append([InlineKeyboardButton(script, callback_data=f"script_{script}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"📋 *Available Scripts:* ({len(scripts)})\n\nClick a script to view options.",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def run_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /run command"""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text("❌ Unauthorized access!")
        return
    
    args = context.args
    if not args:
        await update.message.reply_text(
            "⚠️ Please specify a script name.\n"
            "Example: /run my_script.py"
        )
        return
    
    filename = args[0]
    script_args = args[1:] if len(args) > 1 else []
    
    # Check if script exists
    if filename not in ScriptManager.list_scripts():
        await update.message.reply_text(f"❌ Script '{filename}' not found!")
        return
    
    # Execute script
    process = ScriptManager.execute_script(filename, script_args)
    if process:
        running_processes[process.pid] = process
        await update.message.reply_text(
            f"✅ Script '{filename}' started!\n"
            f"PID: `{process.pid}`\n"
            f"Logs: `/logs {filename}`",
            parse_mode='Markdown'
        )
        
        # Check if process is still running after 1 second
        await asyncio.sleep(1)
        if process.poll() is not None:
            await update.message.reply_text(
                f"⚠️ Script '{filename}' finished quickly.\n"
                f"Check logs: /logs {filename}"
            )
            if process.pid in running_processes:
                del running_processes[process.pid]
    else:
        await update.message.reply_text(f"❌ Failed to run script '{filename}'")

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stop command"""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text("❌ Unauthorized access!")
        return
    
    args = context.args
    if not args:
        # Show running processes
        if running_processes:
            msg = "🔄 *Running Processes:*\n\n"
            for pid, process in running_processes.items():
                try:
                    cmd = ' '.join(process.args)
                    msg += f"PID: `{pid}` - {cmd}\n"
                except:
                    msg += f"PID: `{pid}`\n"
            await update.message.reply_text(msg, parse_mode='Markdown')
        else:
            await update.message.reply_text("ℹ️ No running processes.")
        return
    
    # Stop specific process
    try:
        pid = int(args[0])
        if ScriptManager.stop_process(pid):
            await update.message.reply_text(f"✅ Process {pid} stopped successfully!")
        else:
            await update.message.reply_text(f"❌ Process {pid} not found or already stopped.")
    except ValueError:
        await update.message.reply_text("❌ Invalid PID. Please provide a valid process ID.")

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /delete command"""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text("❌ Unauthorized access!")
        return
    
    args = context.args
    if not args:
        await update.message.reply_text(
            "⚠️ Please specify a script name.\n"
            "Example: /delete my_script.py"
        )
        return
    
    filename = args[0]
    if ScriptManager.delete_script(filename):
        await update.message.reply_text(f"✅ Script '{filename}' deleted successfully!")
    else:
        await update.message.reply_text(f"❌ Failed to delete script '{filename}'")

async def view_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /view command"""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text("❌ Unauthorized access!")
        return
    
    args = context.args
    if not args:
        await update.message.reply_text(
            "⚠️ Please specify a script name.\n"
            "Example: /view my_script.py"
        )
        return
    
    filename = args[0]
    content = ScriptManager.get_script_content(filename)
    if content:
        # Truncate long content
        if len(content) > 4000:
            content = content[:4000] + "\n... (truncated)"
        await update.message.reply_text(
            f"📄 *{filename}*\n\n```python\n{content}\n```",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(f"❌ Script '{filename}' not found!")

async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /logs command"""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text("❌ Unauthorized access!")
        return
    
    args = context.args
    if not args:
        await update.message.reply_text(
            "⚠️ Please specify a script name.\n"
            "Example: /logs my_script.py"
        )
        return
    
    filename = args[0]
    # Get the most recent log file
    log_files = sorted(LOGS_DIR.glob(f"{filename}_*.log"), reverse=True)
    
    if not log_files:
        await update.message.reply_text(f"ℹ️ No logs found for '{filename}'")
        return
    
    # Read the most recent log
    with open(log_files[0], 'r') as f:
        content = f.read()
    
    if content:
        if len(content) > 4000:
            content = content[-4000:] + "\n... (truncated)"
        await update.message.reply_text(
            f"📄 *Logs for {filename}*\n\n```\n{content}\n```",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(f"ℹ️ Log file is empty for '{filename}'")

async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /restart command"""
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text("❌ Unauthorized access!")
        return
    
    await update.message.reply_text("🔄 Restarting bot...")
    logger.info("Bot restart initiated by user")
    
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
                [InlineKeyboardButton("▶️ Run", callback_data=f"run_{filename}")],
                [InlineKeyboardButton("📄 View", callback_data=f"view_{filename}")],
                [InlineKeyboardButton("🗑 Delete", callback_data=f"delete_{filename}")],
                [InlineKeyboardButton("📋 Logs", callback_data=f"logs_{filename}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                f"📄 *{filename}*\n\nActions:",
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        else:
            await query.edit_message_text(f"❌ Script '{filename}' not found!")
    
    elif query.data.startswith("run_"):
        filename = query.data.replace("run_", "")
        process = ScriptManager.execute_script(filename)
        if process:
            running_processes[process.pid] = process
            await query.edit_message_text(
                f"✅ Script '{filename}' started!\nPID: `{process.pid}`",
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(f"❌ Failed to run script '{filename}'")
    
    elif query.data.startswith("view_"):
        filename = query.data.replace("view_", "")
        content = ScriptManager.get_script_content(filename)
        if content:
            if len(content) > 3500:
                content = content[:3500] + "\n... (truncated)"
            await query.edit_message_text(
                f"📄 *{filename}*\n\n```python\n{content}\n```",
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(f"❌ Script '{filename}' not found!")
    
    elif query.data.startswith("delete_"):
        filename = query.data.replace("delete_", "")
        if ScriptManager.delete_script(filename):
            await query.edit_message_text(f"✅ Script '{filename}' deleted!")
        else:
            await query.edit_message_text(f"❌ Failed to delete script '{filename}'")
    
    elif query.data.startswith("logs_"):
        filename = query.data.replace("logs_", "")
        log_files = sorted(LOGS_DIR.glob(f"{filename}_*.log"), reverse=True)
        
        if log_files:
            with open(log_files[0], 'r') as f:
                content = f.read()
            if content:
                if len(content) > 3500:
                    content = content[-3500:] + "\n... (truncated)"
                await query.edit_message_text(
                    f"📄 *Logs for {filename}*\n\n```\n{content}\n```",
                    parse_mode='Markdown'
                )
            else:
                await query.edit_message_text(f"ℹ️ Log file is empty for '{filename}'")
        else:
            await query.edit_message_text(f"ℹ️ No logs found for '{filename}'")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f"Update {update} caused error {context.error}")
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "❌ An error occurred. Please try again later."
        )

def main():
    """Main function to run the bot"""
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set!")
        sys.exit(1)
    
    if not ALLOWED_USERS:
        logger.error("ALLOWED_USER_IDS not set!")
        sys.exit(1)
    
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
    application.add_handler(CommandHandler("restart", restart_command))
    
    # Add document handler for uploads
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    # Add callback query handler
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Start the bot
    logger.info("Bot started!")
    application.run_polling()

if __name__ == "__main__":
    import asyncio
    main()
