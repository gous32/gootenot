"""Main Telegram bot logic and command handlers."""
import logging
from datetime import datetime

from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters
)

import config
from database import Database
from calendar_service import CalendarService, format_event_message, format_event_summary
from scheduler import NotificationScheduler

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, config.LOG_LEVEL)
)
logger = logging.getLogger(__name__)

# Conversation states
AWAITING_AUTH_CODE = 1

# Global state
db = Database()
scheduler = None


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command - begin Google Calendar authentication."""
    chat_id = update.effective_chat.id

    # Add user to database
    db.add_user(chat_id)

    # Check if already authenticated
    creds_json = db.get_user_credentials(chat_id)
    if creds_json:
        await update.message.reply_text(
            "✅ You're already authenticated!\n\n"
            "Use /summary to see today's events\n"
            "Use /reminders to configure notification times\n"
            "Use /help for more commands"
        )
        return ConversationHandler.END

    # Start OAuth flow
    try:
        auth_url, flow = CalendarService.get_authorization_url()

        # Store flow in context for later
        context.user_data['auth_flow'] = flow

        # Escape special characters in URL for Markdown
        escaped_url = auth_url.replace('_', r'\_').replace('*', r'\*').replace('[', r'\[').replace('`', r'\`')

        await update.message.reply_text(
            "🔐 *Google Calendar Authentication*\n\n"
            "To connect your Google Calendar:\n\n"
            f"1. Visit this URL:\n{escaped_url}\n\n"
            "2. Grant calendar access\n"
            "3. Copy the authorization code\n"
            "4. Send the code back to me\n\n"
            "Send /cancel to abort.",
            parse_mode='Markdown',
            disable_web_page_preview=True
        )

        return AWAITING_AUTH_CODE

    except Exception as e:
        logger.error(f"Error starting auth flow: {e}")
        await update.message.reply_text(
            "❌ Error starting authentication. Please check bot configuration."
        )
        return ConversationHandler.END


async def receive_auth_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive and process the OAuth authorization code."""
    chat_id = update.effective_chat.id
    code = update.message.text.strip()

    flow = context.user_data.get('auth_flow')
    if not flow:
        await update.message.reply_text("❌ Authentication session expired. Please /start again.")
        return ConversationHandler.END

    try:
        # Exchange code for credentials
        credentials_json = CalendarService.exchange_code_for_credentials(flow, code)

        # Save credentials
        db.save_user_credentials(chat_id, credentials_json)

        await update.message.reply_text(
            "✅ *Successfully connected to Google Calendar!*\n\n"
            "You'll now receive notifications for:\n"
            "• New and modified events\n"
            "• Upcoming event reminders (15min, 1hr before)\n"
            "• Daily summary at 7 AM\n\n"
            "Use /summary to see today's events\n"
            "Use /reminders to customize notification times\n"
            "Use /help for all commands",
            parse_mode='Markdown'
        )

        # Clean up
        context.user_data.pop('auth_flow', None)

        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error exchanging auth code: {e}")
        await update.message.reply_text(
            "❌ Invalid authorization code. Please try /start again."
        )
        return ConversationHandler.END


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the current operation."""
    await update.message.reply_text("❌ Operation cancelled.")
    context.user_data.clear()
    return ConversationHandler.END


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show today's event summary on demand."""
    chat_id = update.effective_chat.id

    creds_json = db.get_user_credentials(chat_id)
    if not creds_json:
        await update.message.reply_text(
            "❌ Not authenticated. Use /start to connect your calendar."
        )
        return

    try:
        calendar = CalendarService.from_credentials_json(creds_json)

        # Get events for today
        today = datetime.utcnow()
        events = calendar.get_events_for_day(today)

        if not events:
            await update.message.reply_text(
                "📅 *Today's Schedule*\n\nNo events scheduled for today.",
                parse_mode='Markdown'
            )
        else:
            message = f"📅 *Today's Schedule* ({len(events)} events)\n\n"
            for event in events:
                message += format_event_summary(event) + "\n\n"

            # Telegram message limit is 4096 chars
            if len(message) > 4000:
                message = message[:4000] + "\n\n... (truncated)"

            await update.message.reply_text(message, parse_mode='Markdown')

        # Update credentials if refreshed
        new_creds = calendar.get_credentials_json()
        if new_creds != creds_json:
            db.save_user_credentials(chat_id, new_creds)

    except Exception as e:
        logger.error(f"Error fetching summary: {e}")
        await update.message.reply_text("❌ Error fetching calendar events.")


