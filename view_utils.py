"""
Общий базовый класс для всех discord.ui.View в проекте.

Зачем: discord.py по умолчанию отправляет необработанные исключения из
кнопок/селектов/модалок (View.on_error) в системный logging-логгер
('discord.ui.view'), а не в print(). Весь остальной код проекта логирует
через print(), и на хостинге (Render) системный logging может никуда не
долетать — в итоге сбой кнопки выглядит как "ошибка ожидания" у
пользователя и полная тишина в логах, без единой зацепки для диагностики.

SafeView просто переопределяет on_error так, чтобы ошибка гарантированно
попадала в print() + traceback, и чтобы пользователь увидел вменяемое
сообщение вместо вечной надписи "Interaction failed" / зависшего "бот
думает".

Использование: заменить базовый класс View-класса с `discord.ui.View`
(или `View`, или `ui.View`) на `SafeView` — остальной код класса не
меняется, on_error наследуется автоматически. Если у класса уже есть
собственный on_error (как у TempRoomPanelView) — он просто переопределит
унаследованный, поведение не меняется.
"""

import traceback

import discord
from discord import Interaction
from discord.ui import View


class SafeView(View):
    async def on_error(self, interaction: Interaction, error: Exception, item) -> None:
        custom_id = getattr(item, "custom_id", None) or repr(item)
        user_id = interaction.user.id if interaction.user else "?"
        print(
            f"❌ [{self.__class__.__name__}] Необработанная ошибка в '{custom_id}' "
            f"(user={user_id}): {error!r}"
        )
        traceback.print_exception(type(error), error, error.__traceback__)

        embed = discord.Embed(
            description="Произошла ошибка, попробуйте ещё раз.",
            color=0xFF0000,
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception:
            # Интеракция уже мертва (протухший токен и т.п.) — тут уже ничего не поделать,
            # но сама ошибка выше в print() уже зафиксирована.
            pass
