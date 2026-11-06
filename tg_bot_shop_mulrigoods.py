import asyncio
import json
import logging
import os
import shlex
from pathlib import Path

from aiogram import Bot, Dispatcher, F, types
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import FSInputFile, KeyboardButton, LabeledPrice, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

CONFIG_FILE = Path(__file__).resolve().with_name("config.json")
CATALOG_FILE = "catalog.json"
WELCOME_IMAGE_FILE = CONFIG_FILE.with_name("welcome_image.jpg")
DEFAULT_WELCOME_TEXT = "👋 Добро пожаловать в магазин цифровых товаров!\n\nВыберите товар ниже:"
BOT_TOKEN = ""
PAYMENT_TOKEN = ""
ADMIN_ID = 0
WELCOME_TEXT = DEFAULT_WELCOME_TEXT
WELCOME_IMAGE_PATH: Path | None = None


def load_settings() -> tuple[str, str, int]:
    global WELCOME_TEXT, WELCOME_IMAGE_PATH

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            settings = json.load(f)
    except FileNotFoundError as exc:
        raise RuntimeError("Настройки не найдены. Запустите launcher.bat и заполните поля.") from exc
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError("Не удалось прочитать config.json. Откройте запускатель и сохраните настройки заново.") from exc

    bot_token = str(settings.get("BOT_TOKEN", "")).strip()
    payment_token = str(settings.get("PAYMENT_TOKEN", "")).strip()
    try:
        admin_id = int(settings.get("ADMIN_ID", 0))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("ADMIN_ID должен быть числом.") from exc

    if not bot_token or not payment_token or admin_id <= 0:
        raise RuntimeError("Заполните BOT_TOKEN, PAYMENT_TOKEN и ADMIN_ID в запускателе.")

    WELCOME_TEXT = str(settings.get("WELCOME_TEXT", DEFAULT_WELCOME_TEXT)).strip() or DEFAULT_WELCOME_TEXT
    image_name = str(settings.get("WELCOME_IMAGE", "")).strip()
    WELCOME_IMAGE_PATH = WELCOME_IMAGE_FILE if image_name == WELCOME_IMAGE_FILE.name and WELCOME_IMAGE_FILE.is_file() else None
    return bot_token, payment_token, admin_id


