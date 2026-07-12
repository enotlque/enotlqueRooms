import discord
from discord import app_commands, Interaction, Embed, ButtonStyle
from discord.ui import View, Button
from datetime import datetime


STATIC_LOBBY_CHANNEL_ID = 1526001323139666172 


def is_admin(interaction: discord.Interaction):
    return interaction.user.guild_permissions.administrator


def setup_lobby_commands(bot, cursor):
    lobby_group = app_commands.Group(name="lobby", description="Управление системой умного лобби")

    # ==================== /lobby manage ====================

    @lobby_group.command(name="manage", description="Привязать участников к голосовому каналу [Только для Администрации]")
    @app_commands.check(is_admin)
    async def lobby_manage(interaction: Interaction):
        view = LobbyManageView(interaction.user)
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)

    class LobbyManageView(View):
        def __init__(self, admin):
            super().__init__(timeout=120)
            self.admin = admin
            self.selected_channel: discord.VoiceChannel | None = None
            self.selected_users: list = []

            self.channel_select = LobbyChannelSelect(self)
            self.user_select = LobbyUserSelect(self)
            self.confirm_button = LobbyConfirmButton(self)

            self.add_item(self.channel_select)
            self.add_item(self.user_select)
            self.add_item(self.confirm_button)
            self.confirm_button.disabled = True

        def update_confirm_state(self):
            self.confirm_button.disabled = not (self.selected_channel and self.selected_users)

        def build_embed(self):
            embed = Embed(color=0x6e6e6e)
            embed.set_author(name="Настройка умного лобби", icon_url=self.admin.display_avatar.url)
            embed.add_field(
                name="<:voice:1337103709150248992> Канал назначения",
                value=self.selected_channel.mention if self.selected_channel else "не выбран",
                inline=True
            )
            embed.add_field(
                name="<:people:1337103698568020091> Участники",
                value="\n".join(u.mention for u in self.selected_users) if self.selected_users else "не выбраны",
                inline=True
            )
            embed.set_footer(text="Выберите канал и участников, затем нажмите «Привязать»")
            return embed

    class LobbyChannelSelect(discord.ui.ChannelSelect):
        def __init__(self, parent_view: "LobbyManageView"):
            super().__init__(
                placeholder="Выберите голосовой канал назначения",
                channel_types=[discord.ChannelType.voice],
                min_values=1,
                max_values=1
            )
            self.parent_view = parent_view

        async def callback(self, interaction: Interaction):
            self.parent_view.selected_channel = self.values[0]
            self.parent_view.update_confirm_state()
            await interaction.response.edit_message(embed=self.parent_view.build_embed(), view=self.parent_view)

    class LobbyUserSelect(discord.ui.UserSelect):
        def __init__(self, parent_view: "LobbyManageView"):
            super().__init__(
                placeholder="Выберите участников (до 25)",
                min_values=1,
                max_values=25
            )
            self.parent_view = parent_view

        async def callback(self, interaction: Interaction):
            self.parent_view.selected_users = list(self.values)
            self.parent_view.update_confirm_state()
            await interaction.response.edit_message(embed=self.parent_view.build_embed(), view=self.parent_view)

    class LobbyConfirmButton(Button):
        def __init__(self, parent_view: "LobbyManageView"):
            super().__init__(label="Привязать", style=ButtonStyle.success, emoji="<:checkmark:1299081136013709352>")
            self.parent_view = parent_view

        async def callback(self, interaction: Interaction):
            channel = self.parent_view.selected_channel
            users = self.parent_view.selected_users

            bound = []
            for user in users:
                await cursor.execute('''
                    INSERT INTO lobby_bindings (user_id, voice_channel_id, bound_by, bound_at)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (user_id) DO UPDATE SET
                        voice_channel_id = EXCLUDED.voice_channel_id,
                        bound_by = EXCLUDED.bound_by,
                        bound_at = EXCLUDED.bound_at
                ''', user.id, channel.id, interaction.user.id, datetime.now().strftime('%d.%m.%Y %H:%M'))
                bound.append(user.mention)

            result_embed = Embed(color=0x6e6e6e)
            result_embed.set_author(name="Лобби настроено", icon_url=interaction.user.display_avatar.url)
            result_embed.add_field(name="<:voice:1337103709150248992> Канал", value=channel.mention, inline=False)
            result_embed.add_field(name="<:checkmark:1299081136013709352> Привязаны", value="\n".join(bound), inline=False)

            await interaction.response.edit_message(embed=result_embed, view=None)

    # ==================== /lobby who ====================

    @lobby_group.command(name="who", description="Показать привязку участников к каналам лобби [Только для Администрации]")
    @app_commands.check(is_admin)
    async def lobby_who(interaction: Interaction):
        view = LobbyWhoView(interaction.user)
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)

    class LobbyWhoView(View):
        def __init__(self, admin):
            super().__init__(timeout=60)
            self.admin = admin
            self.add_item(LobbyWhoSelect(self))

        def build_embed(self):
            embed = Embed(description="Выберите участников, чтобы проверить их привязку к лобби.", color=0x6e6e6e)
            embed.set_author(name="Проверка привязок", icon_url=self.admin.display_avatar.url)
            return embed

    class LobbyWhoSelect(discord.ui.UserSelect):
        def __init__(self, parent_view: "LobbyWhoView"):
            super().__init__(placeholder="Выберите участников (до 25)", min_values=1, max_values=25)
            self.parent_view = parent_view

        async def callback(self, interaction: Interaction):
            guild = interaction.guild
            lines = []
            for user in self.values:
                await cursor.execute('SELECT voice_channel_id FROM lobby_bindings WHERE user_id = $1', user.id)
                row = cursor.fetchone()
                if row and (channel := guild.get_channel(row[0])):
                    lines.append(f"{user.mention} → {channel.mention}")
                elif row:
                    lines.append(f"{user.mention} → привязанный канал удалён")
                else:
                    lines.append(f"{user.mention} → не привязан")

            embed = Embed(color=0x6e6e6e)
            embed.set_author(name="Привязки лобби", icon_url=self.parent_view.admin.display_avatar.url)
            embed.description = "\n".join(lines)
            await interaction.response.edit_message(embed=embed, view=None)

    # ==================== /lobby unbind ====================

    @lobby_group.command(name="unbind", description="Снять привязку участников к лобби [Только для Администрации]")
    @app_commands.check(is_admin)
    async def lobby_unbind(interaction: Interaction):
        view = LobbyUnbindView(interaction.user)
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)

    class LobbyUnbindView(View):
        def __init__(self, admin):
            super().__init__(timeout=60)
            self.admin = admin
            self.add_item(LobbyUnbindSelect(self))

        def build_embed(self):
            embed = Embed(description="Выберите участников, привязку которых нужно снять.", color=0x6e6e6e)
            embed.set_author(name="Снятие привязки", icon_url=self.admin.display_avatar.url)
            return embed

    class LobbyUnbindSelect(discord.ui.UserSelect):
        def __init__(self, parent_view: "LobbyUnbindView"):
            super().__init__(placeholder="Выберите участников (до 25)", min_values=1, max_values=25)
            self.parent_view = parent_view

        async def callback(self, interaction: Interaction):
            removed, skipped = [], []
            for user in self.values:
                await cursor.execute('SELECT voice_channel_id FROM lobby_bindings WHERE user_id = $1', user.id)
                if cursor.fetchone() is None:
                    skipped.append(f"{user.mention} — не был привязан")
                    continue
                await cursor.execute('DELETE FROM lobby_bindings WHERE user_id = $1', user.id)
                removed.append(user.mention)

            embed = Embed(color=0x6e6e6e)
            embed.set_author(name="Привязки сняты", icon_url=self.parent_view.admin.display_avatar.url)
            if removed:
                embed.add_field(name="<:checkmark:1299081136013709352> Сняты", value="\n".join(removed), inline=False)
            if skipped:
                embed.add_field(name="<:xxx:1299081147917008938> Пропущены", value="\n".join(skipped), inline=False)
            await interaction.response.edit_message(embed=embed, view=None)

    # ==================== Обработчик ошибок для группы /lobby ====================

    @lobby_group.error
    async def lobby_error_handler(interaction: Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(
                embed=Embed(description="У вас недостаточно прав для использования этой команды!", color=0x6e6e6e),
                ephemeral=True
            )
        else:
            raise error

    # ==================== Автоматический редирект из статичного канала ====================

    async def on_voice_state_update(member, before, after):
        # Интересует только вход в статичный лобби-канал
        if after.channel is None or after.channel.id != STATIC_LOBBY_CHANNEL_ID:
            return
        # Защита от повторного срабатывания, если человек уже был в этом канале
        if before.channel is not None and before.channel.id == STATIC_LOBBY_CHANNEL_ID:
            return

        await cursor.execute('SELECT voice_channel_id FROM lobby_bindings WHERE user_id = $1', member.id)
        row = cursor.fetchone()
        if row is None:
            return  # Участник не привязан ни к одному каналу

        target_channel = member.guild.get_channel(row[0])
        if target_channel is None or not isinstance(target_channel, discord.VoiceChannel):
            return  # Привязанный канал был удалён

        try:
            await member.move_to(target_channel, reason="Автоматическое перенаправление из умного лобби")
        except (discord.Forbidden, discord.HTTPException):
            pass

    bot.add_listener(on_voice_state_update, 'on_voice_state_update')

    bot.tree.add_command(lobby_group)
