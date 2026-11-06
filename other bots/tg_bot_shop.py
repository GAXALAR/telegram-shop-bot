import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice

BOT_TOKEN = "ВАШ_ТОКЕН_ОТ_BOTFATHER"
PAYMENT_TOKEN = "ВАШ_ПЛАТЕЖНЫЙ_ТОКЕН"
ADMIN_ID = 123456789
PRICE = 150
FILE_PATH = "goods.txt"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
file_lock = asyncio.Lock()

async def clean_empty_lines(file_path: str):
    """Удаляет пустые строки из файла, чтобы не выдать пустоту."""
    if not os.path.exists(file_path): return
    async with file_lock:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = [line for line in f if line.strip()]
        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

async def get_and_remove_first_line(file_path: str) -> str | None:
    """Безопасно берет первый товар и удаляет его из базы."""
    async with file_lock:
        if not os.path.exists(file_path): return None
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if not lines: return None
        
        first_line = lines[0].strip()
        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(lines[1:])
        return first_line

async def count_goods(file_path: str) -> int:
    """Считает количество товаров в наличии."""
    await clean_empty_lines(file_path)
    if not os.path.exists(file_path): return 0
    async with file_lock:
        with open(file_path, "r", encoding="utf-8") as f:
            return len(f.readlines())

@dp.message(Command("add"))
async def add_goods(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    text = message.text.replace("/add", "").strip()
    if not text:
        await message.answer("⚠️ Вы не указали товары.\nПример: <code>/add Ключ1\nКлюч2</code>", parse_mode="HTML")
        return

    async with file_lock:
        with open(FILE_PATH, "a", encoding="utf-8") as f:
            f.write(f"{text}\n")
            
    stock = await count_goods(FILE_PATH)
    await message.answer(f"✅ Товары добавлены!\n📦 В наличии: {stock} шт.")

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🛒 Купить ({PRICE} руб.)", callback_data="buy_item")],
            [InlineKeyboardButton(text="📦 Наличие товара", callback_data="check_stock")]
        ]
    )
    await message.answer(
        "👋 <b>Добро пожаловать в магазин!</b>\n\nЗдесь вы можете приобрести цифровые товары. Выдача происходит автоматически.",
        reply_markup=kb,
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "check_stock")
async def check_stock(callback: types.CallbackQuery):
    stock = await count_goods(FILE_PATH)
    await callback.message.edit_text(
        f"📦 В наличии осталось: <b>{stock} шт.</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="back_home")]]
        ),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "back_home")
async def back_home(callback: types.CallbackQuery):
    await start_cmd(callback.message)
    await callback.message.delete() 

@dp.callback_query(F.data == "buy_item")
async def send_invoice(callback: types.CallbackQuery):
    stock = await count_goods(FILE_PATH)
    if stock == 0:
        await callback.answer("❌ Товар закончился! Ожидайте пополнения.", show_alert=True)
        return

    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title="Покупка цифрового товара",
        description="Уникальный ключ или данные. Выдача произойдет автоматически сразу после оплаты.",
        payload="bot_payment_payload",
        provider_token=PAYMENT_TOKEN,
        currency="RUB",
        prices=[LabeledPrice(label="Товар", amount=PRICE * 100)]
    )
    await callback.answer()

@dp.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: types.PreCheckoutQuery):
    stock = await count_goods(FILE_PATH)
    if stock > 0:
        await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)
    else:
        await bot.answer_pre_checkout_query(
            pre_checkout_query.id, 
            ok=False, 
            error_message="Извините, товар только что раскупили."
        )

@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    item = await get_and_remove_first_line(FILE_PATH)
    
    await message.answer(f"✅ <b>Оплата прошла успешно!</b>\n\nВаш товар:\n<code>{item}</code>\n\nСпасибо за покупку!", parse_mode="HTML")
    
    if ADMIN_ID != 123456789:
        try:
            username = f"@{message.from_user.username}" if message.from_user.username else str(message.from_user.id)
            await bot.send_message(
                ADMIN_ID,
                f"🔔 <b>Новая продажа!</b>\nПокупатель: {username}\nСумма: {message.successful_payment.total_amount // 100} RUB\nВыдан товар: <code>{item}</code>",
                parse_mode="HTML"
            )
        except Exception:
            pass

async def main():
    print("Бот успешно запущен!")
    if not os.path.exists(FILE_PATH):
        open(FILE_PATH, 'w').close()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())