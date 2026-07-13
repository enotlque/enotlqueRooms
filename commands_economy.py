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

# ============================================
# ГЛОБАЛЬНЫЙ CURSOR (передается из main.py)
# ============================================
cursor = None

def set_cursor(c):
    global cursor
    cursor = c
# ============================================

# ============================================
# HELPER FUNCTIONS
# ============================================

def create_embed(description: str, color: str = "#00FF00", footer=None, title=None, author_name=None, author_icon_url=None):
    embed = discord.Embed(description=description, color=int(color.lstrip("#"), 16))
    if footer:
        embed.set_footer(text=footer)
    if title:
        embed.title = title
    if author_name and author_icon_url:
        embed.set_author(name=author_name, icon_url=author_icon_url)
    return embed

def format_timedelta(td):
    days = td.days
    hours, remainder = divmod(td.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{days}д {hours:02d}ч {minutes:02d}м {seconds:02d}с"

async def get_user_balance(cursor, user_id):
    result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', user_id)
    row = cursor.fetchone()
    return row[0] if row else 0

async def subtract_user_balance(cursor, user_id, amount):
    result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', user_id)
    row = cursor.fetchone()
    if row:
        current_balance = row[0]
        if current_balance >= amount:
            new_balance = current_balance - amount
            await cursor.execute('UPDATE user_profiles SET balance = $1 WHERE user_id = $2', new_balance, user_id)
            return True
    return False

# ============================================
# ECO GROUP - Экономические команды
# ============================================

eco_group = app_commands.Group(name="eco", description="Экономические команды")

@eco_group.command(name="balance", description="Проверить баланс")
@app_commands.describe(пользователь="Проверить баланс пользователя")
async def balance(interaction: discord.Interaction, пользователь: discord.Member = None):
    пользователь = пользователь or interaction.user
    global cursor
    
    result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', пользователь.id)
    row = cursor.fetchone()
    
    if row:
        balance_amount = row[0]
    else:
        await cursor.execute('INSERT INTO user_profiles (user_id, balance, last_daily_claimed) VALUES ($1, $2, $3)', 
                           пользователь.id, 0, None)
        balance_amount = 0
    
    await interaction.response.send_message(embed=create_embed(
        description="",
        color="#696969",
        author_name=f"Баланс - {пользователь.display_name}",
        author_icon_url=пользователь.avatar.url
    ).add_field(name="Монет", value=f"```\n{balance_amount}\n```", inline=False))

@eco_group.command(name="daily", description="Получить ежедневный бонус (каждые 12 часов)")
async def daily(interaction: discord.Interaction):
    BONUS_AMOUNT = 30
    COOLDOWN_HOURS = 12
    global cursor

    result = await cursor.execute('SELECT last_daily_claimed, balance FROM user_profiles WHERE user_id = $1', interaction.user.id)
    row = cursor.fetchone()

    current_time = datetime.now()

    if row:
        last_daily_claimed, balance_amount = row
        if last_daily_claimed:
            last_daily_claimed = datetime.strptime(last_daily_claimed, "%Y-%m-%d %H:%M:%S")
            if current_time < last_daily_claimed + timedelta(hours=COOLDOWN_HOURS):
                next_claim_time = last_daily_claimed + timedelta(hours=COOLDOWN_HOURS)
                discord_time = f"<t:{int(next_claim_time.timestamp())}:R>"
                await interaction.response.send_message(embed=create_embed(
                    description=f"<:xx:1295095667617960018> Вы уже забрали бонус. В следующий раз можете его получить {discord_time}.",
                    color="#6e6e6e",
                    author_name=f"Бонус - {interaction.user.display_name}",
                    author_icon_url=interaction.user.avatar.url
                ))
                return

        new_balance = balance_amount + BONUS_AMOUNT
        await cursor.execute('UPDATE user_profiles SET balance = $1, last_daily_claimed = $2 WHERE user_id = $3', 
                           new_balance, current_time.strftime("%Y-%m-%d %H:%M:%S"), interaction.user.id)
        await interaction.response.send_message(embed=create_embed(
            description=f"Вы забрали: {BONUS_AMOUNT} <a:coinonrole:1298391257042784266>",
            color="#6e6e6e",
            author_name=f"Бонус - {interaction.user.display_name}",
            author_icon_url=interaction.user.avatar.url,
            footer="Возвращайтесь через 12 часов"
        ))
    else:
        await cursor.execute('INSERT INTO user_profiles (user_id, balance, last_daily_claimed) VALUES ($1, $2, $3)', 
                           interaction.user.id, BONUS_AMOUNT, current_time.strftime("%Y-%m-%d %H:%M:%S"))
        await interaction.response.send_message(embed=create_embed(
            description=f"Вы забрали: {BONUS_AMOUNT} <a:coinonrole:1298391257042784266>",
            color="#6e6e6e",
            author_name=f"Бонус - {interaction.user.display_name}",
            author_icon_url=interaction.user.avatar.url,
            footer="Возвращайтесь через 12 часов"
        ))

@eco_group.command(name="purse", description="Администраторская команда для выдачи средств пользователю")
@app_commands.describe(
    пользователь="Участник, которому вы хотите выдать средства",
    сумма="Сумма выдачи (от 1 до 100,000)"
)
async def purse(interaction: discord.Interaction, пользователь: discord.Member, сумма: int):
    global cursor
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(embed=create_embed(
            description="У вас недостаточно прав для выполнения этой команды.",
            color="#696969"
        ), ephemeral=True)
        return

    if сумма < 1 or сумма > 100000:
        await interaction.response.send_message(embed=create_embed(
            description="Сумма должна быть в пределах от 1 до 100,000 монет.",
            color="#696969"
        ), ephemeral=True)
        return

    result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', пользователь.id)
    recipient_balance = cursor.fetchone()

    if recipient_balance:
        await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2', сумма, пользователь.id)
    else:
        await cursor.execute('INSERT INTO user_profiles (user_id, balance) VALUES ($1, $2)', пользователь.id, сумма)

    await interaction.response.send_message(embed=create_embed(
        description=f"Вы успешно выдали {сумма} монет {пользователь.mention}.",
        color="#696969",
        author_name=f"Выдача монет - {interaction.user.display_name}",
        author_icon_url=interaction.user.avatar.url
    ), ephemeral=True)

@eco_group.command(name="take", description="Администраторская команда для изъятия средств у пользователя")
@app_commands.describe(
    пользователь="Участник, у которого вы хотите изъять средства",
    сумма="Сумма изъятия (от 1 до 100,000)"
)
async def take(interaction: discord.Interaction, пользователь: discord.Member, сумма: int):
    global cursor
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(embed=create_embed(
            description="У вас недостаточно прав для выполнения этой команды.",
            color="#696969"
        ), ephemeral=True)
        return

    if сумма < 1 or сумма > 100000:
        await interaction.response.send_message(embed=create_embed(
            description="Сумма должна быть в пределах от 1 до 100,000 монет.",
            color="#696969"
        ), ephemeral=True)
        return

    result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', пользователь.id)
    user_balance = cursor.fetchone()

    if not user_balance:
        await interaction.response.send_message(embed=create_embed(
            description="У пользователя нет профиля в системе.",
            color="#696969"
        ), ephemeral=True)
        return

    if user_balance[0] < сумма:
        await interaction.response.send_message(embed=create_embed(
            description=f"У пользователя недостаточно средств. Текущий баланс: {user_balance[0]} монет.",
            color="#696969"
        ), ephemeral=True)
        return

    await cursor.execute('UPDATE user_profiles SET balance = balance - $1 WHERE user_id = $2', сумма, пользователь.id)

    await interaction.response.send_message(embed=create_embed(
        description=f"Вы успешно изъяли {сумма} монет у {пользователь.mention}.",
        color="#696969",
        author_name=f"Изъятие монет - {interaction.user.display_name}",
        author_icon_url=interaction.user.avatar.url
    ), ephemeral=True)

@eco_group.command(name="transfer", description="Перевести средства другому пользователю")
@app_commands.describe(
    пользователь="Участник, которому вы хотите перевести средства",
    сумма="Сумма перевода (минимум 30 монет)"
)
async def transfer(interaction: discord.Interaction, пользователь: discord.Member, сумма: int):
    global cursor
    if сумма < 30:
        await interaction.response.send_message(embed=create_embed(
            description="Сумма перевода должна быть не менее 30 монет.",
            color="#696969"
        ), ephemeral=True)
        return

    if пользователь.id == interaction.user.id:
        await interaction.response.send_message(embed=create_embed(
            description="Невозможно перевести монеты самому себе.",
            color="#696969"
        ), ephemeral=True)
        return

    if сумма <= 0:
        await interaction.response.send_message(embed=create_embed(
            description="Сумма перевода должна быть больше 0.",
            color="#696969"
        ), ephemeral=True)
        return

    result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', interaction.user.id)
    sender_balance = cursor.fetchone()
    
    if sender_balance and sender_balance[0] >= сумма:
        commission = int(сумма * 0.1)
        total_amount = сумма - commission

        result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', пользователь.id)
        recipient_balance = cursor.fetchone()

        await cursor.execute('UPDATE user_profiles SET balance = balance - $1 WHERE user_id = $2', сумма, interaction.user.id)
        if recipient_balance:
            await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2', total_amount, пользователь.id)
        else:
            await cursor.execute('INSERT INTO user_profiles (user_id, balance) VALUES ($1, $2)', пользователь.id, total_amount)

        await interaction.response.send_message(embed=create_embed(
            description=f"Вы успешно перевели {сумма} монет {пользователь.mention}.",
            color="#696969",
            author_name=f"Перевод монет - {interaction.user.display_name}",
            author_icon_url=interaction.user.avatar.url
        ).set_footer(text="Комиссия 10%"))
    else:
        await interaction.response.send_message(embed=create_embed(
            description="Недостаточно средств для перевода.",
            color="#696969"
        ), ephemeral=True)

@eco_group.command(name="top", description="Показать топ пользователей по монетам")
async def top(interaction: discord.Interaction):
    global cursor
    result = await cursor.execute('SELECT user_id, balance FROM user_profiles ORDER BY balance DESC LIMIT 10')
    top_users = cursor.fetchall()

    if not top_users:
        await interaction.response.send_message(embed=Embed(description="Нет данных.", color=0x6e6e6e))
        return

    embed = Embed(color=0x6e6e6e)
    embed.set_author(name="Топ пользователей по монетам", icon_url=interaction.user.avatar.url if interaction.user.avatar else None)

    user_entries = []
    for index, (user_id, balance_amount) in enumerate(top_users, start=1):
        user = interaction.guild.get_member(user_id)
        if user:
            if index == 1:
                prefix = "<:w1:1337129208819875912>"
            elif index == 2:
                prefix = "<:w2:1337129278818750497>"
            elif index == 3:
                prefix = "<:w3:1337129254755762237>"
            else:
                prefix = f"**{index})**"
            user_entries.append(f"{prefix} {user.mention} - **{balance_amount}** <:wwaluta:1337129761956167751>")

    embed.add_field(name="", value="\n".join(user_entries), inline=False)
    await interaction.response.send_message(embed=embed)

# ============================================
# PROFILE COMMAND - /me
# ============================================

async def create_profile_embed(cursor, user, guild):
    result = await cursor.execute('SELECT balance, status, god_kissed FROM user_profiles WHERE user_id = $1', user.id)
    row = cursor.fetchone()

    if not row:
        await cursor.execute('INSERT INTO user_profiles (user_id, balance) VALUES ($1, $2)', user.id, 0)
        balance_amount, status, god_kissed = 0, "Статуса нет", "—"
    else:
        balance_amount, status, god_kissed = row

    embed = discord.Embed(color=0x6e6e6e, title="", description="")
    embed.add_field(name="Статус", value=f"```\n{status}\n```", inline=False)
    embed.add_field(name="Баланс", value=f"```{balance_amount}```", inline=True)

    days_on_server = "—"
    if isinstance(user, discord.Member) and user.joined_at:
        now = datetime.now(user.joined_at.tzinfo)
        days_on_server = str((now - user.joined_at).days)
    embed.add_field(name="Дней на сервере", value=f"```{days_on_server}```", inline=True)
    embed.add_field(name="Целованный богом", value=f"```{god_kissed or '—'}```", inline=True)

    result = await cursor.execute('SELECT user1_id, user2_id FROM marriages WHERE user1_id = $1 OR user2_id = $1', user.id)
    marriage_data = cursor.fetchone()
    if marriage_data:
        partner_id = marriage_data[0] if marriage_data[1] == user.id else marriage_data[1]
        partner = await guild.fetch_member(partner_id)
        embed.add_field(name="Возлюбленные", value=f"```{partner.display_name}```", inline=False)

    embed.set_author(name=f"Профиль - {user.display_name}", icon_url=user.avatar.url)
    return embed

class StatusModal(ui.Modal, title="Обновить статус"):
    status_input = ui.TextInput(label="Введите новый статус", min_length=2, max_length=40)

    async def on_submit(self, interaction: discord.Interaction):
        global cursor
        await cursor.execute('UPDATE user_profiles SET status = $1 WHERE user_id = $2', self.status_input.value, interaction.user.id)

        await interaction.response.send_message(embed=discord.Embed(
            description=f"Статус обновлен на: ```{self.status_input.value}```",
            color=0x6e6e6e
        ), ephemeral=True)

        profile_embed = await create_profile_embed(cursor, interaction.user, interaction.guild)
        await interaction.followup.edit_message(interaction.message.id, embed=profile_embed)

@app_commands.command(name="me", description="Показать профиль пользователя")
@app_commands.describe(пользователь="Участник, чей профиль вы хотите просмотреть")
async def me(interaction: discord.Interaction, пользователь: discord.Member = None):
    global cursor
    пользователь = пользователь or interaction.user
    embed = await create_profile_embed(cursor, пользователь, interaction.guild)

    view = ui.View()

    button_change_status = ui.Button(
        label="Изменить статус", 
        style=discord.ButtonStyle.secondary, 
        emoji="<:whitepen:1337134443902537769>",
        disabled=пользователь != interaction.user
    )

    async def change_status_callback(i: discord.Interaction):
        if i.user == пользователь:
            await i.response.send_modal(StatusModal())
            updated_embed = await create_profile_embed(cursor, пользователь, i.guild)
            await interaction.edit_original_response(embed=updated_embed, view=view)

    button_change_status.callback = change_status_callback
    view.add_item(button_change_status)

    result = await cursor.execute('SELECT * FROM marriages WHERE user1_id = $1 OR user2_id = $1', пользователь.id)
    has_marriage = cursor.fetchone() is not None

    button_marriage = ui.Button(
        label="Брачный профиль",
        style=discord.ButtonStyle.gray,
        emoji="<a:pinkpixelheart:1298391338223403008>",
        disabled=not has_marriage or пользователь != interaction.user
    )

    class ExtendMarriageModal(ui.Modal, title="Продление брака"):
        days = ui.TextInput(
            label="Количество дней (1 день - 60 монет)",
            style=discord.TextStyle.short,
            placeholder="Введите количество дней от 1 до 365"
        )

        async def on_submit(self, modal_interaction: discord.Interaction):
            global cursor
            try:
                days_to_extend = int(self.days.value)
                if days_to_extend < 1 or days_to_extend > 365:
                    await modal_interaction.response.send_message(
                        embed=discord.Embed(
                            color=0x6e6e6e,
                            description="Количество дней должно быть от 1 до 365."
                        ),
                        ephemeral=True
                    )
                    return
            except ValueError:
                await modal_interaction.response.send_message("Пожалуйста, введите число от 1 до 365", ephemeral=True)
                return

            result = await cursor.execute('SELECT expires_at, marriage_balance FROM marriages WHERE user1_id = $1 OR user2_id = $2', 
                         пользователь.id, пользователь.id)
            marriage_data = cursor.fetchone()
            
            if not marriage_data:
                await modal_interaction.response.send_message("Ошибка: брак не найден", ephemeral=True)
                return

            current_expiration = datetime.fromisoformat(marriage_data[0])
            marriage_balance = marriage_data[1]
            
            max_extend_date = datetime.now() + timedelta(days=365)
            new_expiration_date = min(current_expiration + timedelta(days=days_to_extend), max_extend_date)
            
            actual_days_extended = (new_expiration_date - current_expiration).days
            cost = actual_days_extended * 60

            if marriage_balance < cost:
                await modal_interaction.response.send_message(
                    embed=discord.Embed(
                        color=0x6e6e6e,
                        description=f"Недостаточно средств. Необходимо: {cost} монет, доступно: {marriage_balance} монет"
                    ),
                    ephemeral=True
                )
                return

            await cursor.execute(
                'UPDATE marriages SET expires_at = $1, marriage_balance = marriage_balance - $2 WHERE user1_id = $3 OR user2_id = $4',
                new_expiration_date.isoformat(), cost, пользователь.id, пользователь.id
            )

            updated_marriage_embed = await create_marriage_embed(cursor, modal_interaction, пользователь)
            await modal_interaction.response.edit_message(embed=updated_marriage_embed, view=create_marriage_view(cursor, пользователь))

    async def marriage_callback(i: discord.Interaction):
        global cursor
        if i.user != пользователь:
            await i.response.send_message(
                embed=discord.Embed(
                    description="Вы не имеете право взаимодействовать с данным сообщением",
                    color=0x6e6e6e
                ),
                ephemeral=True
            )
            return
        result = await cursor.execute('SELECT * FROM marriages WHERE user1_id = $1 OR user2_id = $1', пользователь.id)
        marriage_data = cursor.fetchone()
        
        if not marriage_data:
            await i.response.send_message(f"{пользователь.name} не состоит в браке!", ephemeral=True)
            return

        marriage_embed = await create_marriage_embed(cursor, i, пользователь)
        marriage_view = await create_marriage_view(cursor, пользователь, i)
        await i.response.edit_message(embed=marriage_embed, view=marriage_view)

    def format_duration(duration):
        years = duration.days // 365
        days = duration.days % 365
        hours = duration.seconds // 3600
        minutes = (duration.seconds % 3600) // 60
        seconds = duration.seconds % 60

        parts = []
        if years > 0:
            parts.append(f"{years}г")
        if days > 0:
            parts.append(f"{days}д")
        if hours > 0:
            parts.append(f"{hours}ч")
        if minutes > 0:
            parts.append(f"{minutes}м")
        if seconds > 0:
            parts.append(f"{seconds}с")
            
        if not parts:
            return "0с"
            
        return " ".join(parts)

    async def create_marriage_embed(cursor, interaction, user):
        result = await cursor.execute('SELECT * FROM marriages WHERE user1_id = $1 OR user2_id = $1', user.id)
        marriage_data = cursor.fetchone()
        
        partner_id = marriage_data[1] if marriage_data[1] != user.id else marriage_data[2]
        partner = await interaction.client.fetch_user(partner_id)
        created_at = datetime.fromisoformat(marriage_data[4])
        expires_at = datetime.fromisoformat(marriage_data[6])
        
        now = datetime.now()
        marriage_duration = now - created_at

        embed = discord.Embed(color=0x6e6e6e)
        embed.set_author(name=f"Любовный профиль - @{user.display_name}", icon_url=user.avatar.url)
        embed.add_field(name="Возлюбленные", value=f"```ㅤ{user.display_name}ㅤ ㅤ❤ㅤ ㅤ{partner.display_name}ㅤ```", inline=False)
        embed.add_field(name="Баланс", value=f"```{marriage_data[3]}```", inline=True)
        embed.add_field(name="Регистрация", value=f"```{created_at.strftime('%d-%m-%Y')}```", inline=True)
        embed.add_field(name="Действителен до", value=f"```{expires_at.strftime('%d-%m-%Y')}```", inline=True)
        embed.add_field(name="Времени вместе", value=f"```{format_duration(marriage_duration)}```", inline=False)
        
        return embed

    async def create_marriage_view(cursor, user, interaction):
        marriage_view = ui.View()
        
        button_add_balance = ui.Button(label="Пополнить баланс", style=discord.ButtonStyle.green, emoji="<a:co11nn:1301173474751938641>")
        async def add_balance_callback(i: discord.Interaction):
            global cursor
            if i.user != user:
                await i.response.send_message(
                    embed=discord.Embed(
                        description="Вы не имеете право взаимодействовать с данным сообщением",
                        color=0x6e6e6e
                    ),
                    ephemeral=True
                )
                return
            class AddBalanceModal(ui.Modal, title="Пополнить баланс"):
                amount = ui.TextInput(label="Сумма (мин. 60 монет)", min_length=2, max_length=10, required=True)

                async def on_submit(self, modal_interaction: discord.Interaction):
                    global cursor
                    try:
                        amount = int(self.amount.value)
                        if amount < 60:
                            await modal_interaction.response.send_message("Минимальная сумма пополнения — 60 монет.", ephemeral=True)
                            return

                        result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', user.id)
                        user_balance_row = cursor.fetchone()
                        user_balance = user_balance_row[0] if user_balance_row else 0

                        if user_balance < amount:
                            await modal_interaction.response.send_message(
                                embed=discord.Embed(
                                    color=0x6e6e6e,
                                    description="Недостаточно средств."
                                ),
                                ephemeral=True
                            )
                            return

                        await cursor.execute('UPDATE user_profiles SET balance = balance - $1 WHERE user_id = $2', amount, user.id)
                        await cursor.execute('UPDATE marriages SET marriage_balance = marriage_balance + $1 WHERE user1_id = $2 OR user2_id = $3', amount, user.id, user.id)

                        updated_marriage_embed = await create_marriage_embed(cursor, modal_interaction, user)
                        updated_view = await create_marriage_view(cursor, user, modal_interaction)
                        await modal_interaction.response.edit_message(embed=updated_marriage_embed, view=updated_view)
                        await modal_interaction.followup.send(
                            embed=discord.Embed(
                                color=0x6e6e6e,
                                description=f"Брачный баланс пополнен на {amount} монет."
                            ),
                            ephemeral=True
                        )

                    except ValueError:
                        await modal_interaction.response.send_message("Пожалуйста, введите корректное число.", ephemeral=True)

            await i.response.send_modal(AddBalanceModal())

        button_add_balance.callback = add_balance_callback
        marriage_view.add_item(button_add_balance)

        result = await cursor.execute('SELECT expires_at FROM marriages WHERE user1_id = $1 OR user2_id = $1', user.id)
        marriage_data = cursor.fetchone()
        if marriage_data:
            expires_at = datetime.fromisoformat(marriage_data[0])
            now = datetime.now()
            days_until_max = (now + timedelta(days=365) - expires_at).days
        else:
            days_until_max = 0
        
        button_extend = ui.Button(
            label="Продление", 
            style=discord.ButtonStyle.primary,
            disabled=days_until_max <= 0,
            emoji="<:heart:1295095623690878987>"
        )
        
        async def extend_callback(i: discord.Interaction):
            if i.user != user:
                await i.response.send_message(
                    embed=discord.Embed(
                        description="Вы не имеете право взаимодействовать с данным сообщением",
                        color=0x6e6e6e
                    ),
                    ephemeral=True
                )
                return
            await i.response.send_modal(ExtendMarriageModal())

        button_extend.callback = extend_callback
        marriage_view.add_item(button_extend)

        button_divorce = ui.Button(
            label="Развестись",
            style=discord.ButtonStyle.danger,
            emoji="<:xx:1295095667617960018>"
        )

        async def divorce_callback(i: discord.Interaction):
            global cursor
            if i.user != user:
                await i.response.send_message(
                    embed=discord.Embed(
                        description="Вы не имеете право взаимодействовать с данным сообщением",
                        color=0x6e6e6e
                    ),
                    ephemeral=True
                )
                return
            confirmation_embed = discord.Embed(
                color=0x6e6e6e,
                description="Вы уверены, что хотите развестись? Нажмите **__Подтвердить__**."
            )
            confirmation_embed.set_author(
                name=f"Решил развестись - {i.user.display_name}",
                icon_url=i.user.avatar.url
            )
            confirmation_embed.set_footer(text="Это сообщение будет доступно 25 секунд.")

            confirm_view = ui.View(timeout=25)
            confirm_button = ui.Button(
                label="Подтвердить",
                style=discord.ButtonStyle.danger
            )

            async def confirm_callback(confirm_i: discord.Interaction):
                if confirm_i.user != i.user:
                    await confirm_i.response.send_message(
                        embed=discord.Embed(
                            color=0x6e6e6e,
                            description="Только инициатор развода может подтвердить это действие."
                        ),
                        ephemeral=True
                    )
                    return

                result = await cursor.execute('SELECT voice_marry_id, user1_id, user2_id FROM marriages WHERE user1_id = $1 OR user2_id = $1', user.id)
                marriage_data = cursor.fetchone()

                if marriage_data:
                    voice_channel_id = marriage_data[0]
                    
                    if voice_channel_id:
                        try:
                            voice_channel = await i.guild.fetch_channel(voice_channel_id)
                            await voice_channel.delete()
                        except discord.NotFound:
                            pass
                        except Exception as e:
                            print(f"Error deleting voice channel: {e}")

                    await cursor.execute('DELETE FROM marriages WHERE user1_id = $1 OR user2_id = $1', user.id)

                    final_embed = discord.Embed(
                        color=0x6e6e6e,
                        description="Ваш брак был успешно расторгнут."
                    )
                    final_embed.set_author(
                        name=f"Решил развестись - {confirm_i.user.display_name}",
                        icon_url=confirm_i.user.avatar.url
                    )
                    final_embed.set_footer(text=":(")
                    await confirm_i.response.edit_message(embed=final_embed, view=None)
                else:
                    error_embed = discord.Embed(
                        color=0x6e6e6e,
                        description="Ошибка: брак не найден."
                    )
                    await confirm_i.response.edit_message(embed=error_embed, view=None)

            confirm_button.callback = confirm_callback
            confirm_view.add_item(confirm_button)

            async def on_timeout():
                timeout_embed = discord.Embed(
                    color=0x6e6e6e,
                    description="Время расторжения брака истекло, повторите действие."
                )
                timeout_embed.set_author(
                    name=f"Решил развестись - {i.user.display_name}",
                    icon_url=i.user.avatar.url
                )
                timeout_embed.set_footer(text="Это сообщение было доступно 25 секунд.")
                await i.message.edit(embed=timeout_embed, view=None)

            confirm_view.on_timeout = on_timeout
            await i.response.edit_message(embed=confirmation_embed, view=confirm_view)

        button_divorce.callback = divorce_callback
        marriage_view.add_item(button_divorce)

        button_back = ui.Button(label="Назад", style=discord.ButtonStyle.gray)
        async def back_callback(i: discord.Interaction):
            if i.user != user:
                await i.response.send_message(
                    embed=discord.Embed(
                        description="Вы не имеете право взаимодействовать с данным сообщением",
                        color=0x6e6e6e
                    ),
                    ephemeral=True
                )
                return
            updated_embed = await create_profile_embed(cursor, user, i.guild)
            await i.response.edit_message(embed=updated_embed, view=view)

        button_back.callback = back_callback
        marriage_view.add_item(button_back)
        
        return marriage_view

    button_marriage.callback = marriage_callback
    view.add_item(button_marriage)

    await interaction.response.send_message(embed=embed, view=view)

# ============================================
# MARRY COMMAND
# ============================================

@app_commands.command(name="marry", description="Сделать предложение пользователю")
async def marry(interaction: discord.Interaction, пользователь: discord.Member):
    global cursor
    logging.basicConfig(level=logging.ERROR)
    logger = logging.getLogger(__name__)
    
    MALE_ROLE_ID = 1126893214536827050
    FEMALE_ROLE_ID = 1126893217405739090
    MARRIAGE_COST = 500
    MARRIAGE_CATEGORY_ID = 1132300392215097365
    
    async def check_basic_conditions():
        if пользователь.id == interaction.user.id:
            await interaction.response.send_message("Вы не можете сделать предложение самому себе!", ephemeral=True)
            return False
            
        sender_roles = [role.id for role in interaction.user.roles]
        target_roles = [role.id for role in пользователь.roles]
        
        sender_gender_count = sum(1 for role_id in sender_roles if role_id in (MALE_ROLE_ID, FEMALE_ROLE_ID))
        target_gender_count = sum(1 for role_id in target_roles if role_id in (MALE_ROLE_ID, FEMALE_ROLE_ID))
        
        if sender_gender_count != 1 or target_gender_count != 1:
            await interaction.response.send_message("У каждого участника должна быть ровно одна гендерная роль!", ephemeral=True)
            return False
            
        sender_is_male = MALE_ROLE_ID in sender_roles
        target_is_male = MALE_ROLE_ID in target_roles
        if sender_is_male == target_is_male:
            await interaction.response.send_message("Ай-ай, нарушаем закон РФ)", ephemeral=True)
            return False
            
        return True

    async def check_marriage_conditions():
        try:
            result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', interaction.user.id)
            row = cursor.fetchone()
            if not row or row[0] < MARRIAGE_COST:
                await interaction.response.send_message(f"У вас недостаточно монет! Необходимо {MARRIAGE_COST} монет.", ephemeral=True)
                return False
                
            result = await cursor.execute('SELECT * FROM marriages WHERE user1_id = $1 OR user2_id = $1 OR user1_id = $2 OR user2_id = $2', 
                                         interaction.user.id, interaction.user.id, пользователь.id, пользователь.id)
            if cursor.fetchone():
                await interaction.response.send_message("Один из пользователей уже состоит в браке!", ephemeral=True)
                return False
                
            return True
        except Exception as e:
            logger.error(f"Error checking marriage conditions: {str(e)}\n{traceback.format_exc()}")
            await interaction.response.send_message("Произошла ошибка при проверке условий брака.", ephemeral=True)
            return False

    class MarriageView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=30)
            self.message = None

        async def on_timeout(self):
            sender_avatar = interaction.user.avatar.url if interaction.user.avatar else interaction.user.default_avatar.url
            sender_name = interaction.user.name
            
            embed = discord.Embed(
                description="Ваше предложение руки и сердца истекло.",
                color=discord.Color.from_rgb(110, 110, 110)
            )
            embed.set_author(
                name=f"Предложение руки и сердца - {sender_name}",
                icon_url=sender_avatar
            )
            
            await self.message.edit(embed=embed, view=None)

        @discord.ui.button(label="Принять", style=discord.ButtonStyle.green)
        async def accept(self, button_interaction: discord.Interaction, button: discord.ui.Button):
            global cursor
            if button_interaction.user.id != пользователь.id:
                await button_interaction.response.send_message("Это не ваше предложение!", ephemeral=True)
                return

            try:
                result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', interaction.user.id)
                row = cursor.fetchone()
                if not row or row[0] < MARRIAGE_COST:
                    await button_interaction.response.send_message(f"У отправителя недостаточно монет! Необходимо {MARRIAGE_COST} монет.", ephemeral=True)
                    self.stop()
                    return

                category = discord.utils.get(interaction.guild.categories, id=MARRIAGE_CATEGORY_ID)
                if category is None:
                    logger.error(f"Marriage category {MARRIAGE_CATEGORY_ID} not found")
                    await button_interaction.response.send_message("Категория для создания голосового канала не найдена.", ephemeral=True)
                    self.stop()
                    return

                voice_channel = await category.create_voice_channel(
                    name=f"☯ {interaction.user.display_name} & {пользователь.display_name}"
                )

                await voice_channel.set_permissions(interaction.guild.default_role, connect=False)
                await voice_channel.set_permissions(interaction.user, connect=True, speak=True)
                await voice_channel.set_permissions(пользователь, connect=True, speak=True)

                created_at = datetime.now().isoformat()
                expires_at = (datetime.now() + timedelta(days=30)).isoformat()

                await cursor.execute('''
                    INSERT INTO marriages (user1_id, user2_id, marriage_balance, created_at, renewed_at, expires_at, voice_marry_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                ''', interaction.user.id, пользователь.id, 0, created_at, created_at, expires_at, voice_channel.id)

                await cursor.execute('UPDATE user_profiles SET balance = balance - $1 WHERE user_id = $2', MARRIAGE_COST, interaction.user.id)

                success_embed = discord.Embed(
                    title="Брак успешно зарегистрирован!",
                    description=f"{interaction.user.mention} и {пользователь.mention} теперь **Возлюбленные**. Им предоставляется комната {voice_channel.mention}",
                    color=discord.Color.from_rgb(110, 110, 110)
                )
                await self.message.edit(embed=success_embed, view=None)
                self.stop()

            except Exception as e:
                logger.error(f"Error in marriage creation: {str(e)}\n{traceback.format_exc()}")
                error_message = "Произошла ошибка при создании брака. "
                if "unique constraint" in str(e).lower():
                    error_message += "Возможно, один из пользователей уже состоит в браке."
                else:
                    error_message += "Пожалуйста, попробуйте еще раз."
                
                await button_interaction.response.send_message(error_message, ephemeral=True)
                self.stop()

        @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red)
        async def decline(self, button_interaction: discord.Interaction, button: discord.ui.Button):
            if button_interaction.user.id not in [пользователь.id, interaction.user.id]:
                await button_interaction.response.send_message("Это не ваше предложение!", ephemeral=True)
                return

            if button_interaction.user.id == interaction.user.id:
                decline_embed = discord.Embed(
                    description=f"💔 {interaction.user.mention} отклонил собственное предложение руки и сердца {пользователь.mention}.",
                    color=discord.Color.from_rgb(110, 110, 110)
                )
            else:
                decline_embed = discord.Embed(
                    description=f"💔 {пользователь.mention} отклонил(а) предложение руки и сердца {interaction.user.mention}.",
                    color=discord.Color.from_rgb(110, 110, 110)
                )

            await self.message.edit(embed=decline_embed, view=None)
            self.stop()

    try:
        if not await check_basic_conditions() or not await check_marriage_conditions():
            return

        embed = discord.Embed(
            description=f"Сделал предложение {пользователь.mention}\nДля принятия нажмите на кнопку ниже.",
            color=discord.Color.from_rgb(110, 110, 110)
        )
        embed.set_author(
            name=f"Предложение руки и сердца - {interaction.user.name}",
            icon_url=interaction.user.avatar.url if interaction.user.avatar else interaction.user.default_avatar.url
        )

        view = MarriageView()
        response = await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    except Exception as e:
        logger.error(f"Error in main relation flow: {str(e)}\n{traceback.format_exc()}")
        await interaction.response.send_message("Произошла непредвиденная ошибка. Пожалуйста, попробуйте позже.", ephemeral=True)