def save_welcome_settings() -> None:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        settings = json.load(f)
    if not isinstance(settings, dict):
        raise ValueError("Файл config.json имеет неверный формат.")

    settings["WELCOME_TEXT"] = WELCOME_TEXT
    settings["WELCOME_IMAGE"] = WELCOME_IMAGE_FILE.name if WELCOME_IMAGE_PATH else ""
    temporary_file = CONFIG_FILE.with_suffix(".tmp")
    with open(temporary_file, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    temporary_file.replace(CONFIG_FILE)


def build_default_catalog() -> dict[str, dict[str, str | int]]:
    return {
        "win10": {
            "name": "Ключ Windows 10 Pro",
            "price": 200,
            "file": "goods_win10.txt",
        },
        "netflix": {
            "name": "Аккаунт Netflix 1 месяц",
            "price": 350,
            "file": "goods_netflix.txt",
        },
    }


def load_catalog(file_path: str | Path = CATALOG_FILE) -> dict[str, dict[str, str | int]]:
    path = Path(file_path)
    if not path.exists():
        catalog = build_default_catalog()
        save_catalog(path, catalog)
        return catalog

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        catalog = build_default_catalog()
        save_catalog(path, catalog)
        return catalog

    if not isinstance(data, dict):
        catalog = build_default_catalog()
        save_catalog(path, catalog)
        return catalog

    normalized: dict[str, dict[str, str | int]] = {}
    for item_id, item_data in data.items():
        if not isinstance(item_data, dict):
            continue

        normalized[str(item_id)] = {
            "name": str(item_data.get("name", item_id)),
            "price": int(item_data.get("price", 0)),
            "file": str(item_data.get("file", f"goods_{item_id}.txt")),
        }

    if data and not normalized:
        catalog = build_default_catalog()
        save_catalog(path, catalog)
        return catalog

    return normalized


def save_catalog(file_path: str | Path = CATALOG_FILE, catalog: dict | None = None) -> dict[str, dict[str, str | int]]:
    path = Path(file_path)
    if catalog is None:
        catalog = CATALOG

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    return catalog


def upsert_product(
    file_path: str | Path = CATALOG_FILE,
    item_id: str = "",
    name: str = "",
    price: int = 0,
    stock_file: str | None = None,
) -> dict[str, dict[str, str | int]]:
    catalog = load_catalog(file_path)
    item_key = str(item_id).strip().lower().replace(" ", "_")
    if not item_key:
        raise ValueError("item_id не может быть пустым")

    if not name:
        raise ValueError("name не может быть пустым")

    existing = catalog.get(item_key)
    if not stock_file:
        stock_file = str(existing.get("file")) if isinstance(existing, dict) and existing.get("file") else f"goods_{item_key}.txt"

    catalog[item_key] = {
        "name": name,
        "price": int(price),
        "file": stock_file,
    }
    save_catalog(file_path, catalog)
    return catalog


def remove_product(file_path: str | Path = CATALOG_FILE, item_id: str = "") -> bool:
    catalog = load_catalog(file_path)
    item_key = str(item_id).strip().lower().replace(" ", "_")
    if item_key not in catalog:
        return False
    del catalog[item_key]
    save_catalog(file_path, catalog)
    return True


CATALOG = load_catalog(CATALOG_FILE)

logging.basicConfig(level=logging.INFO)
bot: Bot | None = None
dp = Dispatcher(storage=MemoryStorage())
file_lock = asyncio.Lock()


def get_bot() -> Bot:
    if bot is None:
        raise RuntimeError("Бот ещё не инициализирован.")
    return bot


class AdminStates(StatesGroup):
    adding_product = State()
    setting_price = State()
    editing_welcome = State()
    uploading_welcome_image = State()


async def clean_empty_lines(file_path: str) -> None:
    """Удаляет пустые строки из файла, чтобы не выдавать пустоты."""
    if not os.path.exists(file_path):
        return

    async with file_lock:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = [line for line in f if line.strip()]

        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(lines)


async def get_and_remove_first_line(file_path: str) -> str | None:
    """Берёт первый товар из файла и удаляет его."""
    async with file_lock:
        if not os.path.exists(file_path):
            return None

        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        if not lines:
            return None

        first_line = lines[0].strip()
        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(lines[1:])
        return first_line


async def count_goods(file_path: str) -> int:
    """Считает количество доступных товаров."""
    await clean_empty_lines(file_path)
    if not os.path.exists(file_path):
        return 0

    async with file_lock:
        with open(file_path, "r", encoding="utf-8") as f:
            return len(f.readlines())


def ensure_catalog_files() -> None:
    for item_data in CATALOG.values():
        stock_file = str(item_data["file"])
        if not os.path.exists(stock_file):
            with open(stock_file, "w", encoding="utf-8") as f:
                f.write("")


@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="📦 Каталог", callback_data="admin_catalog")
    builder.button(text="➕ Добавить товар", callback_data="admin_add_product")
    builder.button(text="💰 Изменить цену", callback_data="admin_choose_price")
    builder.button(text="🗑 Удалить товар", callback_data="admin_choose_delete")
    builder.button(text="👋 Приветствие", callback_data="admin_welcome")
    builder.adjust(2, 1, 1, 1)

    await message.answer(
        "🛠️ <b>Панель администратора</b>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


def admin_products_keyboard(action: str) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item_id, item_data in CATALOG.items():
        builder.button(
            text=f"{item_data['name']} ({item_data['price']} ₽)",
            callback_data=f"admin_{action}:{item_id}",
        )
    builder.button(text="⬅️ В админ-панель", callback_data="admin_back")
    builder.adjust(1)
    return builder.as_markup()


@dp.callback_query(F.data == "admin_back")
async def admin_back(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text("🛠️ <b>Панель администратора</b>", reply_markup=admin_main_keyboard(), parse_mode="HTML")
    await callback.answer()


def admin_main_keyboard() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📦 Каталог", callback_data="admin_catalog")
    builder.button(text="➕ Добавить товар", callback_data="admin_add_product")
    builder.button(text="💰 Изменить цену", callback_data="admin_choose_price")
    builder.button(text="🗑 Удалить товар", callback_data="admin_choose_delete")
    builder.button(text="👋 Приветствие", callback_data="admin_welcome")
    builder.adjust(2, 1, 1, 1)
    return builder.as_markup()


def welcome_settings_keyboard() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить текст", callback_data="admin_edit_welcome_text")
    builder.button(text="🖼 Загрузить изображение", callback_data="admin_upload_welcome_image")
    builder.button(text="🗑 Удалить изображение", callback_data="admin_remove_welcome_image")
    builder.button(text="⬅️ В админ-панель", callback_data="admin_back")
    builder.adjust(1)
    return builder.as_markup()


@dp.callback_query(F.data == "admin_welcome")
async def admin_welcome_menu(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    image_status = "изображение установлено" if WELCOME_IMAGE_PATH and WELCOME_IMAGE_PATH.is_file() else "изображение не установлено"
    await callback.message.edit_text(
        f"👋 Настройка приветствия\n\nТекущий текст:\n{WELCOME_TEXT}\n\n{image_status}",
        reply_markup=welcome_settings_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data == "admin_edit_welcome_text")
async def begin_edit_welcome_text(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.editing_welcome)
    await callback.message.answer("Отправьте новый текст приветствия. Для отмены отправьте /cancel")
    await callback.answer()


@dp.message(AdminStates.editing_welcome)
async def finish_edit_welcome_text(message: types.Message, state: FSMContext):
    global WELCOME_TEXT
    if message.from_user.id != ADMIN_ID:
        return
    if message.text == "/cancel":
        await state.clear()
        await message.answer("Изменение отменено.", reply_markup=admin_main_keyboard())
        return
    if not message.text or not message.text.strip():
        await message.answer("Отправьте непустой текст или /cancel.")
        return

    WELCOME_TEXT = message.text.strip()
    try:
        save_welcome_settings()
    except (OSError, json.JSONDecodeError, ValueError) as error:
        logging.exception("Could not save welcome text")
        await message.answer(f"Не удалось сохранить приветствие: {error}")
        return
    await state.clear()
    await message.answer("✅ Текст приветствия сохранён.", reply_markup=admin_main_keyboard())


@dp.callback_query(F.data == "admin_upload_welcome_image")
async def begin_upload_welcome_image(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.uploading_welcome_image)
    await callback.message.answer("Отправьте изображение как фотографию в Telegram. Для отмены отправьте /cancel")
    await callback.answer()


@dp.message(AdminStates.uploading_welcome_image, F.photo)
async def finish_upload_welcome_image(message: types.Message, state: FSMContext):
    global WELCOME_IMAGE_PATH
    if message.from_user.id != ADMIN_ID:
        return
    photo = message.photo[-1]
    try:
        await get_bot().download(photo, destination=WELCOME_IMAGE_FILE)
        WELCOME_IMAGE_PATH = WELCOME_IMAGE_FILE
        save_welcome_settings()
    except (OSError, json.JSONDecodeError, ValueError, TelegramAPIError) as error:
        logging.exception("Could not save welcome image")
        await message.answer(
            "Не удалось загрузить изображение. Попробуйте отправить картинку ещё раз "
            "как фотографию, а не как файл."
        )
        return
    await state.clear()
    await message.answer("✅ Изображение приветствия сохранено.", reply_markup=admin_main_keyboard())


@dp.message(AdminStates.uploading_welcome_image)
async def prompt_welcome_image(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    if message.text == "/cancel":
        await state.clear()
        await message.answer("Загрузка отменена.", reply_markup=admin_main_keyboard())
        return
    await message.answer("Пришлите изображение именно как фотографию или отправьте /cancel.")


@dp.callback_query(F.data == "admin_remove_welcome_image")
async def remove_welcome_image(callback: types.CallbackQuery):
    global WELCOME_IMAGE_PATH
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    try:
        if WELCOME_IMAGE_PATH and WELCOME_IMAGE_PATH.exists():
            WELCOME_IMAGE_PATH.unlink()
        WELCOME_IMAGE_PATH = None
        save_welcome_settings()
    except (OSError, json.JSONDecodeError, ValueError) as error:
        logging.exception("Could not remove welcome image")
        await callback.answer(f"Ошибка: {error}", show_alert=True)
        return
    await callback.message.edit_text("✅ Изображение удалено.", reply_markup=welcome_settings_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "admin_catalog")
async def admin_catalog_button(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    if not CATALOG:
        text = "📦 Каталог пуст."
    else:
        lines = []
        for item_id, item_data in CATALOG.items():
            stock = await count_goods(str(item_data["file"]))
            lines.append(f"• <b>{item_id}</b> — {item_data['name']} | {item_data['price']} ₽ | остаток: {stock}")
        text = "📦 <b>Текущий каталог</b>\n\n" + "\n".join(lines)
    await callback.message.edit_text(text, reply_markup=admin_main_keyboard(), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "admin_add_product")
async def begin_add_product(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.adding_product)
    await callback.message.answer(
        "Отправьте данные одной строкой в формате:\n"
        "<code>id | Название товара | цена</code>\n\n"
        "Например: <code>new | НОВЫЙ ТОВАР | 1599</code>\n"
        "Для отмены отправьте /cancel",
        parse_mode="HTML",
    )
    await callback.answer()


@dp.message(AdminStates.adding_product)
async def finish_add_product(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    if message.text == "/cancel":
        await state.clear()
        await message.answer("Добавление отменено.", reply_markup=admin_main_keyboard())
        return

    parts = [part.strip() for part in (message.text or "").split("|", maxsplit=2)]
    if len(parts) != 3 or not parts[0] or not parts[1]:
        await message.answer("⚠️ Формат не распознан. Используйте: <code>id | Название товара | цена</code>", parse_mode="HTML")
        return
    try:
        price = int(parts[2])
        if price <= 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Цена должна быть положительным целым числом. Попробуйте ещё раз.")
        return

    global CATALOG
    item_id = parts[0].lower().replace(" ", "_")
    CATALOG = upsert_product(CATALOG_FILE, item_id, parts[1], price)
    ensure_catalog_files()
    await state.clear()
    await message.answer(f"✅ Товар <b>{item_id}</b> добавлен. Цена: {price} ₽", reply_markup=admin_main_keyboard(), parse_mode="HTML")


@dp.callback_query(F.data == "admin_choose_price")
async def choose_product_price(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    if not CATALOG:
        await callback.answer("Каталог пуст", show_alert=True)
        return
    await callback.message.edit_text("Выберите товар для изменения цены:", reply_markup=admin_products_keyboard("price"))
    await callback.answer()


@dp.callback_query(F.data.startswith("admin_price:"))
async def begin_set_product_price(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    item_id = callback.data.split(":", maxsplit=1)[1]
    if item_id not in CATALOG:
        await callback.answer("Товар уже удалён", show_alert=True)
        return
    await state.set_state(AdminStates.setting_price)
    await state.update_data(item_id=item_id)
    await callback.message.answer(f"Введите новую цену для «{CATALOG[item_id]['name']}» в рублях. Для отмены отправьте /cancel")
    await callback.answer()


@dp.message(AdminStates.setting_price)
async def finish_set_product_price(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    if message.text == "/cancel":
        await state.clear()
        await message.answer("Изменение цены отменено.", reply_markup=admin_main_keyboard())
        return
    try:
        price = int(message.text)
        if price <= 0:
            raise ValueError
    except (TypeError, ValueError):
        await message.answer("⚠️ Введите положительное целое число, например <code>299</code>.", parse_mode="HTML")
        return

    data = await state.get_data()
    item_id = data["item_id"]
    if item_id not in CATALOG:
        await state.clear()
        await message.answer("Товар уже удалён из каталога.", reply_markup=admin_main_keyboard())
        return
    CATALOG[item_id]["price"] = price
    save_catalog(CATALOG_FILE, CATALOG)
    await state.clear()
    await message.answer(f"✅ Новая цена товара <b>{item_id}</b>: {price} ₽", reply_markup=admin_main_keyboard(), parse_mode="HTML")


@dp.callback_query(F.data == "admin_choose_delete")
async def choose_product_delete(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    if not CATALOG:
        await callback.answer("Каталог пуст", show_alert=True)
        return
    await callback.message.edit_text("Выберите товар для удаления:", reply_markup=admin_products_keyboard("delete"))
    await callback.answer()


@dp.callback_query(F.data.startswith("admin_delete:"))
async def confirm_product_delete(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    item_id = callback.data.split(":", maxsplit=1)[1]
    if item_id not in CATALOG:
        await callback.answer("Товар уже удалён", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="Да, удалить", callback_data=f"admin_confirm_delete:{item_id}")
    builder.button(text="Отмена", callback_data="admin_choose_delete")
    builder.adjust(1)
    await callback.message.edit_text(
        f"Удалить <b>{item_id}</b> из каталога? Файл с остатками останется на диске.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("admin_confirm_delete:"))
async def delete_product_button(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    global CATALOG
    item_id = callback.data.split(":", maxsplit=1)[1]
    if not remove_product(CATALOG_FILE, item_id):
        await callback.answer("Товар уже удалён", show_alert=True)
        return
    CATALOG = load_catalog(CATALOG_FILE)
    await callback.message.edit_text(f"✅ Товар <b>{item_id}</b> удалён из каталога.", reply_markup=admin_main_keyboard(), parse_mode="HTML")
    await callback.answer()


@dp.message(Command("catalog"))
async def catalog_list(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    if not CATALOG:
        await message.answer("❌ Каталог пуст.", parse_mode="HTML")
        return

    lines = []
    for item_id, item_data in CATALOG.items():
        stock = await count_goods(str(item_data["file"]))
        lines.append(f"• <b>{item_id}</b> — {item_data['name']} | {item_data['price']} ₽ | ост.: {stock}")

    await message.answer("📦 Текущий каталог:\n\n" + "\n".join(lines), parse_mode="HTML")


@dp.message(Command("additem"))
async def add_item_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    try:
        args = shlex.split(message.text)
    except ValueError:
        await message.answer("⚠️ Неверный формат. Пример: <code>/additem win11pro ""Ключ Windows 11 Pro"" 250</code>", parse_mode="HTML")
        return

    if len(args) < 4:
        await message.answer("⚠️ Неверный формат. Пример: <code>/additem win11pro ""Ключ Windows 11 Pro"" 250</code>", parse_mode="HTML")
        return

    item_id = args[1].strip().lower().replace(" ", "_")
    name = " ".join(args[2:-1])
    price = int(args[-1])

    if not item_id or not name or price <= 0:
        await message.answer("⚠️ Некорректные данные. ID, название и цена должны быть заполнены.", parse_mode="HTML")
        return

    global CATALOG
    CATALOG = upsert_product(CATALOG_FILE, item_id, name, price)
    ensure_catalog_files()

    await message.answer(
        f"✅ Товар <b>{item_id}</b> добавлен/обновлён.\n"
        f"Название: {name}\n"
        f"Цена: {price} ₽",
        parse_mode="HTML",
    )


@dp.message(Command("delitem"))
async def del_item_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    args = message.text.split()
    if len(args) != 2:
        await message.answer("⚠️ Использование: <code>/delitem &lt;id&gt;</code>", parse_mode="HTML")
        return

    item_id = args[1].strip().lower().replace(" ", "_")
    if not remove_product(CATALOG_FILE, item_id):
        await message.answer(f"❌ Товар <code>{item_id}</code> не найден.", parse_mode="HTML")
        return

    global CATALOG
    CATALOG = load_catalog(CATALOG_FILE)
    await message.answer(f"✅ Товар <code>{item_id}</code> удалён из каталога.", parse_mode="HTML")


@dp.message(Command("setprice"))
async def set_price_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    args = message.text.split()
    if len(args) != 3:
        await message.answer("⚠️ Использование: <code>/setprice &lt;id&gt; &lt;цена&gt;</code>", parse_mode="HTML")
        return

    item_id = args[1].strip().lower().replace(" ", "_")
    try:
        price = int(args[2])
    except ValueError:
        await message.answer("⚠️ Цена должна быть числом.", parse_mode="HTML")
        return

    if item_id not in CATALOG:
        await message.answer(f"❌ Товар <code>{item_id}</code> не найден.", parse_mode="HTML")
        return

    CATALOG[item_id]["price"] = price
    save_catalog(CATALOG_FILE, CATALOG)
    await message.answer(f"✅ Цена товара <b>{item_id}</b> обновлена: {price} ₽", parse_mode="HTML")


@dp.message(Command("addstock"))
async def add_stock_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    try:
        args = shlex.split(message.text)
    except ValueError:
        await message.answer("⚠️ Неверный формат. Пример: <code>/addstock netflix ""ключ1"" ""ключ2""</code>", parse_mode="HTML")
        return

    if len(args) < 3:
        await message.answer("⚠️ Укажите ID товара и хотя бы один ключ. Пример: <code>/addstock netflix ""ключ1"" ""ключ2""</code>", parse_mode="HTML")
        return

    item_id = args[1].strip().lower().replace(" ", "_")
    if item_id not in CATALOG:
        await message.answer(f"❌ Товар <code>{item_id}</code> не найден.", parse_mode="HTML")
        return

    file_path = str(CATALOG[item_id]["file"])
    values = args[2:]

    async with file_lock:
        with open(file_path, "a", encoding="utf-8") as f:
            for value in values:
                f.write(f"{value}\n")

    stock = await count_goods(file_path)
    await message.answer(
        f"✅ В каталог <b>{item_id}</b> добавлено {len(values)} шт.\n"
        f"📦 Остаток: {stock}",
        parse_mode="HTML",
    )


@dp.message(Command("add"))
async def add_goods(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        available_ids = ", ".join(CATALOG.keys())
        await message.answer(
            "⚠️ Ошибка формата.\n"
            "Пример:\n"
            "<code>/add win10</code>\n"
            "<code>Ключ1</code>\n"
            "<code>Ключ2</code>\n\n"
            f"Доступные ID: {available_ids}",
            parse_mode="HTML",
        )
        return

    item_key = args[1].strip().lower().replace(" ", "_")
    keys = args[2].strip()

    if not item_key or item_key not in CATALOG:
        available_ids = ", ".join(CATALOG.keys())
        await message.answer(
            f"❌ Неверный ID товара. Доступно: <code>{available_ids}</code>",
            parse_mode="HTML",
        )
        return

    if not keys:
        await message.answer("⚠️ Вы не указали значения товаров.", parse_mode="HTML")
        return

    file_path = CATALOG[item_key]["file"]
    async with file_lock:
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(f"{keys}\n")

    stock = await count_goods(file_path)
    await message.answer(
        f"✅ Товары добавлены в <b>{CATALOG[item_key]['name']}</b>!\n"
        f"📦 Остаток: {stock} шт.",
        parse_mode="HTML",
    )


@dp.message(Command("debugpay"))
async def debug_pay(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    item_id = "win10"
    item_data = CATALOG.get(item_id)
    if not item_data:
        await message.answer("❌ Товар не найден.", parse_mode="HTML")
        return

    item = await get_and_remove_first_line(item_data["file"])
    if item is None:
        await message.answer("❌ Товар закончился.", parse_mode="HTML")
        return

    await message.answer(
        f"✅ DEBUG: выдан ключ для <b>{item_data['name']}</b>\n\n<code>{item}</code>",
        parse_mode="HTML",
    )


def public_product_button_text(item_data: dict[str, str | int]) -> str:
    return f"🛒 {item_data['name']} — {item_data['price']} ₽"


def customer_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🛍 Открыть каталог")]],
        resize_keyboard=True,
        is_persistent=True,
    )


async def send_catalog(message: types.Message) -> None:
    if not CATALOG:
        await message.answer("Сейчас каталог пуст.", reply_markup=customer_menu_keyboard())
        return

    builder = InlineKeyboardBuilder()
    for item_id, item_data in CATALOG.items():
        builder.button(text=public_product_button_text(item_data), callback_data=f"buy_{item_id}")
    builder.adjust(1)
    await message.answer("🛍 Выберите товар:", reply_markup=builder.as_markup())


@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    reply_markup = customer_menu_keyboard()
    if WELCOME_IMAGE_PATH and WELCOME_IMAGE_PATH.is_file():
        image = FSInputFile(WELCOME_IMAGE_PATH)
        if len(WELCOME_TEXT) <= 1024:
            await message.answer_photo(image, caption=WELCOME_TEXT, reply_markup=reply_markup)
        else:
            await message.answer_photo(image, reply_markup=reply_markup)
            await message.answer(WELCOME_TEXT, reply_markup=reply_markup)
    else:
        await message.answer(WELCOME_TEXT, reply_markup=reply_markup)
    await send_catalog(message)


@dp.message(F.text == "🛍 Открыть каталог")
async def open_catalog_button(message: types.Message):
    await send_catalog(message)


@dp.callback_query(F.data.startswith("buy_"))
async def send_invoice(callback: types.CallbackQuery):
    item_id = callback.data[4:]
    item_data = CATALOG.get(item_id)

    if not item_data:
        await callback.answer("❌ Товар не найден!", show_alert=True)
        return

    stock = await count_goods(item_data["file"])
    if stock == 0:
        await callback.answer("❌ Товар закончился!", show_alert=True)
        return

    try:
        logging.info("Sending invoice for item=%s price=%s provider=%s", item_id, item_data["price"], PAYMENT_TOKEN[:12])
        await get_bot().send_invoice(
            chat_id=callback.message.chat.id,
            title=item_data["name"],
            description=f"Покупка: {item_data['name']}. Выдача после оплаты.",
            payload=f"payment_{item_id}",
            provider_token=PAYMENT_TOKEN,
            currency="RUB",
            prices=[LabeledPrice(label=item_data["name"], amount=item_data["price"] * 100)],
        )
        await callback.answer()
    except Exception as e:
        logging.exception("Error while sending invoice for item=%s", item_id)
        await callback.answer("❌ Ошибка оплаты. Проверьте токен провайдера и настройки Telegram Payments.", show_alert=True)
        print(f"Invoice error: {e}")


@dp.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: types.PreCheckoutQuery):
    payload = pre_checkout_query.invoice_payload
    item_id = payload.replace("payment_", "")
    item_data = CATALOG.get(item_id)

    if not item_data:
        await get_bot().answer_pre_checkout_query(
            pre_checkout_query.id,
            ok=False,
            error_message="Товар не найден.",
        )
        return

    stock = await count_goods(item_data["file"])
    if stock > 0:
        await get_bot().answer_pre_checkout_query(pre_checkout_query.id, ok=True)
    else:
        await get_bot().answer_pre_checkout_query(
            pre_checkout_query.id,
            ok=False,
            error_message="Извините, товар только что раскупили.",
        )


@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    item_id = payload.replace("payment_", "")
    item_data = CATALOG.get(item_id)

    if not item_data:
        return

    file_path = item_data["file"]
    item = await get_and_remove_first_line(file_path)

    if item is None:
        await message.answer("❌ Товар закончился, но оплата уже была принята. Обратитесь к администратору.")
        return

    await message.answer(
        f"✅ <b>Оплата прошла успешно!</b>\n\n"
        f"Товар: <b>{item_data['name']}</b>\n"
        f"Ваш ключ/данные:\n<code>{item}</code>\n\n"
        "Спасибо за покупку!",
        reply_markup=customer_menu_keyboard(),
        parse_mode="HTML",
    )

    if ADMIN_ID and ADMIN_ID != 123456789:
        try:
            username = f"@{message.from_user.username}" if message.from_user.username else str(message.from_user.id)
            await get_bot().send_message(
                ADMIN_ID,
                f"🔔 <b>Новая продажа!</b>\n"
                f"Товар: {item_data['name']}\n"
                f"Покупатель: {username}\n"
                f"Сумма: {message.successful_payment.total_amount // 100} RUB\n"
                f"Выдан: <code>{item}</code>",
                parse_mode="HTML",
            )
        except Exception:
            pass


async def main():
    global BOT_TOKEN, PAYMENT_TOKEN, ADMIN_ID, bot

    print("Бот запущен. Для остановки нажмите Ctrl+C")

    BOT_TOKEN, PAYMENT_TOKEN, ADMIN_ID = load_settings()
    bot = Bot(token=BOT_TOKEN)

    ensure_catalog_files()

    await dp.start_polling(get_bot())


if __name__ == "__main__":
    asyncio.run(main())
