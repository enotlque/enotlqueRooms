import discord
from discord import app_commands, Interaction, Embed, utils, ButtonStyle
from discord import TextStyle
from discord.ext import tasks
import random
import discord.ui
from discord.ui import View, Button, Modal, TextInput
from discord import ui
from discord.utils import get
from datetime import datetime, timedelta
from functools import wraps
from typing import List, Tuple, Dict, Set
import re
import logging
import traceback
import asyncio
import time
import os
import io
from PIL import Image, ImageDraw, ImageFont
from cache import get_cached, set_cached, delete_cached, balance_cache_key, profile_cache_key, top_cache_key
from commands_profile import create_profile_image, get_active_role_names, get_member_room_options

from . import common
from .common import create_embed, format_timedelta, get_user_balance, subtract_user_balance


# ============================================
# ДУЭЛЬ В КОСТИ (/duel)
# ============================================

active_games: Dict[int, bool] = {}

DUEL_RAKE_PERCENT = 0.10  # 10% комиссия с банка при победе — защита от абуза переливом баланса


class DiceButton(Button):
    def __init__(self, ставка: int, author_id: int, author_name: str, target_user: discord.Member = None):
        super().__init__(label="Принять вызов", style=discord.ButtonStyle.primary)
        self.ставка = ставка
        self.author_id = author_id
        self.author_name = author_name
        self.target_user = target_user
        self.game_completed = False  # Флаг завершения игры
        self.lock = asyncio.Lock()   # Асинхронная блокировка для предотвращения гонок

    async def callback(self, interaction: discord.Interaction):
        cursor = common.cursor

        if time.time() > self.view.end_time:
            await self.view.on_timeout()
            return

        async with self.lock:
            if self.game_completed:
                await interaction.response.send_message("Игра уже завершена.", ephemeral=True)
                return

            await interaction.response.defer()

            if interaction.user.id == self.author_id:
                await interaction.followup.send("<:hausted:1303112530402480128> Вы не можете принять свой собственный вызов!", ephemeral=True)
                return

            if self.target_user and interaction.user.id != self.target_user.id:
                await interaction.followup.send("<:nerd:1295095424855834635> Этот вызов предназначен для другого игрока!", ephemeral=True)
                return

            result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', interaction.user.id)
            row = cursor.fetchone()
            challenger_balance = row[0] if row else None

            if challenger_balance is None or challenger_balance < self.ставка:
                await interaction.followup.send("У вас недостаточно монет для принятия вызова!", ephemeral=True)
                return

            await cursor.execute('UPDATE user_profiles SET balance = balance - $1 WHERE user_id = $2',
                                  self.ставка, interaction.user.id)

            # СОЗДАЕМ КАРТИНКУ ДУЭЛИ
            try:
                duel_image = self.create_duel_image(self.author_name, interaction.user.name)
                duel_file = discord.File(duel_image, filename="duel.png")
            except Exception as e:
                print(f"Ошибка создания картинки дуэли: {e}")
                duel_file = None

            author_roll1 = random.randint(1, 20)
            author_roll2 = random.randint(1, 20)
            author_total = author_roll1 + author_roll2

            challenger_roll1 = random.randint(1, 20)
            challenger_roll2 = random.randint(1, 20)
            challenger_total = challenger_roll1 + challenger_roll2

            result_embed = discord.Embed(color=int("6e6e6e", 16))
            result_embed.set_author(name=f"Брошен вызов в кости - {self.author_name}",
                                     icon_url=interaction.guild.get_member(self.author_id).avatar.url)

            # ДОБАВЛЯЕМ КАРТИНКУ В EMBED
            if duel_file:
                result_embed.set_image(url="attachment://duel.png")

            if author_total > challenger_total:
                winner_id = self.author_id
                winner_name = self.author_name
            elif challenger_total > author_total:
                winner_id = interaction.user.id
                winner_name = interaction.user.name
            else:
                # НИЧЬЯ — ставки возвращаются обоим
                await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2',
                                      self.ставка, self.author_id)
                await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2',
                                      self.ставка, interaction.user.id)

                result_note = discord.Embed(color=int("6e6e6e", 16))
                result_note.description = "<:shoked:1295095176548847717> Ничья! Ставки возвращены обоим игрокам."
                result_note.set_image(url="https://i.postimg.cc/jdv5cp6v/1111-1.png")

                view = View()
                button = DiceButton(self.ставка, self.author_id, self.author_name, self.target_user)
                button.disabled = True
                view.add_item(button)

                if duel_file:
                    await interaction.message.edit(embeds=[result_embed, result_note], view=view, attachments=[duel_file])
                else:
                    await interaction.message.edit(embeds=[result_embed, result_note], view=view)

                self.game_completed = True
                self.view.stop()
                return

            # Победителю зачисляется банк за вычетом комиссии (защита от абуза переливом баланса)
            pot = self.ставка * 2
            rake = int(pot * DUEL_RAKE_PERCENT)
            payout = pot - rake
            net_win = payout - self.ставка  # чистая прибыль победителя сверх возврата своей ставки

            await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2',
                                  payout, winner_id)

            result_note = discord.Embed(color=int("6e6e6e", 16))
            result_note.description = (f"<:winner:1299059106060959766> Победитель: **{winner_name}**\n"
                                        f"<a:coinonrole:1298391257042784266> Чистый выигрыш: **{net_win}**")
            result_note.set_footer(text=f"комиссия: {rake}")
            result_note.set_image(url="https://i.postimg.cc/jdv5cp6v/1111-1.png")

            view = View()
            button = DiceButton(self.ставка, self.author_id, self.author_name, self.target_user)
            button.disabled = True
            view.add_item(button)

            if duel_file:
                await interaction.message.edit(embeds=[result_embed, result_note], view=view, attachments=[duel_file])
            else:
                await interaction.message.edit(embeds=[result_embed, result_note], view=view)

            self.game_completed = True
            self.view.stop()

    def create_duel_image(self, author_name, opponent_name):
        # Открываем заготовленное изображение
        image_path = "duel-1.png"

        if not os.path.exists(image_path):
            raise FileNotFoundError("Файл изображения не найден по указанному пути.")

        image = Image.open(image_path)
        draw = ImageDraw.Draw(image)

        # Задаем параметры шрифта
        font_path = "Vito Wide Bold.ttf"
        font_size = 45

        if os.path.exists(font_path):
            font = ImageFont.truetype(font_path, font_size)
        else:
            raise FileNotFoundError("Файл шрифта не найден по указанному пути.")

        text_author = f"{author_name.lower()}"
        text_opponent = f"{opponent_name.lower()}"

        author_bbox = draw.textbbox((0, 0), text_author, font=font)
        opponent_bbox = draw.textbbox((0, 0), text_opponent, font=font)

        center_x = image.width // 2
        center_y = image.height // 2

        author_x = center_x - 650 - (author_bbox[2] - author_bbox[0]) // 2
        author_y = center_y - 300 - (author_bbox[3] - author_bbox[1]) // 2

        opponent_x = center_x + 650 - (opponent_bbox[2] - opponent_bbox[0]) // 2
        opponent_y = center_y - 300 - (opponent_bbox[3] - opponent_bbox[1]) // 2

        draw.text((author_x, author_y), text_author, font=font, fill=(255, 255, 255))
        draw.text((opponent_x, opponent_y), text_opponent, font=font, fill=(255, 255, 255))

        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format="PNG")
        img_byte_arr.seek(0)
        return img_byte_arr


