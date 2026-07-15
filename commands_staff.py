import discord
from discord import app_commands, Interaction, Embed
from datetime import datetime
import asyncio

# === КОНСТАНТЫ ===
BANNED_ROLE_ID = 1129742835487358989      # роль, выдаваемая при /staff ban
AMNESTY_ROLE_ID = 1129103624870563950     # отслеживаемая роль (14 дней / период амнистии)
MODERATOR_ROLE_ID = 1126902187675627552   # роль модератора
LOG_CHANNEL_ID = 1526748291830775961      # канал для логов модерации

DIVIDER_IMAGE = "https://i.postimg.cc/jdv5cp6v/1111-1.png"
EMBED_COLOR = 0x6e6e6e


def is_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.guild_permissions.administrator


def is_staff(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    return any(role.id == MODERATOR_ROLE_ID for role in interaction.user.roles)


def setup_staff_commands(bot, cursor):
    staff_group = app_commands.Group(name="staff", description="Инструменты модерации сервера")

    # ==================== helpers ====================

    async def get_log_channel(guild: discord.Guild):
        return guild.get_channel(LOG_CHANNEL_ID)

    def base_log_embed(target: discord.abc.User, title: str, moderator: discord.abc.User | None = None) -> Embed:
        embed = Embed(color=EMBED_COLOR)
        embed.set_author(name=title, icon_url=target.display_avatar.url)
        embed.add_field(
            name="<:people:1526013751457874033> Участник",
            value=f"{target.mention}\n`{target.name}` • `{target.id}`",
            inline=True
        )
        if moderator is not None:
            embed.add_field(
                name="<:othericonw:1337130091142058064> Модератор",
                value=moderator.mention,
                inline=True
            )
        embed.set_footer(text=datetime.now().strftime('%d.%m.%Y %H:%M'))
        embed.set_image(url=DIVIDER_IMAGE)
        return embed

    # ==================== /staff ban ====================

    @staff_group.command(name="ban", description="Заблокировать участника [Администрация / Модерация]")
    @app_commands.describe(участник="Кого заблокировать", причина="Причина блокировки")
    @app_commands.check(is_staff)
    async def staff_ban(interaction: Interaction, участник: discord.Member, причина: str):
        role = interaction.guild.get_role(BANNED_ROLE_ID)
        if role is None:
            await interaction.response.send_message(
                embed=Embed(description="Роль блокировки не найдена на сервере.", color=EMBED_COLOR),
                ephemeral=True
            )
            return

        try:
            await участник.add_roles(role, reason=f"/staff ban by {interaction.user}: {причина}")
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=Embed(description="Недостаточно прав, чтобы выдать роль блокировки.", color=EMBED_COLOR),
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=Embed(description=f"{участник.mention} заблокирован(а).", color=EMBED_COLOR),
            ephemeral=True
        )

        log_channel = await get_log_channel(interaction.guild)
        if log_channel is None:
            return

        embed = base_log_embed(участник, "Новая блокировка", interaction.user)
        embed.add_field(name="<:information:1337130197262270535> Причина", value=причина, inline=False)
        await log_channel.send(embed=embed)

    # ==================== /staff mercy ====================

    @staff_group.command(name="mercy", description="Снять роль периода амнистии с участника [Администрация / Модерация]")
    @app_commands.describe(участник="С кого снять роль", причина="Причина снятия")
    @app_commands.check(is_staff)
    async def staff_mercy(interaction: Interaction, участник: discord.Member, причина: str):
        role = interaction.guild.get_role(AMNESTY_ROLE_ID)
        if role is None:
            await interaction.response.send_message(
                embed=Embed(description="Роль периода амнистии не найдена на сервере.", color=EMBED_COLOR),
                ephemeral=True
            )
            return

        if role not in участник.roles:
            await interaction.response.send_message(
                embed=Embed(description=f"У {участник.mention} нет данной роли.", color=EMBED_COLOR),
                ephemeral=True
            )
            return

        try:
            await участник.remove_roles(role, reason=f"/staff mercy by {interaction.user}: {причина}")
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=Embed(description="Недостаточно прав, чтобы снять роль.", color=EMBED_COLOR),
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=Embed(description=f"Роль периода амнистии снята с {участник.mention}.", color=EMBED_COLOR),
            ephemeral=True
        )

        log_channel = await get_log_channel(interaction.guild)
        if log_channel is None:
            return

        embed = base_log_embed(участник, "Период амнистии завершён досрочно", interaction.user)
        embed.add_field(name="<:information:1337130197262270535> Причина", value=причина, inline=False)
        await log_channel.send(embed=embed)

    # ==================== /staff info ====================

    @staff_group.command(name="info", description="Список доступных команд модерации")
    @app_commands.check(is_staff)
    async def staff_info(interaction: Interaction):
        embed = Embed(color=EMBED_COLOR)
        embed.set_author(name="Доступные команды", icon_url=interaction.user.display_avatar.url)

        embed.add_field(
            name="👑 Только Администрация",
            value=(
                "`/temp-role` `<@user|id> <@role> <1h52m1s>` — Выдать роль на время.\n"
                "`/purge` — Очистка сообщений."
            ),
            inline=False
        )
        embed.add_field(
            name="🛡️ Администрация / Модерация",
            value=(
                "`/staff ban` `<@user> <причина>` — Заблокировать участника.\n"
                "`/staff mercy` `<@user> <причина>` — Снять период амнистии.\n"
                "`/staff info` — Список доступных команд."
            ),
            inline=False
        )
        embed.add_field(
            name="🔧 Модерация",
            value=(
                "`/mute` `<@user|id> <1h52m1s> <причина>` — Временно заглушить.\n"
                "`/strike` `14d (причина)` — Выдать пред на 14 дней.\n"
                "`/strikes` — Список активных предупреждений.\n"
                "`/pardon` — Снять предупреждение."
            ),
            inline=False
        )
        embed.set_footer(text="Обязательно ознакомьтесь с правилами модерации")
        embed.set_image(url=DIVIDER_IMAGE)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ==================== Обработчик ошибок для группы /staff ====================

    @staff_group.error
    async def staff_error_handler(interaction: Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(
                embed=Embed(description="У вас недостаточно прав для использования этой команды!", color=EMBED_COLOR),
                ephemeral=True
            )
        else:
            raise error

    # ==================== Отслеживание изменений ролей через журнал аудита ====================

    async def get_audit_log_entry(guild: discord.Guild, target: discord.Member, action_type: discord.AuditLogAction, limit: int = 10):
        """Получить последнюю запись аудита для целевого пользователя и действия"""
        try:
            async for entry in guild.audit_logs(action=action_type, limit=limit):
                if entry.target.id == target.id:
                    return entry
        except discord.Forbidden:
            print(f"❌ Нет прав на просмотр журнала аудита на сервере {guild.id}")
            return None
        except Exception as e:
            print(f"❌ Ошибка при получении аудита: {e}")
            return None
        return None

    async def on_member_update(before: discord.Member, after: discord.Member):
        before_role_ids = {r.id for r in before.roles}
        after_role_ids = {r.id for r in after.roles}
        
        log_channel = await get_log_channel(after.guild)
        if log_channel is None:
            return

        # === ВЫДАЧА РОЛИ БЛОКИРОВКИ ===
        if BANNED_ROLE_ID in after_role_ids and BANNED_ROLE_ID not in before_role_ids:
            entry = await get_audit_log_entry(after.guild, after, discord.AuditLogAction.member_role_update)
            
            moderator = None
            reason = "Не указана"
            
            if entry:
                # Проверяем изменения ролей в аудите
                if hasattr(entry, 'changes') and entry.changes:
                    try:
                        if hasattr(entry.changes, 'before') and hasattr(entry.changes, 'after'):
                            before_roles = {r.id for r in entry.changes.before.roles} if entry.changes.before and hasattr(entry.changes.before, 'roles') else set()
                            after_roles = {r.id for r in entry.changes.after.roles} if entry.changes.after and hasattr(entry.changes.after, 'roles') else set()
                            if BANNED_ROLE_ID in after_roles and BANNED_ROLE_ID not in before_roles:
                                moderator = entry.user
                                if entry.reason:
                                    reason = entry.reason
                    except Exception as e:
                        print(f"❌ Ошибка при разборе аудита: {e}")
            
            embed = base_log_embed(after, "Новая блокировка", moderator)
            
            if moderator:
                embed.add_field(name="<:information:1337130197262270535> Причина", value=reason, inline=False)
            else:
                embed.add_field(
                    name="<:information:1337130197262270535> Причина",
                    value="*—*",
                    inline=False
                )
                embed.add_field(
                    name="<:data:1337141473162039337> Способ",
                    value="Роль выдана вручную (журнал аудита не доступен)",
                    inline=False
                )
            
            await log_channel.send(embed=embed)

        # === СНЯТИЕ РОЛИ БЛОКИРОВКИ (РАЗБАН) ===
        if BANNED_ROLE_ID in before_role_ids and BANNED_ROLE_ID not in after_role_ids:
            entry = await get_audit_log_entry(after.guild, after, discord.AuditLogAction.member_role_update)
            
            moderator = None
            reason = "Не указана"
            
            if entry:
                if hasattr(entry, 'changes') and entry.changes:
                    try:
                        if hasattr(entry.changes, 'before') and hasattr(entry.changes, 'after'):
                            before_roles = {r.id for r in entry.changes.before.roles} if entry.changes.before and hasattr(entry.changes.before, 'roles') else set()
                            after_roles = {r.id for r in entry.changes.after.roles} if entry.changes.after and hasattr(entry.changes.after, 'roles') else set()
                            if BANNED_ROLE_ID in before_roles and BANNED_ROLE_ID not in after_roles:
                                moderator = entry.user
                                if entry.reason:
                                    reason = entry.reason
                    except Exception as e:
                        print(f"❌ Ошибка при разборе аудита: {e}")
            
            embed = base_log_embed(after, "Разбан", moderator)
            
            if moderator:
                embed.add_field(name="<:information:1337130197262270535> Причина", value=reason, inline=False)
            else:
                embed.add_field(
                    name="<:information:1337130197262270535> Причина",
                    value="*—*",
                    inline=False
                )
                embed.add_field(
                    name="<:data:1337141473162039337> Способ",
                    value="Роль снята вручную (журнал аудита не доступен)",
                    inline=False
                )
            
            await log_channel.send(embed=embed)

        # === ВЫДАЧА РОЛИ АМНИСТИИ ===
        if AMNESTY_ROLE_ID in after_role_ids and AMNESTY_ROLE_ID not in before_role_ids:
            entry = await get_audit_log_entry(after.guild, after, discord.AuditLogAction.member_role_update)
            
            moderator = None
            
            if entry:
                if hasattr(entry, 'changes') and entry.changes:
                    try:
                        if hasattr(entry.changes, 'before') and hasattr(entry.changes, 'after'):
                            before_roles = {r.id for r in entry.changes.before.roles} if entry.changes.before and hasattr(entry.changes.before, 'roles') else set()
                            after_roles = {r.id for r in entry.changes.after.roles} if entry.changes.after and hasattr(entry.changes.after, 'roles') else set()
                            if AMNESTY_ROLE_ID in after_roles and AMNESTY_ROLE_ID not in before_roles:
                                moderator = entry.user
                    except Exception as e:
                        print(f"❌ Ошибка при разборе аудита: {e}")
            
            embed = base_log_embed(after, "Начался период амнистии", moderator)
            embed.add_field(
                name="<:data:1337141473162039337> Срок",
                value="14 дней",
                inline=True
            )
            embed.add_field(
                name="<:vremya:1337141252151447555> Статус",
                value="Участнику назначен период амнистии на 14 дней.",
                inline=False
            )
            
            if not moderator:
                embed.add_field(
                    name="<:data:1337141473162039337> Способ",
                    value="Роль выдана вручную",
                    inline=False
                )
            
            await log_channel.send(embed=embed)

        # === СНЯТИЕ РОЛИ АМНИСТИИ ===
        if AMNESTY_ROLE_ID in before_role_ids and AMNESTY_ROLE_ID not in after_role_ids:
            entry = await get_audit_log_entry(after.guild, after, discord.AuditLogAction.member_role_update)
            
            moderator = None
            
            if entry:
                if hasattr(entry, 'changes') and entry.changes:
                    try:
                        if hasattr(entry.changes, 'before') and hasattr(entry.changes, 'after'):
                            before_roles = {r.id for r in entry.changes.before.roles} if entry.changes.before and hasattr(entry.changes.before, 'roles') else set()
                            after_roles = {r.id for r in entry.changes.after.roles} if entry.changes.after and hasattr(entry.changes.after, 'roles') else set()
                            if AMNESTY_ROLE_ID in before_roles and AMNESTY_ROLE_ID not in after_roles:
                                moderator = entry.user
                    except Exception as e:
                        print(f"❌ Ошибка при разборе аудита: {e}")
            
            embed = base_log_embed(after, "Период амнистии завершён", moderator)
            
            if moderator:
                if moderator.id == bot.user.id:
                    embed.add_field(
                        name="<:vremya:1337141252151447555> Статус",
                        value="Амнистия успешно завершена",
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="<:vremya:1337141252151447555> Статус",
                        value="<:staffw:1337130060947128432> Амнистия завершена досрочно (ручное снятие)",
                        inline=False
                    )
                    embed.add_field(
                        name="<:data:1337141473162039337> Способ",
                        value=f"Роль снята {moderator.mention} через журнал аудита",
                        inline=False
                    )
            else:
                embed.add_field(
                    name="<:vremya:1337141252151447555> Статус",
                    value="<:staffw:1337130060947128432> Амнистия завершена (способ не определён)",
                    inline=False
                )
                embed.add_field(
                    name="<:data:1337141473162039337> Способ",
                    value="Роль снята вручную (журнал аудита не доступен)",
                    inline=False
                )
            
            await log_channel.send(embed=embed)

    bot.add_listener(on_member_update, 'on_member_update')

    bot.tree.add_command(staff_group)
