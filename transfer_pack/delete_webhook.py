import os
import asyncio

from aiogram import Bot

async def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")
    bot = Bot(token=token.strip())
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.session.close()
    print("webhook deleted")

if __name__ == "__main__":
    asyncio.run(main())