class DiceView(View):
    def __init__(self, ставка: int, author_id: int, author_name: str, target_user: discord.Member = None):
        super().__init__(timeout=60)  # 60 секунд таймаут
        self.ставка = ставка
        self.author_id = author_id
        self.author_name = author_name
        self.target_user = target_user
        self.message = None
        self.end_time = time.time() + 60  # Фиксированное время истечения вызова
        self.add_item(DiceButton(ставка, author_id, author_name, target_user))

    async def on_timeout(self):
        cursor = common.cursor

        if not self.message:
            return

        # Ставка возвращается автору, если никто не принял вызов вовремя
        await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2',
                              self.ставка, self.author_id)

        view = View()
        button = DiceButton(self.ставка, self.author_id, self.author_name, self.target_user)
        button.disabled = True
        view.add_item(button)

        timeout_embed = discord.Embed(color=int("6e6e6e", 16))
        timeout_embed.set_author(name=f"Вызов в кости отменен - {self.author_name}",
                                  icon_url=self.message.guild.get_member(self.author_id).avatar.url)
        timeout_embed.add_field(name="Сумма вызова",
                                 value=f"**{self.ставка}** <a:coinonrole:1298391257042784266>",
                                 inline=False)

        await self.message.edit(embed=timeout_embed, view=view)
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if time.time() > self.end_time:
            await self.on_timeout()
            return False
        return True


@app_commands.command(name="duel", description="Бросить вызов на игру в кости на определенную ставку")
@app_commands.describe(
    ставка="Сумма вызова (от 30 до 2500 монет)",
    противник="Игрок, которому бросаете вызов (необязательно)"
)
async def duel(interaction: discord.Interaction, ставка: int, противник: discord.Member = None):
    cursor = common.cursor

    if interaction.user.id in active_games:
        await interaction.response.send_message("У вас уже есть активный вызов! Дождитесь его завершения.", ephemeral=True)
        return

    if противник and противник.id == interaction.user.id:
        await interaction.response.send_message("<:roblox:1295095451254657066> Вы не можете бросить вызов самому себе!", ephemeral=True)
        return

    if ставка < 30 or ставка > 2500:
        await interaction.response.send_message("Ставка должна быть от 30 до 2500 монет!", ephemeral=True)
        return

    result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', interaction.user.id)
    row = cursor.fetchone()
    balance_amount = row[0] if row else None

    if balance_amount is None or balance_amount < ставка:
        await interaction.response.send_message("У вас недостаточно монет для такой ставки!", ephemeral=True)
        return

    if противник:
        result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', противник.id)
        opponent_row = cursor.fetchone()
        opponent_balance = opponent_row[0] if opponent_row else None

        if opponent_balance is None or opponent_balance < ставка:
            error_embed = discord.Embed(
                description=f"У {противник.mention} недостаточно монет для принятия вызова!",
                color=int("6e6e6e", 16)
            )
            await interaction.response.send_message(embed=error_embed, ephemeral=True)
            return

    await cursor.execute('UPDATE user_profiles SET balance = balance - $1 WHERE user_id = $2',
                          ставка, interaction.user.id)

    embed = discord.Embed(color=int("6e6e6e", 16))
    embed.set_author(name=f"Брошен вызов в кости - {interaction.user.name}",
                      icon_url=interaction.user.avatar.url)

    if противник:
        embed.description = f"{interaction.user.mention} бросил вызов {противник.mention} на **{ставка}** <a:coinonrole:1298391257042784266>"
    else:
        embed.description = f"{interaction.user.mention} бросил вызов на **{ставка}** <a:coinonrole:1298391257042784266>"

    view = DiceView(ставка, interaction.user.id, interaction.user.name, противник)
    active_games[interaction.user.id] = True

    await interaction.response.send_message(embed=embed, view=view)
    view.message = await interaction.original_response()

    await view.wait()
    if interaction.user.id in active_games:
        del active_games[interaction.user.id]