# ============================================
# АВТОПРОДЛЕНИЕ / АВТОРАСТОРЖЕНИЕ БРАКОВ
# ============================================

MARRIAGE_RENEWAL_DAY_COST = 60      # Стоимость одного дня продления (совпадает с ExtendMarriageModal)
MARRIAGE_AUTO_RENEW_MAX_DAYS = 30   # Максимум дней, на которое продлеваем за один автоцикл

_marriage_task_started = False

def start_marriage_expiry_task(bot):
    """Запускает фоновую проверку истёкших браков. Безопасно вызывать повторно — стартует только один раз."""
    global _marriage_task_started
    if _marriage_task_started:
        return
    _marriage_task_started = True

    @tasks.loop(hours=1)
    async def check_marriage_expirations():
        global cursor
        try:
            await cursor.execute(
                'SELECT user1_id, user2_id, marriage_balance, expires_at, voice_marry_id FROM marriages'
            )
            rows = cursor.fetchall()
        except Exception as e:
            print(f"❌ Ошибка при чтении браков для автопроверки: {e}")
            return

        now = datetime.now()

        for row in rows:
            user1_id, user2_id, marriage_balance, expires_at_str, voice_channel_id = row

            try:
                expires_at = datetime.fromisoformat(expires_at_str)
            except (TypeError, ValueError):
                continue

            if expires_at > now:
                continue  # Брак ещё действителен

            affordable_days = (marriage_balance or 0) // MARRIAGE_RENEWAL_DAY_COST

            if affordable_days >= 1:
                # Хватает средств на общем балансе — продлеваем автоматически
                days_to_add = min(affordable_days, MARRIAGE_AUTO_RENEW_MAX_DAYS)
                cost = days_to_add * MARRIAGE_RENEWAL_DAY_COST
                new_expires_at = (expires_at + timedelta(days=days_to_add)).isoformat()

                try:
                    await cursor.execute(
                        'UPDATE marriages SET expires_at = $1, marriage_balance = marriage_balance - $2, renewed_at = $3 '
                        'WHERE user1_id = $4 AND user2_id = $5',
                        new_expires_at, cost, now.isoformat(), user1_id, user2_id
                    )
                    print(f"✅ Брак {user1_id}-{user2_id} автопродлён на {days_to_add} дн. (списано {cost} с общего баланса)")
                except Exception as e:
                    print(f"❌ Ошибка автопродления брака {user1_id}-{user2_id}: {e}")
                continue

            # Средств не хватает даже на 1 день — брак автоматически расторгается
            if voice_channel_id:
                channel = bot.get_channel(voice_channel_id)
                if channel:
                    try:
                        await channel.delete(reason="Автоматическое расторжение брака: закончился общий баланс")
                    except Exception as e:
                        print(f"❌ Не удалось удалить голосовой канал брака {user1_id}-{user2_id}: {e}")

            try:
                await cursor.execute('DELETE FROM marriages WHERE user1_id = $1 AND user2_id = $2', user1_id, user2_id)
                print(f"💔 Брак {user1_id}-{user2_id} автоматически расторгнут: не хватило средств на продление")
            except Exception as e:
                print(f"❌ Ошибка автоматического расторжения брака {user1_id}-{user2_id}: {e}")
                continue

            divorce_embed = discord.Embed(
                description="Ваш брак был автоматически расторгнут: на общем балансе не хватило средств для продления.",
                color=0x6e6e6e
            )
            for uid in (user1_id, user2_id):
                user_obj = bot.get_user(uid)
                if user_obj:
                    try:
                        await user_obj.send(embed=divorce_embed)
                    except Exception:
                        pass  # Пользователь закрыл личные сообщения — не критично

    @check_marriage_expirations.before_loop
    async def before_check():
        await bot.wait_until_ready()

    check_marriage_expirations.start()

