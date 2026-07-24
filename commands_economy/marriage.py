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
# MARRY COMMAND
# ============================================

@app_commands.command(name="marry", description="Сделать предложение пользователю")
async def marry(interaction: discord.Interaction, пользователь: discord.Member):
    cursor = common.cursor
    logging.basicConfig(level=logging.ERROR)
    logger = logging.getLogger(__name__)
    
    MALE_ROLE_ID = 1126893214536827050
    FEMALE_ROLE_ID = 1126893217405739090
    MARRIAGE_COST = 2500
    MARRIAGE_CATEGORY_ID = 1132300392215097365
    
    # Хранилище активных предложений (user_id -> сумма)
    active_proposals = getattr(marry, 'active_proposals', {})
    marry.active_proposals = active_proposals
    
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

    async def check_balance_and_profiles():
        # Проверяем активные предложения
        if interaction.user.id in active_proposals:
            await interaction.response.send_message(
                "У вас уже есть активное предложение! Дождитесь его завершения.",
                ephemeral=True
            )
            return False
        
        # Проверяем и создаём профиль отправителя если его нет
        result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', interaction.user.id)
        sender_row = cursor.fetchone()
        
        if not sender_row:
            await cursor.execute(
                'INSERT INTO user_profiles (user_id, balance, last_daily_claimed) VALUES ($1, $2, $3)',
                interaction.user.id, 0, None
            )
            await interaction.response.send_message(
                f"У вас недостаточно монет! Необходимо {MARRIAGE_COST} монет. У вас 0 монет.",
                ephemeral=True
            )
            return False
        
        if sender_row[0] < MARRIAGE_COST:
            await interaction.response.send_message(
                f"У вас недостаточно монет! Необходимо {MARRIAGE_COST} монет. У вас {sender_row[0]} монет.",
                ephemeral=True
            )
            return False
        
        # Проверяем и создаём профиль получателя если его нет
        result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', пользователь.id)
        receiver_row = cursor.fetchone()
        
        if not receiver_row:
            await cursor.execute(
                'INSERT INTO user_profiles (user_id, balance, last_daily_claimed) VALUES ($1, $2, $3)',
                пользователь.id, 0, None
            )
            logger.info(f"Создан профиль для пользователя {пользователь.id} при отправке предложения")
            
        return True

    async def check_marriage_status():
        try:
            await cursor.execute(
                'SELECT * FROM marriages WHERE user1_id = $1 OR user2_id = $1 OR user1_id = $2 OR user2_id = $2',
                interaction.user.id, пользователь.id
            )
            if cursor.fetchone():
                await interaction.response.send_message("Один из пользователей уже состоит в браке!", ephemeral=True)
                return False
            return True
        except Exception as e:
            logger.error(f"Error checking marriage status: {str(e)}\n{traceback.format_exc()}")
            await interaction.response.send_message("Произошла ошибка при проверке статуса брака.", ephemeral=True)
            return False

    class MarriageView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=60)
            self.message = None
            self.is_processed = False  # Флаг, чтобы избежать двойной обработки

        async def on_timeout(self):
            if self.is_processed:
                return
            self.is_processed = True
            
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
            
            # Возвращаем монеты отправителю
            try:
                # Проверяем, не был ли уже создан брак
                await cursor.execute(
                    'SELECT * FROM marriages WHERE user1_id = $1 OR user2_id = $1',
                    interaction.user.id
                )
                if not cursor.fetchone():
                    # Возвращаем монеты
                    await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2', 
                                        MARRIAGE_COST, interaction.user.id)
                    logger.info(f"Возвращены монеты пользователю {interaction.user.id} при таймауте")
                
                # Удаляем из активных предложений
                if interaction.user.id in active_proposals:
                    del active_proposals[interaction.user.id]
            except Exception as e:
                logger.error(f"Error returning coins on timeout: {e}")
            
            await self.message.edit(embed=embed, view=None)
            self.stop()

        @discord.ui.button(label="Принять", style=discord.ButtonStyle.green)
        async def accept(self, button_interaction: discord.Interaction, button: discord.ui.Button):
            cursor = common.cursor
            if self.is_processed:
                await button_interaction.response.send_message("Это предложение уже обработано!", ephemeral=True)
                return
                
            if button_interaction.user.id != пользователь.id:
                await button_interaction.response.send_message("Это не ваше предложение!", ephemeral=True)
                return

            # Блокируем кнопки
            self.is_processed = True
            for child in self.children:
                child.disabled = True
            await self.message.edit(view=self)

            try:
                # === КРИТИЧЕСКАЯ ПРОВЕРКА: баланс отправителя ===
                result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', interaction.user.id)
                sender_row = cursor.fetchone()
                
                # Проверяем, что у отправителя всё ещё есть достаточно монет
                # (с учётом того, что мы уже зарезервировали MARRIAGE_COST)
                if not sender_row or sender_row[0] < 0:
                    # Если баланс отрицательный или отсутствует - что-то пошло не так
                    await button_interaction.response.send_message(
                        "Ошибка: баланс отправителя повреждён. Обратитесь к администратору.",
                        ephemeral=True
                    )
                    # Возвращаем зарезервированные монеты
                    await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2', 
                                        MARRIAGE_COST, interaction.user.id)
                    if interaction.user.id in active_proposals:
                        del active_proposals[interaction.user.id]
                    self.stop()
                    return
                
                # Проверяем, не потратил ли отправитель зарезервированные монеты
                # Если баланс меньше 0 - значит он потратил больше, чем у него было
                if sender_row[0] < 0:
                    await button_interaction.response.send_message(
                        f"У отправителя недостаточно монет! Баланс: {sender_row[0]} монет. Необходимо: {MARRIAGE_COST} монет.",
                        ephemeral=True
                    )
                    # Возвращаем зарезервированные монеты
                    await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2', 
                                        MARRIAGE_COST, interaction.user.id)
                    if interaction.user.id in active_proposals:
                        del active_proposals[interaction.user.id]
                    self.stop()
                    return

                # Проверяем и создаём профиль получателя
                result = await cursor.execute('SELECT balance FROM user_profiles WHERE user_id = $1', пользователь.id)
                receiver_row = cursor.fetchone()
                
                if not receiver_row:
                    await cursor.execute(
                        'INSERT INTO user_profiles (user_id, balance, last_daily_claimed) VALUES ($1, $2, $3)',
                        пользователь.id, 0, None
                    )
                    logger.info(f"Создан профиль для пользователя {пользователь.id} при принятии предложения")

                # Проверяем брак повторно
                await cursor.execute(
                    'SELECT * FROM marriages WHERE user1_id = $1 OR user2_id = $1 OR user1_id = $2 OR user2_id = $2',
                    interaction.user.id, пользователь.id
                )
                if cursor.fetchone():
                    await button_interaction.response.send_message(
                        "Кто-то из пользователей уже успел вступить в брак!", 
                        ephemeral=True
                    )
                    # Возвращаем зарезервированные монеты
                    await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2', 
                                        MARRIAGE_COST, interaction.user.id)
                    if interaction.user.id in active_proposals:
                        del active_proposals[interaction.user.id]
                    self.stop()
                    return

                # === СОЗДАЁМ БРАК ===
                category = discord.utils.get(interaction.guild.categories, id=MARRIAGE_CATEGORY_ID)
                if category is None:
                    logger.error(f"Marriage category {MARRIAGE_CATEGORY_ID} not found")
                    await button_interaction.response.send_message(
                        "Категория для создания голосового канала не найдена.", 
                        ephemeral=True
                    )
                    await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2', 
                                        MARRIAGE_COST, interaction.user.id)
                    if interaction.user.id in active_proposals:
                        del active_proposals[interaction.user.id]
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

                # Монеты уже были списаны при отправке предложения, 
                # поэтому НЕ списываем их повторно!

                # Удаляем из активных предложений
                if interaction.user.id in active_proposals:
                    del active_proposals[interaction.user.id]

                success_embed = discord.Embed(
                    title="Брак успешно зарегистрирован!",
                    description=f"{interaction.user.mention} и {пользователь.mention} теперь **Возлюбленные**. Им предоставляется комната {voice_channel.mention}",
                    color=discord.Color.from_rgb(110, 110, 110)
                )
                await self.message.edit(embed=success_embed, view=None)
                
                await button_interaction.response.send_message(
                    "✅ Брак успешно заключён!", 
                    ephemeral=True
                )
                self.stop()

            except Exception as e:
                logger.error(f"Error in marriage creation: {str(e)}\n{traceback.format_exc()}")
                
                # Откат при ошибке
                try:
                    await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2', 
                                        MARRIAGE_COST, interaction.user.id)
                    if interaction.user.id in active_proposals:
                        del active_proposals[interaction.user.id]
                except Exception as rollback_error:
                    logger.error(f"Error rolling back: {rollback_error}")
                
                await button_interaction.response.send_message(
                    "Произошла ошибка при создании брака. Пожалуйста, попробуйте еще раз.",
                    ephemeral=True
                )
                self.stop()

        @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red)
        async def decline(self, button_interaction: discord.Interaction, button: discord.ui.Button):
            if self.is_processed:
                await button_interaction.response.send_message("Это предложение уже обработано!", ephemeral=True)
                return
                
            if button_interaction.user.id not in [пользователь.id, interaction.user.id]:
                await button_interaction.response.send_message("Это не ваше предложение!", ephemeral=True)
                return

            self.is_processed = True
            for child in self.children:
                child.disabled = True
            await self.message.edit(view=self)

            # Возвращаем зарезервированные монеты
            try:
                await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2', 
                                    MARRIAGE_COST, interaction.user.id)
                if interaction.user.id in active_proposals:
                    del active_proposals[interaction.user.id]
            except Exception as e:
                logger.error(f"Error returning coins on decline: {e}")

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
        if not await check_basic_conditions():
            return
            
        if not await check_balance_and_profiles():
            return
            
        if not await check_marriage_status():
            return

        # === РЕЗЕРВИРУЕМ МОНЕТЫ ===
        # Списываем их сразу, чтобы предотвратить трату; атомарная проверка
        # баланса встроена прямо в UPDATE, чтобы исключить гонку с другим
        # одновременным /marry от того же отправителя.
        await cursor.execute(
            'UPDATE user_profiles SET balance = balance - $1 WHERE user_id = $2 AND balance >= $1 RETURNING balance',
            MARRIAGE_COST, interaction.user.id
        )
        if cursor.fetchone() is None:
            await interaction.response.send_message(
                f"У вас недостаточно монет! Необходимо {MARRIAGE_COST} монет.",
                ephemeral=True
            )
            return
        
        # Добавляем в список активных предложений
        active_proposals[interaction.user.id] = MARRIAGE_COST

        embed = discord.Embed(
            description=f"Сделал предложение {пользователь.mention}\nДля принятия нажмите на кнопку ниже.",
            color=discord.Color.from_rgb(110, 110, 110)
        )
        embed.set_author(
            name=f"Предложение руки и сердца - {interaction.user.name}",
            icon_url=interaction.user.avatar.url if interaction.user.avatar else interaction.user.default_avatar.url
        )
        embed.set_footer(text="У вас есть 60 секунд на принятие решения")

        view = MarriageView()
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    except Exception as e:
        logger.error(f"Error in main relation flow: {str(e)}\n{traceback.format_exc()}")
        
        # Откат при любой ошибке
        try:
            await cursor.execute('UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2', 
                                MARRIAGE_COST, interaction.user.id)
            if interaction.user.id in active_proposals:
                del active_proposals[interaction.user.id]
        except Exception as rollback_error:
            logger.error(f"Error rolling back in main flow: {rollback_error}")
        
        await interaction.response.send_message("Произошла непредвиденная ошибка. Пожалуйста, попробуйте позже.", ephemeral=True)

# ============================================
# АВТОПРОДЛЕНИЕ / АВТОРАСТОРЖЕНИЕ БРАКОВ
# ============================================

MARRIAGE_RENEWAL_DAY_COST = 90
MARRIAGE_AUTO_RENEW_MAX_DAYS = 30

_marriage_task_started = False

def start_marriage_expiry_task(bot):
    global _marriage_task_started
    if _marriage_task_started:
        return
    _marriage_task_started = True

    @tasks.loop(hours=1)
    async def check_marriage_expirations():
        cursor = common.cursor
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
                continue

            affordable_days = (marriage_balance or 0) // MARRIAGE_RENEWAL_DAY_COST

            if affordable_days >= 1:
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
                        pass

    @check_marriage_expirations.before_loop
    async def before_check():
        await bot.wait_until_ready()

    check_marriage_expirations.start()
