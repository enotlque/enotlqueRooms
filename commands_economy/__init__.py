"""
Пакет commands_economy — экономика бота, разложенная по темам вместо одного
файла на 3000+ строк.

Файлы:
    common.py    — общий cursor (устанавливается через set_cursor) и мелкие хелперы
    eco.py       — /eco balance|daily|work|purse|take|transfer
    top.py       — /top coin|role|hours
    profile.py   — /me (визуальный профиль)
    marriage.py  — /marry + автопродление/автораспад браков
    roles.py     — /role ... + /withrole + автопроверка истечения ролей
                   + чистка БД при ручном удалении роли на сервере
    slots.py     — /slots bet|info
    duel.py      — /duel

Здесь только реэкспорт — main.py как импортировал раньше
`import commands_economy` / `from commands_economy import (...)`,
так и продолжает импортировать без каких-либо изменений.
"""

from .common import set_cursor

from .eco import eco_group
from .top import top_group
from .profile import me
from .marriage import marry, start_marriage_expiry_task
from .roles import (
    role_group,
    withrole,
    start_role_expiry_task,
    setup_role_delete_listener,
    reconcile_deleted_roles,
)
from .slots import slots_group, set_slots_connection_factory
from .duel import duel

__all__ = [
    "set_cursor",
    "set_slots_connection_factory",
    "eco_group",
    "top_group",
    "me",
    "marry",
    "start_marriage_expiry_task",
    "role_group",
    "withrole",
    "start_role_expiry_task",
    "setup_role_delete_listener",
    "reconcile_deleted_roles",
    "slots_group",
    "duel",
]