# ============================================
# SLOTS GROUP
# ============================================

active_players: Set[int] = set()

SLOT_SYMBOLS = {
    "<:orangediamond:1295376833688113232>": (1, 50, 15),
    "<:slotiseven:1337178032430911488>": (3, 25, 8),
    "<:cherry128x:1337421942529065082>": (10, 10, 3),
    "<:lemon128x:1337421957431300146>": (20, 4, 1),
    "<:strawberry128x:1337421500898082817>": (35, 2, 0)
}

LEFT_ARROW = "<:rightarrow:1337396550204129330>"
RIGHT_ARROW = "<:leftarrow:1337396538619592744>"

slots_group = app_commands.Group(name="slots", description="Команды для игры в слоты")

async def generate_slot_display(symbols_matrix: List[List[str]]) -> str:
    display_lines = []
    padding = " ㅤㅤ "
    
    for i, row in enumerate(symbols_matrix):
        if i == 1:
            line = f"{LEFT_ARROW}ㅤ**|** {' **:** '.join(row)} **|**ㅤ{RIGHT_ARROW}"
        else:
            line = f"{padding}**|** {' **|** '.join(row)} **|**{padding}"
        display_lines.append(line)
    
    return "\n \n".join(display_lines)

async def animate_slots(interaction: discord.Interaction, embed: discord.Embed) -> List[str]:
    symbols, weights = zip(*[(sym, data[0]) for sym, data in SLOT_SYMBOLS.items()])
    
    sequence = []
    initial_rows = [random.choices(symbols, weights=weights, k=3) for _ in range(3)]
    sequence.extend(initial_rows)
    
    spin_rows = 3
    for _ in range(spin_rows):
        sequence.append(random.choices(symbols, weights=weights, k=3))
    
    final_middle_line = random.choices(symbols, weights=weights, k=3)
    
    post_final_rows = [random.choices(symbols, weights=weights, k=3) for _ in range(2)]
    sequence.extend(post_final_rows)
    
    frames = []
    for i in range(len(sequence) - 2):
        frame = sequence[i:i+3]
        frames.append(frame)
    
    for frame_idx, frame in enumerate(frames):
        embed.description = await generate_slot_display(frame)
        await interaction.edit_original_response(embed=embed)
        
        if frame_idx < len(frames) - 6:
            await asyncio.sleep(0.2)
        elif frame_idx < len(frames) - 3:
            await asyncio.sleep(0.4)
        else:
            await asyncio.sleep(0.7)
    
    return frames[-1][1]