async def reminders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Configure reminder notification times."""
    chat_id = update.effective_chat.id

    current_times = db.get_reminder_times(chat_id)

    await update.message.reply_text(
        f"⏰ *Reminder Settings*\n\n"
        f"Current reminders: {', '.join(str(m) + ' min' for m in current_times)} before events\n\n"
        f"To change, send comma-separated minutes:\n"
        f"Example: `15,60,1440` for 15min, 1hr, 1 day before\n\n"
        f"Or use /reminders\\_default to reset to default (15min, 1hr)",
        parse_mode='Markdown'
    )


async def reminders_default_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reset reminders to default."""
    chat_id = update.effective_chat.id
    db.set_reminder_times(chat_id, config.DEFAULT_REMINDER_TIMES)

    await update.message.reply_text(
        f"✅ Reminders reset to default: {', '.join(str(m) + ' min' for m in config.DEFAULT_REMINDER_TIMES)}"
    )


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all user data from the database."""
    chat_id = update.effective_chat.id

    success = db.clear_user_data(chat_id)

    if success:
        await update.message.reply_text(
            "✅ *All your data has been cleared*\n\n"
            "• Google Calendar credentials removed\n"
            "• Notification history deleted\n"
            "• Settings reset\n\n"
            "Use /start to reconnect your calendar.",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            "❌ Error clearing data. Please try again or contact support."
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help message."""
    help_text = """
🤖 *Google Calendar Bot Commands*

/start - Connect your Google Calendar
/summary - Show today's events
/reminders - Configure reminder times
/reminders\\_default - Reset reminders to default
/clear - Clear all your data
/help - Show this help message

*Automatic Notifications:*
• New events created
• Events modified/updated
• Reminders before events start
• Daily summary at 7 AM

*Note:* You'll receive notifications only after authentication with /start
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle reminder time configuration messages."""
    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    # Check if message looks like reminder times (comma-separated numbers)
    if ',' in text or text.isdigit():
        try:
            times = [int(t.strip()) for t in text.split(',')]

            # Validate times
            if not all(0 < t < 10080 for t in times):  # Max 1 week
                await update.message.reply_text(
                    "❌ Invalid times. Use values between 1 and 10080 minutes (1 week)."
                )
                return

            db.set_reminder_times(chat_id, times)
            await update.message.reply_text(
                f"✅ Reminders updated: {', '.join(str(m) + ' min' for m in times)} before events"
            )

        except ValueError:
            await update.message.reply_text(
                "❌ Invalid format. Send comma-separated numbers, e.g., `15,60,1440`",
                parse_mode='Markdown'
            )


async def setup_bot_commands(application: Application):
    """Set up bot command hints."""
    commands = [
        BotCommand("start", "Connect your Google Calendar"),
        BotCommand("summary", "Show today's events"),
        BotCommand("reminders", "Configure reminder times"),
        BotCommand("reminders_default", "Reset reminders to default"),
        BotCommand("clear", "Clear all your data"),
        BotCommand("help", "Show help message"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Bot commands set up")


def main():
    """Start the bot."""
    global scheduler

    logger.info("Starting Google Calendar Bot")

    # Create application
    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    # Set up scheduler with bot instance
    scheduler = NotificationScheduler(application.bot, db)
    scheduler.start()

    # Conversation handler for authentication
    auth_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start_command)],
        states={
            AWAITING_AUTH_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_auth_code)]
        },
        fallbacks=[CommandHandler('cancel', cancel_command)]
    )

    # Add handlers
    application.add_handler(auth_conv_handler)
    application.add_handler(CommandHandler('summary', summary_command))
    application.add_handler(CommandHandler('reminders', reminders_command))
    application.add_handler(CommandHandler('reminders_default', reminders_default_command))
    application.add_handler(CommandHandler('clear', clear_command))
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Set up bot commands on startup
    application.post_init = setup_bot_commands

    # Start bot
    logger.info("Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

    # Cleanup on shutdown
    scheduler.stop()


if __name__ == '__main__':
    main()
