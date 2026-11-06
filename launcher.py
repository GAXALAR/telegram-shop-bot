import json
import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

# Source attribution: by GAXALAR

PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = PROJECT_DIR / "config.json"
BOT_FILE = PROJECT_DIR / "tg_bot_shop_mulrigoods.py"
REQUIREMENTS_FILE = PROJECT_DIR / "requirements.txt"
MIN_PYTHON = (3, 10)


class BotLauncher:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Настройка и запуск Telegram-бота")
        self.root.resizable(False, False)
        self.root.geometry("590x470")

        self.python_status = tk.StringVar()
        self.aiogram_status = tk.StringVar()
        self.show_tokens = tk.BooleanVar(value=False)
        self.aiogram_ready = False
        self.entries: dict[str, ttk.Entry] = {}

        self._build_ui()
        self._load_config()
        self.check_dependencies()

    def _build_ui(self) -> None:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")

        content = ttk.Frame(self.root, padding=22)
        content.pack(fill="both", expand=True)

        ttk.Label(content, text="Подготовка бота к запуску", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(
            content,
            text="Проверьте среду и укажите данные Telegram и платежного провайдера.",
        ).pack(anchor="w", pady=(4, 16))

        checks = ttk.LabelFrame(content, text="Проверка системы", padding=(12, 8))
        checks.pack(fill="x", pady=(0, 16))
        ttk.Label(checks, textvariable=self.python_status).pack(anchor="w")
        ttk.Label(checks, textvariable=self.aiogram_status).pack(anchor="w", pady=(3, 0))

        form = ttk.Frame(content)
        form.pack(fill="x")
        fields = [
            ("BOT_TOKEN", "Токен Telegram-бота"),
            ("PAYMENT_TOKEN", "Платёжный токен"),
            ("ADMIN_ID", "Telegram ID администратора"),
        ]
        for row, (key, label) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=6)
            entry = ttk.Entry(form, width=49, show="*" if key != "ADMIN_ID" else "")
            entry.grid(row=row, column=1, sticky="ew", padx=(14, 0), pady=6)
            self._bind_entry_actions(entry)
            self.entries[key] = entry
        form.columnconfigure(1, weight=1)

        ttk.Checkbutton(
            content,
            text="Показывать токены",
            variable=self.show_tokens,
            command=self._toggle_tokens,
        ).pack(anchor="w", pady=(5, 12))

        actions = ttk.Frame(content)
        actions.pack(fill="x", pady=(4, 0))
        ttk.Button(actions, text="Проверить ещё раз", command=self.check_dependencies).grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=4)
        ttk.Button(actions, text="Установить aiogram", command=self.install_aiogram).grid(row=0, column=1, sticky="ew", padx=6, pady=4)
        ttk.Button(actions, text="Сохранить настройки", command=self.save_settings).grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=4)
        ttk.Button(actions, text="Сохранить и запустить", command=self.save_and_launch).grid(row=1, column=1, sticky="ew", padx=6, pady=4)
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)

        ttk.Label(
            content,
            text="Настройки хранятся в config.json рядом с программой. Не отправляйте этот файл другим людям.",
            wraplength=540,
        ).pack(anchor="w", pady=(14, 0))
        ttk.Label(self.root, text="by GAXALAR", font=("Segoe UI", 8), foreground="#777777").place(
            relx=1.0,
            rely=1.0,
            x=-14,
            y=-8,
            anchor="se",
        )

    def _bind_entry_actions(self, entry: ttk.Entry) -> None:
        entry.bind("<Control-v>", self._paste_entry)
        entry.bind("<Control-V>", self._paste_entry)
        entry.bind("<Shift-Insert>", self._paste_entry)
        entry.bind("<<Paste>>", self._paste_entry)
        entry.bind("<Button-3>", lambda event: self._show_entry_menu(event, entry))

    def _paste_entry(self, event: tk.Event) -> str:
        entry = event.widget
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            self.root.bell()
            return "break"

        try:
            selection_start = entry.index(tk.SEL_FIRST)
            selection_end = entry.index(tk.SEL_LAST)
            entry.delete(selection_start, selection_end)
        except tk.TclError:
            pass

        entry.insert(tk.INSERT, text)
        return "break"

    def _show_entry_menu(self, event: tk.Event, entry: ttk.Entry) -> str:
        entry.focus_set()
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label="Вырезать", command=lambda: entry.event_generate("<<Cut>>"))
        menu.add_command(label="Копировать", command=lambda: entry.event_generate("<<Copy>>"))
        menu.add_command(label="Вставить", command=lambda: self._paste_entry_for(entry))
        menu.add_separator()
        menu.add_command(label="Выделить всё", command=lambda: self._select_all(entry))
        menu.tk_popup(event.x_root, event.y_root)
        menu.grab_release()
        return "break"

    def _paste_entry_for(self, entry: ttk.Entry) -> None:
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            self.root.bell()
            return
        try:
            entry.delete(tk.SEL_FIRST, tk.SEL_LAST)
        except tk.TclError:
            pass
        entry.insert(tk.INSERT, text)

    @staticmethod
    def _select_all(entry: ttk.Entry) -> None:
        entry.selection_range(0, tk.END)
        entry.icursor(tk.END)

    def _toggle_tokens(self) -> None:
        mask = "" if self.show_tokens.get() else "*"
        for key in ("BOT_TOKEN", "PAYMENT_TOKEN"):
            self.entries[key].configure(show=mask)

    def _load_config(self) -> None:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as config_file:
                settings = json.load(config_file)
        except (OSError, json.JSONDecodeError):
            return

        if not isinstance(settings, dict):
            return
        for key, entry in self.entries.items():
            value = settings.get(key)
            if value is not None:
                entry.insert(0, str(value))

    def check_dependencies(self) -> None:
        version = sys.version_info
        python_ok = version >= MIN_PYTHON
        self.python_status.set(
            f"{'✅' if python_ok else '❌'} Python {version.major}.{version.minor}.{version.micro} "
            f"({'подходит' if python_ok else 'нужна версия 3.10 или новее'})"
        )

        result = subprocess.run(
            [sys.executable, "-c", "import aiogram"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.aiogram_ready = result.returncode == 0
        self.aiogram_status.set(
            "✅ Библиотека aiogram установлена" if self.aiogram_ready else "❌ Библиотека aiogram не найдена"
        )

    def install_aiogram(self) -> None:
        if sys.version_info < MIN_PYTHON:
            messagebox.showerror("Нужен Python 3.10+", "Установите Python 3.10 или новее и запустите программу снова.")
            return

        if not REQUIREMENTS_FILE.exists():
            messagebox.showerror("Нет файла зависимостей", "В папке проекта отсутствует requirements.txt.")
            return

        self.aiogram_status.set("⏳ Установка aiogram...")
        self.root.update_idletasks()
        try:
            install = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)],
                capture_output=True,
                text=True,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except OSError as error:
            messagebox.showerror("Ошибка установки", str(error))
            self.check_dependencies()
            return

        self.check_dependencies()
        if install.returncode != 0 or not self.aiogram_ready:
            details = (install.stderr or install.stdout or "Неизвестная ошибка")[-1800:]
            messagebox.showerror("Не удалось установить aiogram", details)
            return
        messagebox.showinfo("Готово", "aiogram установлен.")

    def _get_settings(self) -> dict[str, str | int] | None:
        bot_token = self.entries["BOT_TOKEN"].get().strip()
        payment_token = self.entries["PAYMENT_TOKEN"].get().strip()
        admin_id_text = self.entries["ADMIN_ID"].get().strip()

        if not bot_token or not payment_token or not admin_id_text:
            messagebox.showerror("Не все поля заполнены", "Введите BOT_TOKEN, PAYMENT_TOKEN и ADMIN_ID.")
            return None
        try:
            admin_id = int(admin_id_text)
            if admin_id <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Неверный ADMIN_ID", "Telegram ID администратора должен быть положительным целым числом.")
            return None

        return {
            "BOT_TOKEN": bot_token,
            "PAYMENT_TOKEN": payment_token,
            "ADMIN_ID": admin_id,
        }

    def save_settings(self) -> bool:
        settings = self._get_settings()
        if settings is None:
            return False
        try:
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as config_file:
                    existing_settings = json.load(config_file)
                if not isinstance(existing_settings, dict):
                    raise ValueError("config.json имеет неверный формат.")
            except FileNotFoundError:
                existing_settings = {}

            existing_settings.update(settings)
            temporary_file = CONFIG_FILE.with_suffix(".tmp")
            with open(temporary_file, "w", encoding="utf-8") as config_file:
                json.dump(existing_settings, config_file, ensure_ascii=False, indent=2)
            temporary_file.replace(CONFIG_FILE)
        except (OSError, json.JSONDecodeError, ValueError) as error:
            messagebox.showerror("Ошибка сохранения", str(error))
            return False
        messagebox.showinfo("Настройки сохранены", "Данные сохранены в config.json.")
        return True

    def save_and_launch(self) -> None:
        if sys.version_info < MIN_PYTHON:
            messagebox.showerror("Нужен Python 3.10+", "Установите Python 3.10 или новее.")
            return
        if not self.aiogram_ready:
            if not messagebox.askyesno("Не установлен aiogram", "Установить aiogram сейчас?"):
                return
            self.install_aiogram()
            if not self.aiogram_ready:
                return
        if not BOT_FILE.exists():
            messagebox.showerror("Файл бота не найден", f"В папке проекта нет файла:\n{BOT_FILE.name}")
            return
        if not self.save_settings():
            return

        try:
            creationflags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
            subprocess.Popen([sys.executable, str(BOT_FILE)], cwd=PROJECT_DIR, creationflags=creationflags)
        except OSError as error:
            messagebox.showerror("Не удалось запустить бота", str(error))
            return
        messagebox.showinfo("Бот запущен", "Бот запущен в отдельном окне. Чтобы остановить его, закройте это окно.")


def main() -> None:
    root = tk.Tk()
    BotLauncher(root)
    root.mainloop()


if __name__ == "__main__":
    main()