async def calculate_winnings(final_slots: List[str], bet: int) -> Tuple[int, str, int]:
    symbol_counts = {}
    for symbol in final_slots:
        symbol_counts[symbol] = symbol_counts.get(symbol, 0) + 1

    max_count = max(symbol_counts.values())
    winning_symbol = next((s for s, cnt in symbol_counts.items() if cnt == max_count), None)
    
    if max_count >= 2:
        multiplier = SLOT_SYMBOLS[winning_symbol][1] if max_count == 3 else SLOT_SYMBOLS[winning_symbol][2]
        
        if multiplier == 0:
            return 0, winning_symbol, max_count
        
        if multiplier == 1:
            return bet, winning_symbol, max_count
        
        winnings = int(bet * multiplier)
        return winnings, winning_symbol, max_count
    
    return 0, None, 0

async def update_balance(cursor, user_id: int, amount: int):
    await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2', amount, user_id)

async def play_slot_machine(interaction: discord.Interaction, bet: int):
    global cursor
    try:
        embed = discord.Embed(color=int("6e6e6e", 16))
        embed.set_author(
            name=f"Слоты - {interaction.user.name}",
            icon_url=interaction.user.avatar.url
        )
        
        await interaction.response.send_message(embed=embed)
        final_slots = await animate_slots(interaction, embed)
        
        winnings, winning_symbol, symbol_count = await calculate_winnings(final_slots, bet)
        
        if winnings > 0:
            if winnings == bet:
                win_message = (
                    f"<:infor:1337141420305416252> Возврат ставки: **{winnings}** <:wwaluta:1337129761956167751>\n"
                    f"Комбинация: {winning_symbol} x{symbol_count}"
                )
            else:
                win_message = (
                    f"<:galochka:1337141373446651955> Выигрыш: **{winnings}** <:wwaluta:1337129761956167751>\n"
                    f"Комбинация: {winning_symbol} x{symbol_count}"
                )
            embed.add_field(name="Результат", value=win_message)
            
            await update_balance(cursor, interaction.user.id, winnings)
        else:
            embed.add_field(
                name="Результат",
                value="<:krestic:1337141359286550618> Вы проиграли ставку"
            )
        
        await interaction.edit_original_response(embed=embed)
    except Exception as e:
        print(f"Ошибка в слотах для пользователя {interaction.user.id}: {e}")
    finally:
        active_players.discard(interaction.user.id)

