"""
Модуль логирования: ежедневные файлы в папке logs,
сводка успешных записей в папку, автоудаление старше 30 дней.
Часовой пояс: Moscow.
"""
import os
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")
LOGS_DIR = "logs"
RETENTION_DAYS = 30


class LogManager:
    def __init__(self):
        self._current_date = None
        self._current_file_path = None
        self._success_count = 0
        self._unsuccess_count = 0
        self._lock = asyncio.Lock()
        os.makedirs(LOGS_DIR, exist_ok=True)

    def _get_moscow_now(self) -> datetime:
        return datetime.now(MOSCOW)

    def _get_log_path(self, dt: datetime) -> str:
        return os.path.join(LOGS_DIR, f"{dt.strftime('%Y-%m-%d')}.txt")

    async def _rotate_if_needed(self):
        """Проверяет смену дня и при необходимости пишет итог, переключает файл."""
        now = self._get_moscow_now()
        today = now.date()

        if self._current_date is None:
            self._current_date = today
            self._current_file_path = self._get_log_path(now)
            self._success_count = 0
            self._unsuccess_count = 0
            return

        if today != self._current_date:
            # Конец дня — пишем итог в старый файл
            if self._current_file_path:
                summary = (
                    f"{self._current_date} 23:59:59 [SUMMARY] "
                    f"=== ИТОГО ЗА ДЕНЬ: успешных записей в папку — {self._success_count}, "
                    f"неуспешных — {self._unsuccess_count} ===\n"
                )
                try:
                    with open(self._current_file_path, "a", encoding="utf-8") as f:
                        f.write(summary)
                except OSError:
                    pass

            self._current_date = today
            self._current_file_path = self._get_log_path(now)
            self._success_count = 0
            self._unsuccess_count = 0

    async def log(self, level: str, message: str):
        """Запись лога в файл."""
        async with self._lock:
            await self._rotate_if_needed()
            now = self._get_moscow_now()
            line = f"{now.strftime('%Y-%m-%d %H:%M:%S')} [{level}] {message}\n"
            try:
                with open(self._current_file_path, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError:
                pass

    async def log_successful_write(self):
        """Учитывает успешную запись в /srv/esia_obmen/."""
        async with self._lock:
            await self._rotate_if_needed()
            self._success_count += 1

    async def log_unsuccessful_write(self):
        """Учитывает неуспешную запись в /srv/esia_obmen/."""
        async with self._lock:
            await self._rotate_if_needed()
            self._unsuccess_count += 1

    def log_sync(self, level: str, message: str):
        """Синхронная запись (для вызовов вне async контекста, например SOAP)."""
        now = self._get_moscow_now()
        path = self._get_log_path(now)
        line = f"{now.strftime('%Y-%m-%d %H:%M:%S')} [{level}] {message}\n"
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass

    def cleanup_old_logs(self):
        """Удаляет файлы логов старше RETENTION_DAYS дней."""
        try:
            cutoff = (self._get_moscow_now() - timedelta(days=RETENTION_DAYS)).date()
            for name in os.listdir(LOGS_DIR):
                if not name.endswith(".txt"):
                    continue
                try:
                    file_date = datetime.strptime(name[:10], "%Y-%m-%d").date()
                    if file_date < cutoff:
                        path = os.path.join(LOGS_DIR, name)
                        os.remove(path)
                except (ValueError, OSError):
                    pass
        except OSError:
            pass

    async def _cleanup_task(self):
        """Фоновая задача: запуск cleanup каждый день в 01:00 по Москве."""
        while True:
            now = self._get_moscow_now()
            next_run = now.replace(hour=1, minute=0, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            seconds = (next_run - now).total_seconds()
            await asyncio.sleep(seconds)
            self.cleanup_old_logs()


# Глобальный экземпляр
log_manager = LogManager()


def setup_log_tasks(app):
    """Регистрирует фоновые задачи логирования для aiohttp."""
    async def on_startup(app):
        log_manager.cleanup_old_logs()  # очистка при старте
        asyncio.create_task(log_manager._cleanup_task())

    app.on_startup.append(on_startup)