@slots_group.command(name="info", description="Показать информацию о игре в слоты")
async def slots_info(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Руководство по игре в слоты",
        color=int("6e6e6e", 16)
    )
    
    info = (
        "<:infor:1337141420305416252> **Основная информация**\n"
        "<:smalldotwhite:1337130077808230508> Минимальная ставка: **50** <:wwaluta:1337129761956167751>\n"
        "<:smalldotwhite:1337130077808230508> Максимальная ставка: **5000** <:wwaluta:1337129761956167751>\n"
        "<:smalldotwhite:1337130077808230508> Выигрыш рассчитывается по средней линии\n\n"
        "<:smska:1337141319394529280> **Символы и множители**\n"
        f"> Алмаз <:orangediamond:1295376833688113232>\n"
        "<:smalldotwhite:1337130077808230508> 3 в ряд: х50 от ставки\n"
        "<:smalldotwhite:1337130077808230508> 2 в ряд: х15 от ставки\n\n"
        f"> Семёрка <:slotiseven:1337178032430911488>\n"
        "<:smalldotwhite:1337130077808230508> 3 в ряд: х25 от ставки\n"
        "<:smalldotwhite:1337130077808230508> 2 в ряд: х8 от ставки\n\n"
        f"> Вишня <:cherry128x:1337421942529065082>\n"
        "<:smalldotwhite:1337130077808230508> 3 в ряд: х10 от ставки\n"
        "<:smalldotwhite:1337130077808230508> 2 в ряд: х3 от ставки\n\n"
        f"> Лимон <:lemon128x:1337421957431300146>\n"
        "<:smalldotwhite:1337130077808230508> 3 в ряд: х4 от ставки\n"
        "<:smalldotwhite:1337130077808230508> 2 в ряд: возврат ставки\n\n"
        f"> Клубника <:strawberry128x:1337421500898082817>\n"
        "<:smalldotwhite:1337130077808230508> 3 в ряд: х2 от ставки\n"
        "<:smalldotwhite:1337130077808230508> 2 в ряд: проигрыш\n\n"
        "<:controller:1337129028745826364> **Как играть**\n"
        "1. Используйте команду `/slots bet`\n"
        "2. Укажите сумму ставки\n"
        "3. Выигрыш определяется по комбинации символов в средней линии\n"
        "4. При выигрыше сумма автоматически зачисляется на баланс\n\n"
        "<:warning:1295095037734031472> **Важно**\n"
        "• Учитываются только символы в средней линии\n"
        "• Выигрышная комбинация считается слева направо\n"
        "• При проигрыше ставка не возвращается"
    )
    
    embed.description = info
    await interaction.response.send_message(embed=embed, ephemeral=True)

@slots_group.command(name="bet", description="Сыграть в слоты")
@app_commands.describe(ставка="Сумма ставки (от 50 до 5000 монет)")
async def slots_bet(interaction: discord.Interaction, ставка: int):
    global cursor
    if interaction.user.id in active_players:
        error_embed = discord.Embed(
            description="<:krestic:1337141359286550618> Вы уже играете! Дождитесь окончания текущей игры.",
            color=int("6e6e6e", 16)
        )
        await interaction.response.send_message(embed=error_embed, ephemeral=True)
        return

    if ставка < 50 or ставка > 5000:
        error_embed = discord.Embed(
            description="Ставка должна быть от 50 до 5000 монет!",
            color=int("6e6e6e", 16)
        )
        await interaction.response.send_message(embed=error_embed, ephemeral=True)
        return

    result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', interaction.user.id)
    balance_row = cursor.fetchone()
    
    if not balance_row or balance_row[0] < ставка:
        error_embed = discord.Embed(
            description="У вас недостаточно монет!",
            color=int("6e6e6e", 16)
        )
        await interaction.response.send_message(embed=error_embed, ephemeral=True)
        return

    active_players.add(interaction.user.id)

    await update_balance(cursor, interaction.user.id, -ставка)
    
    await play_slot_machine(interaction, ставка)

# ============================================
# ROLE GROUP
# ============================================

role_group = app_commands.Group(name="role", description="Управление ролями")

async def check_role_exists(guild, role_name):
    role = discord.utils.get(guild.roles, name=role_name)
    if role is None:
        await cursor.execute("DELETE FROM roles WHERE role_name = $1", role_name)
        return False
    return True

def role_existence_check(func):
    @wraps(func)
    async def wrapper(interaction: discord.Interaction, *args, **kwargs):
        guild = interaction.guild
        result = await cursor.execute("SELECT role_name FROM roles")
        roles = cursor.fetchall()
        for (role_name,) in roles:
            await check_role_exists(guild, role_name)
        return await func(interaction, *args, **kwargs)
    return wrapper

POSITION_UNDER_ROLE_ID = 1295482270567632949

@role_group.command(name="create", description="Создать новую роль и выдать её пользователю")
@app_commands.describe(
    название="Название роли",
    цвет="HEX-код роли (6 символов, например, 000FFF)"
)
async def create(interaction: discord.Interaction, название: str, цвет: str):
    global cursor
    await interaction.response.defer(ephemeral=True)

    if len(название) > 20:
        error_embed = discord.Embed(
            description="### <:xx:1295095667617960018> Максимальное количество символов в названии: `20`",
            color=discord.Color(int('6e6e6e', 16))
        )
        await interaction.followup.send(embed=error_embed, ephemeral=True)
        return

    result = await cursor.execute("SELECT COUNT(*) FROM roles WHERE id_owner_now = $1", interaction.user.id)
    role_count = cursor.fetchone()[0]
    if role_count >= 2:
        await interaction.followup.send("У вас уже есть максимальное количество ролей (2). Вы не можете создать новую роль.", ephemeral=True)
        return

    if not re.match(r'^[0-9A-Fa-f]{6}$', цвет):
        await interaction.followup.send("Неверный формат HEX-кода. Используйте 6 символов (0-9, A-F).", ephemeral=True)
        return

    result = await cursor.execute("SELECT role_name, hex_code FROM roles WHERE role_name = $1 AND hex_code = $2", название, f"#{цвет}")
    if cursor.fetchone():
        await interaction.followup.send("Роль с таким названием и цветом уже существует. Название и цвет не могут совпадать одновременно.", ephemeral=True)
        return

    result = await cursor.execute("SELECT balance FROM user_profiles WHERE user_id = $1", interaction.user.id)
    user_profile = cursor.fetchone()
    if not user_profile or user_profile[0] < 1000:
        await interaction.followup.send("У вас недостаточно средств для создания роли. Требуется 1000 монет.", ephemeral=True)
        return

    guild = interaction.guild
    
    reference_role = guild.get_role(POSITION_UNDER_ROLE_ID)
    if not reference_role:
        await interaction.followup.send("Не удалось найти роль для позиционирования.", ephemeral=True)
        return
        
    role = await guild.create_role(name=название, color=discord.Color(int(цвет, 16)))
    
    try:
        positions = {r: r.position for r in guild.roles}
        positions[role] = reference_role.position
        await guild.edit_role_positions(positions)
    except discord.Forbidden:
        await interaction.followup.send("У бота недостаточно прав для изменения позиции роли.", ephemeral=True)
        return
    except Exception as e:
        await interaction.followup.send(f"Произошла ошибка при установке позиции роли: {str(e)}", ephemeral=True)
        return

    await interaction.user.add_roles(role)

    await cursor.execute("UPDATE user_profiles SET balance = balance - 1000 WHERE user_id = $1", interaction.user.id)

    creation_date = datetime.now()
    expiration_date = creation_date + timedelta(days=30)

    creation_date_str = creation_date.strftime("%d.%m.%Y в %Hч %Mм %Sс")
    expiration_date_str = expiration_date.strftime("%d.%m.%Y в %Hч %Mм %Sс")

    await cursor.execute(""" 
        INSERT INTO roles (
            role_name, hex_code, owner_id, id_owner_now, creation_date, expiration_date, archived,
            extend_date, archivation_date, razarchive_date
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
    """, название, f"#{цвет}", interaction.user.id, interaction.user.id,
        creation_date_str, expiration_date_str, 0, None, None, None)

    embed = discord.Embed(
        description="Вы успешно приобрели роль на `30 д`",
        color=discord.Color(int('6e6e6e', 16))
    )
    
    embed.set_author(
        name=f"Создание роли - {interaction.user.name}",
        icon_url=interaction.user.avatar.url if interaction.user.avatar else interaction.user.default_avatar.url
    )
    
    embed.add_field(name="<:docs:1295378117875204198> Название", value=название, inline=True)
    embed.add_field(name="<:100:1296116859040698389> Цвет", value=f"#{цвет}", inline=True)
    embed.add_field(name="<:pinkdiamond:1295376800431476780> Роль", value=role.mention, inline=True)

    await interaction.followup.send(embed=embed, ephemeral=True)

@role_group.command(name="manage", description="Управление ролями")
@role_existence_check
async def manage(interaction: Interaction):
    global cursor
    user_id = interaction.user.id
    
    result = await cursor.execute("SELECT role_name, expiration_date, archived, remaining_time, id_owner_now FROM roles WHERE id_owner_now = $1", user_id)
    user_roles = cursor.fetchall()

    if not user_roles:
        await interaction.response.send_message("У вас нет ролей для управления.", ephemeral=True)
        return

    embed = Embed(color=0x6e6e6e)
    embed.set_author(name=interaction.user.name, icon_url=interaction.user.avatar.url if interaction.user.avatar else None)

    description = ""
    for index, (role_name, expiration_date, archived, remaining_time, id_owner_now) in enumerate(user_roles, start=1):
        role = get(interaction.guild.roles, name=role_name)
        if role:
            emoji = f"<:ww{index}:{'1337129170022432889' if index == 1 else '1337129132789862501'}>"
            if archived:
                description += f"{emoji} {role.mention} (архивирована) - **осталось:** `{remaining_time}`\n"
            else:
                try:
                    expiration = datetime.strptime(expiration_date, '%d.%m.%Y в %Hч %Mм %Sс')
                    remaining_time = expiration - datetime.now()
                    remaining_str = format_timedelta(remaining_time)
                    description += f"{emoji} {role.mention} - **осталось:** `{remaining_str}`\n"
                except Exception as e:
                    description += f"{emoji} {role.mention} - **Ошибка в дате истечения:** {str(e)}\n"

    embed.description = description or "Нет активных ролей."

    view = RoleSelectView(user_roles, interaction.user, interaction.guild)
    await interaction.response.send_message(embed=embed, view=view)

class RoleSelectView(View):
    def __init__(self, user_roles, user, guild):
        super().__init__()
        self.user_roles = user_roles
        self.user = user
        self.guild = guild

        emoji_ids = {
            1: "<:ww1:1337129170022432889>",
            2: "<:ww2:1337129132789862501>"
        }

        for index, (role_name, _, _, _, _) in enumerate(self.user_roles, start=1):
            if index <= 2:
                button = Button(emoji=emoji_ids[index], style=ButtonStyle.primary)
                button.callback = self.create_callback(role_name)
                self.add_item(button)

    def create_callback(self, role_name):
        async def callback(interaction: Interaction):
            global cursor
            if interaction.user.id != self.user.id:
                await interaction.response.send_message("У вас нет прав на это действие.", ephemeral=True)
                return

            result = await cursor.execute("SELECT * FROM roles WHERE role_name = $1", role_name)
            role_info = cursor.fetchone()
            
            if role_info:
                embed = self.create_role_embed(role_info)
                view = self.create_role_view(role_info, interaction.user.id)
                await interaction.response.edit_message(embed=embed, view=view)
            else:
                await interaction.response.send_message("Информация о роли не найдена.", ephemeral=True)

        return callback

    @staticmethod
    def create_role_embed(role_info):
        role_name, hex_code, owner_id, id_owner_now, creation_date, expiration_date, archived, extend_date, archivation_date, razarchive_date, numberofday, remaining_time, allcoinsend_on_role = role_info

        embed = Embed(title=role_name, color=int(hex_code[1:], 16))
        embed.add_field(name="<:biletik:1337141227329556692> Цвет", value=hex_code)
        embed.add_field(name="<:crownaa:1337141290068086825> Создатель", value=f"<@{owner_id}>")
        embed.add_field(name="<:gamepads:1337141435811762289> Текущий владелец", value=f"<@{id_owner_now}>")
        embed.add_field(name="<:data:1337141473162039337> Дата создания", value=creation_date)
        embed.add_field(name="Статус", value="Архивирована" if archived else "Активна")
        embed.add_field(name="<:watchw:1337130049123389500> Дата истечения", value=expiration_date if not archived else "Архивирована")
        embed.add_field(name="<:vremya:1337141252151447555> Оставшееся время", value=remaining_time if archived else "Роль активна")
        embed.add_field(name="<:infor:1337141420305416252> Дата продления", value=extend_date if extend_date else "Нет")
        embed.add_field(name="<:pause:1337141200334880838> Дата архивации", value=archivation_date if archivation_date else "Нет")
        embed.add_field(name="<:unpause:1337141212599025684> Дата разархивации", value=razarchive_date if razarchive_date else "Нет")
        embed.add_field(name="<a:coinonrole:1298391257042784266> Потрачено монет на роль", value=str(allcoinsend_on_role))
        embed.set_footer(text="Архивация 200 монет")

        return embed

    def create_role_view(self, role_info, user_id):
        view = View()
        archived = role_info[6]
        expiration_date = role_info[5]
        id_owner_now = role_info[3]

        archive_button = Button(label="Разархивация" if archived else "Архивация", style=ButtonStyle.secondary, emoji="<:listok:1337141447861993653>")
        archive_button.callback = self.archive_callback(role_info, user_id)
        view.add_item(archive_button)

        can_extend = False
        if not archived and expiration_date != '-':
            expiration = datetime.strptime(expiration_date, '%d.%m.%Y в %Hч %Mм %Sс')
            remaining_time = expiration - datetime.now()
            can_extend = remaining_time.days < 364

        extend_button = Button(label="Продление", style=ButtonStyle.secondary, emoji="<:beskone4:1337141486512242868>", disabled=archived or not can_extend)
        extend_button.callback = self.extend_callback(role_info[0], user_id)
        view.add_item(extend_button)

        back_button = Button(label="Назад", style=ButtonStyle.secondary)
        back_button.callback = self.back_callback
        view.add_item(back_button)

        return view

    def archive_callback(self, role_info, user_id):
        async def callback(interaction: Interaction):
            global cursor
            if interaction.user.id != user_id:
                await interaction.response.send_message("У вас нет прав на это действие.", ephemeral=True)
                return

            user_balance = await get_user_balance(cursor, user_id)
            if user_balance < 200:
                await interaction.response.send_message("У вас недостаточно монет для архивации роли.", ephemeral=True)
                return

            role_name, _, _, id_owner_now, _, expiration_date, archived, _, _, _, _, remaining_time, allcoinsend_on_role = role_info

            if archived:
                new_expiration_date = (datetime.now() + timedelta(
                    days=int(remaining_time.split('д')[0]),
                    hours=int(remaining_time.split('д')[1].split('ч')[0]),
                    minutes=int(remaining_time.split('ч')[1].split('м')[0]),
                    seconds=int(remaining_time.split('м')[1].split('с')[0])
                )).strftime("%d.%m.%Y в %Hч %Mм %Sс")
                await cursor.execute("UPDATE roles SET archived = 0, razarchive_date = $1, expiration_date = $2, remaining_time = NULL WHERE role_name = $3", 
                                   datetime.now().strftime("%d.%m.%Y в %Hч %Mм %Sс"), new_expiration_date, role_name)
                role = get(interaction.guild.roles, name=role_name)
                if role:
                    await interaction.user.add_roles(role)
                await interaction.response.send_message(f"Роль {role_name} разархивирована и выдана вам.", ephemeral=True)
            else:
                remaining_time_delta = datetime.strptime(expiration_date, "%d.%m.%Y в %Hч %Mм %Sс") - datetime.now()
                remaining_time = f"{remaining_time_delta.days}д {remaining_time_delta.seconds // 3600}ч {(remaining_time_delta.seconds % 3600) // 60}м {remaining_time_delta.seconds % 60}с"
                await cursor.execute("UPDATE roles SET archived = 1, archivation_date = $1, expiration_date = '-', remaining_time = $2, allcoinsend_on_role = allcoinsend_on_role + 200 WHERE role_name = $3",
                                   datetime.now().strftime("%d.%m.%Y в %Hч %Mм %Sс"), remaining_time, role_name)
                role = get(interaction.guild.roles, name=role_name)
                if role:
                    await interaction.user.remove_roles(role)
                await interaction.response.send_message(f"Роль {role_name} заархивирована и удалена у вас. Вычтено 200 монет.", ephemeral=True)
                await subtract_user_balance(cursor, user_id, 200)

            result = await cursor.execute("SELECT * FROM roles WHERE role_name = $1", role_name)
            updated_role_info = cursor.fetchone()

            updated_embed = self.create_role_embed(updated_role_info)
            updated_view = self.create_role_view(updated_role_info, user_id)

            await interaction.followup.edit_message(message_id=interaction.message.id, embed=updated_embed, view=updated_view)

        return callback

    def extend_callback(self, role_name, user_id):
        async def callback(interaction: Interaction):
            await interaction.response.send_modal(ExtendRoleModal(role_name, self, user_id))
        return callback

    async def back_callback(self, interaction: Interaction):
        global cursor
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("У вас нет прав на это действие.", ephemeral=True)
            return
        
        result = await cursor.execute("SELECT role_name, expiration_date, archived, remaining_time, id_owner_now FROM roles WHERE id_owner_now = $1", self.user.id)
        updated_user_roles = cursor.fetchall()

        embed = Embed(color=0x6e6e6e)
        embed.set_author(name=self.user.name, icon_url=self.user.avatar.url if self.user.avatar else None)

        description = ""
        for index, (role_name, expiration_date, archived, remaining_time, id_owner_now) in enumerate(updated_user_roles, start=1):
            role = get(self.guild.roles, name=role_name)
            if role:
                emoji = f"<:pixe{index}:{'1298675729864851520' if index == 1 else '1298675752690122853'}>"
                if archived:
                    description += f"{emoji} {role.mention} (архивирована) - **осталось:** {remaining_time}\n"
                else:
                    try:
                        expiration = datetime.strptime(expiration_date, '%d.%m.%Y в %Hч %Mм %Sс')
                        remaining_time = expiration - datetime.now()
                        remaining_str = format_timedelta(remaining_time)
                        description += f"{emoji} {role.mention} - **осталось:** {remaining_str}\n"
                    except Exception as e:
                        description += f"{emoji} {role.mention} - **Ошибка в дате истечения:** {str(e)}\n"

        embed.description = description or "Нет активных ролей."
        
        updated_view = RoleSelectView(updated_user_roles, self.user, self.guild)

        await interaction.response.edit_message(embed=embed, view=updated_view)

class ExtendRoleModal(Modal):
    def __init__(self, role_name, view, user_id):
        super().__init__(title="Продление роли")
        self.role_name = role_name
        self.view = view
        self.user_id = user_id

        self.add_item(TextInput(label="Количество дней (1 день 33 монет)", style=TextStyle.short, placeholder="Введите количество дней"))

    async def on_submit(self, interaction: Interaction):
        global cursor
        try:
            days_to_extend = int(self.children[0].value)
            if days_to_extend < 1 or days_to_extend > 365:
                await interaction.response.send_message("В модальном окне только цифры от 1 до 365", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("В модальном окне только цифры от 1 до 365", ephemeral=True)
            return

        result = await cursor.execute("SELECT expiration_date FROM roles WHERE role_name = $1", self.role_name)
        expiration_date = cursor.fetchone()[0]
        current_expiration = datetime.strptime(expiration_date, "%d.%m.%Y в %Hч %Mм %Sс")
        max_extend_date = datetime.now() + timedelta(days=365)
        new_expiration_date = min(current_expiration + timedelta(days=days_to_extend), max_extend_date)

        actual_days_extended = (new_expiration_date - current_expiration).days
        cost_of_extension = actual_days_extended * 33

        user_balance = await get_user_balance(cursor, self.user_id)
        if user_balance < cost_of_extension:
            await interaction.response.send_message(f"Недостаточно монет для продления роли. Необходимо {cost_of_extension} монет, а у вас {user_balance} монет.", ephemeral=True)
            return

        await cursor.execute("UPDATE roles SET expiration_date = $1, extend_date = $2, allcoinsend_on_role = allcoinsend_on_role + $3 WHERE role_name = $4", 
                           new_expiration_date.strftime("%d.%m.%Y в %Hч %Mм %Sс"), datetime.now().strftime("%d.%m.%Y в %Hч %Mм %Sс"), cost_of_extension, self.role_name)
        await subtract_user_balance(cursor, self.user_id, cost_of_extension)
        
        await interaction.response.send_message(f"Роль {self.role_name} успешно продлена на {actual_days_extended} дней за {cost_of_extension} монет!", ephemeral=True)

        result = await cursor.execute("SELECT * FROM roles WHERE role_name = $1", self.role_name)
        updated_role_info = cursor.fetchone()

        updated_embed = self.view.create_role_embed(updated_role_info)
        updated_view = self.view.create_role_view(updated_role_info, self.user_id)

        await interaction.followup.edit_message(message_id=interaction.message.id, embed=updated_embed, view=updated_view)

@role_group.command(name="inventory", description="Показать активные роли и дату их истечения")
@role_existence_check
async def inventory(interaction: discord.Interaction, пользователь: discord.User = None):
    global cursor
    if пользователь is None:
        пользователь = interaction.user

    result = await cursor.execute(''' 
        SELECT role_name, expiration_date, remaining_time, archived 
        FROM roles 
        WHERE id_owner_now = $1 
    ''', пользователь.id)
    roles = cursor.fetchall()

    if not roles:
        await interaction.response.send_message(f"{пользователь.display_name} не имеет активных ролей в базе данных.", ephemeral=True)
        return

    embed = discord.Embed(color=discord.Color.from_str('#6e6e6e'))
    embed.set_author(name=f"Активные роли - {пользователь.display_name}", icon_url=пользователь.avatar.url)

    role_list = ""
    warning_emoji = "<:warning:1295095037734031472>"

    for index, (role_name, expiration_date, remaining_time, archived) in enumerate(roles, start=1):
        role = discord.utils.get(interaction.guild.roles, name=role_name)
        if role is None:
            continue

        archived_status = " (архивирована)" if archived == 1 else ""

        if archived == 1:
            remaining_time_str = remaining_time if remaining_time else "Оставшееся время не указано"
            days_left = int(remaining_time_str.split('д')[0]) if remaining_time_str.split('д')[0].isdigit() else 0
        else:
            try:
                expiration = datetime.strptime(expiration_date, '%d.%m.%Y в %Hч %Mм %Sс')
            except ValueError as e:
                await interaction.response.send_message(f"Ошибка преобразования даты для роли {role_name}: {e}", ephemeral=True)
                return

            time_difference = expiration - datetime.now()

            if time_difference.total_seconds() <= 0:
                remaining_time_str = "Роль истекла"
                days_left = 0
            else:
                days = time_difference.days
                hours, remainder = divmod(time_difference.seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                remaining_time_str = f"{days}д {hours:02}ч {minutes:02}м {seconds:02}с"
                days_left = days

        warning = warning_emoji if days_left < 3 else ""
        role_list += f"**{index})** {role.mention}{archived_status} - **осталось:** `{remaining_time_str}`{warning}\n"

    embed.description = role_list if role_list else "Нет активных ролей."
    await interaction.response.send_message(embed=embed)

@role_group.command(name="info", description="Получить информацию о роли")
@app_commands.describe(роль="Упомяните роль для проверки информации")
@role_existence_check
async def info(interaction: discord.Interaction, роль: discord.Role):
    global cursor
    result = await cursor.execute("""
        SELECT role_name, hex_code, owner_id, creation_date, expiration_date, archived, extend_date, archivation_date, razarchive_date, allcoinsend_on_role, remaining_time
        FROM roles
        WHERE role_name = $1
    """, роль.name)
    result_row = cursor.fetchone()

    if result_row:
        role_name, hex_code, owner_id, creation_date, expiration_date, archived, extend_date, archivation_date, razarchive_date, allcoinsend_on_role, remaining_time = result_row
        owner = interaction.guild.get_member(owner_id)

        def format_date(date_str):
            if date_str and date_str != '-':
                return datetime.strptime(date_str, '%d.%m.%Y в %Hч %Mм %Sс').strftime("%d.%m.%Y в %Hч %Mм %Sс")
            return "Не установлена"

        embed = discord.Embed(color=int(hex_code.lstrip("#"), 16))
        embed.set_author(name=f"Информация о роли - {interaction.user.display_name}", icon_url=interaction.user.avatar.url)
        
        embed.add_field(name="<:4elovekww:1337141385530445886> Роль", value=роль.mention, inline=True)
        embed.add_field(name="<:biletik:1337141227329556692> Цвет (HEX)", value=f"*{hex_code}*", inline=True)
        embed.add_field(name="<a:SmokingAstronaut:1298391242706522224> Владелец", value=owner.mention if owner else "Неизвестен", inline=True)
        embed.add_field(name="<:data:1337141473162039337> Создание", value=f"*{format_date(creation_date)}*", inline=True)
        embed.add_field(name="<:beskone4:1337141486512242868> Продление", value=f"*{format_date(extend_date)}*", inline=True)
        embed.add_field(name="<a:coinonrole:1298391257042784266> Потрачено на роль", value=f"*{allcoinsend_on_role}*", inline=True)

        await interaction.response.send_message(embed=embed)
    else:
        await interaction.response.send_message("Эта роль не найдена в базе данных.", ephemeral=True)

class RoleTransfer(discord.ui.View):
    def __init__(self, bot, interaction, роль, sender, пользователь, сумма, timeout=30):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.interaction = interaction
        self.role = роль
        self.sender = sender
        self.receiver = пользователь
        self.amount = сумма
        self.accepted = False
        self.message = None

    @discord.ui.button(label="Получить", style=discord.ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        global cursor
        if interaction.user != self.receiver:
            await interaction.response.send_message("Вы не можете принять это предложение.", ephemeral=True)
            return
        
        self.accepted = True
        
        for item in self.children:
            item.disabled = True
        
        embed = discord.Embed(
            description=f"Роль {self.role.mention} успешно передана пользователю {self.receiver.mention}",
            color=discord.Color.from_str('#6e6e6e'),
            timestamp=discord.utils.utcnow()
        )
        embed.set_author(name=f"Трансфер роли - {self.sender.name}", icon_url=self.sender.avatar.url if self.sender.avatar else None)
        
        embed.add_field(name="Получатель", value=self.receiver.mention, inline=True)
        embed.add_field(name="Роль", value=self.role.mention, inline=True)
        embed.add_field(name="Сумма", value=f"{self.amount} монет" if self.amount > 0 else "Бесплатно", inline=True)
        
        await interaction.response.edit_message(embed=embed, view=self)
        
        await self.transfer_role()
        self.stop()

    @discord.ui.button(label="Отказаться", style=discord.ButtonStyle.red)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        global cursor
        if interaction.user != self.receiver and interaction.user != self.sender:
            await interaction.response.send_message("Вы не можете отменить это предложение.", ephemeral=True)
            return
        
        for item in self.children:
            item.disabled = True
        
        embed = discord.Embed(
            description=f"{interaction.user.mention} нажал <:xx:1295095667617960018> отказать.",
            color=discord.Color.from_str('#6e6e6e'),
            timestamp=discord.utils.utcnow()
        )
        embed.set_author(name=f"Трансфер роли - {self.sender.name}", icon_url=self.sender.avatar.url if self.sender.avatar else None)
        
        embed.add_field(name="Получатель", value=self.receiver.mention, inline=True)
        embed.add_field(name="Роль", value=self.role.mention, inline=True)
        embed.add_field(name="Сумма", value=f"{self.amount} <a:coinonrole:1298391257042784266>" if self.amount > 0 else "Бесплатно", inline=True)
        
        await interaction.response.edit_message(embed=embed, view=self)
        
        await self.return_role()
        self.stop()

    async def on_timeout(self):
        global cursor
        if not self.accepted:
            embed = discord.Embed(
                description="<:sleeping:1295095518145282058> Роль возвращена отправителю.",
                color=discord.Color.from_str('#6e6e6e'),
                timestamp=discord.utils.utcnow()
            )
            embed.set_author(name=f"Трансфер роли - {self.sender.name}", icon_url=self.sender.avatar.url if self.sender.avatar else None)
            
            embed.add_field(name="Получатель", value=self.receiver.mention, inline=True)
            embed.add_field(name="Роль", value=self.role.mention, inline=True)
            embed.add_field(name="Сумма", value=f"{self.amount} <a:coinonrole:1298391257042784266>" if self.amount > 0 else "Бесплатно", inline=True)
            
            for item in self.children:
                item.disabled = True
            
            try:
                original_message = await self.interaction.original_response()
                await original_message.edit(embed=embed, view=self)
            except discord.NotFound:
                pass
            
            await self.return_role()

    async def transfer_role(self):
        global cursor
        await cursor.execute("UPDATE roles SET id_owner_now = $1 WHERE role_name = $2", self.receiver.id, self.role.name)
        
        await self.receiver.add_roles(self.role)
        
        if self.amount > 0:
            await cursor.execute("UPDATE user_profiles SET balance = balance - $1 WHERE user_id = $2", self.amount, self.receiver.id)
            await cursor.execute("UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2", self.amount, self.sender.id)

    async def return_role(self):
        global cursor
        await cursor.execute("UPDATE roles SET id_owner_now = $1 WHERE role_name = $2", self.sender.id, self.role.name)
        
        await self.sender.add_roles(self.role)

@role_group.command(name="give", description="Передать роль другому пользователю")
@app_commands.describe(
    роль="Роль, которую вы хотите передать",
    пользователь="Пользователь, которому вы хотите передать роль",
    сумма="Сумма, которую вы хотите получить за роль (необязательно)"
)
@role_existence_check
async def give(interaction: discord.Interaction, роль: discord.Role, пользователь: discord.Member, сумма: int = 0):
    global cursor
    sender = interaction.user

    result = await cursor.execute("SELECT id_owner_now FROM roles WHERE role_name = $1 AND id_owner_now = $2", роль.name, sender.id)
    if not cursor.fetchone():
        await interaction.response.send_message("Вы не являетесь владельцем этой роли.", ephemeral=True)
        return

    if пользователь == sender:
        await interaction.response.send_message("Вы не можете передать роль самому себе.", ephemeral=True)
        return

    result = await cursor.execute("SELECT COUNT(*) FROM roles WHERE id_owner_now = $1", пользователь.id)
    role_count = cursor.fetchone()[0]
    if role_count >= 2:
        await interaction.response.send_message("У получателя уже есть 2 роли. Передача <:xx:1295095667617960018> невозможна.", ephemeral=True)
        return

    if сумма > 0:
        result = await cursor.execute("SELECT balance FROM user_profiles WHERE user_id = $1", пользователь.id)
        receiver_balance = cursor.fetchone()
        if not receiver_balance or receiver_balance[0] < сумма:
            await interaction.response.send_message("У получателя недостаточно средств для оплаты роли.", ephemeral=True)
            return

    await cursor.execute("UPDATE roles SET id_owner_now = -1 WHERE role_name = $1", роль.name)

    await sender.remove_roles(роль)

    embed = discord.Embed(
        description="<:clock:1298744322661023856> Ожидание подтверждения передачи роли",
        color=discord.Color.from_str('#6e6e6e'),
        timestamp=interaction.created_at
    )
    embed.set_author(name=f"Трансфер роли - {sender.name}", icon_url=sender.avatar.url if sender.avatar else None)

    embed.add_field(name="Получатель", value=пользователь.mention, inline=True)
    embed.add_field(name="Роль", value=роль.mention, inline=True)
    embed.add_field(name="Сумма", value=f"{сумма} <a:coinonrole:1298391257042784266>" if сумма > 0 else "Бесплатно", inline=True)
    embed.set_footer(text="У получателя есть 30 секунд на принятие роли")

    view = RoleTransfer(interaction.client, interaction, роль, sender, пользователь, сумма)
    await interaction.response.send_message(embed=embed, view=view)

    await view.wait()

# ============================================
# WITHROLE COMMAND
# ============================================

@app_commands.command(name="withrole", description="Показать список пользователей с указанной ролью")
@app_commands.describe(роль="Роль, для которой нужно показать список пользователей")
async def withrole(interaction: discord.Interaction, роль: discord.Role):
    members_with_role = [member for member in interaction.guild.members if роль in member.roles]
    
    if not members_with_role:
        await interaction.response.send_message(
            embed=discord.Embed(
                description="В данной роли нет пользователей.",
                color=0x6e6e6e
            )
        )
        return

    await list_role_members_update(interaction, роль, 0, members_with_role)

async def list_role_members_update(interaction: discord.Interaction, роль: discord.Role, offset: int, members: list):
    members_per_page = 20
    members_to_display = members[offset:offset + members_per_page]

    embed = discord.Embed(
        title=f"Пользователи с ролью {роль.name}",
        color=0x6e6e6e
    )

    member_list = []
    for index, member in enumerate(members_to_display, start=offset + 1):
        member_list.append(f"**{index}.** {member.mention}")

    embed.description = "\n".join(member_list)
    total_pages = (len(members) + members_per_page - 1) // members_per_page
    embed.set_footer(text=f"Страница {offset // members_per_page + 1}/{total_pages}")

    view = RoleMemberView(offset, len(members), interaction, роль, members)

    if isinstance(interaction, discord.Interaction):
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view)
    else:
        await interaction.edit(embed=embed, view=view)

class RoleMemberView(ui.View):
    def __init__(self, offset: int, total_items: int, interaction: discord.Interaction, роль: discord.Role, members: list):
        super().__init__()
        self.offset = offset
        self.total_items = total_items
        self.items_per_page = 20
        self.interaction = interaction
        self.роль = роль
        self.members = members
        self.update_buttons()

    def update_buttons(self):
        self.clear_items()
        self.add_button("Назад", self.go_back, discord.ButtonStyle.primary, disabled=(self.offset == 0))
        self.add_button("Информация о роли", self.show_role_info, discord.ButtonStyle.secondary)
        self.add_button("Вперед", self.go_forward, discord.ButtonStyle.primary, 
                       disabled=(self.offset + self.items_per_page >= self.total_items))

    def add_button(self, label: str, callback, style: discord.ButtonStyle, disabled: bool = False):
        button = discord.ui.Button(label=label, style=style, disabled=disabled)
        button.callback = callback
        self.add_item(button)

    async def go_back(self, interaction: discord.Interaction):
        if interaction.user.id != self.interaction.user.id:
            await interaction.response.send_message("У вас нет прав на это действие.", ephemeral=True)
            return

        new_offset = max(self.offset - self.items_per_page, 0)
        await list_role_members_update(interaction.message, self.роль, new_offset, self.members)
        await interaction.response.defer()

    async def go_forward(self, interaction: discord.Interaction):
        if interaction.user.id != self.interaction.user.id:
            await interaction.response.send_message("У вас нет прав на это действие.", ephemeral=True)
            return

        new_offset = min(self.offset + self.items_per_page, self.total_items - self.items_per_page)
        await list_role_members_update(interaction.message, self.роль, new_offset, self.members)
        await interaction.response.defer()

    async def show_role_info(self, interaction: discord.Interaction):
        if interaction.user.id != self.interaction.user.id:
            await interaction.response.send_message("У вас нет прав на это действие.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"Информация о роли {self.роль.name}",
            description=( 
                f"**Роль:** {self.роль.mention}\n"
                f"**Носителей:** {len([member for member in interaction.guild.members if self.роль in member.roles])}\n"
                f"**ID роли:** {self.роль.id}\n"
                f"**Цвет роли:** {self.роль.color}"
            ),
            color=0x6e6e6e
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
