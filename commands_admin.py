import discord
from discord import app_commands, Interaction, Embed


BLOCK_ROLE_ID = 1129742835487358989


def is_admin(interaction: discord.Interaction):
    return interaction.user.guild_permissions.administrator


def setup_admin_commands(bot):

    @app_commands.command(name="block", description="Выдать роль блокировки указанному пользователю [Только для Администрации]")
    @app_commands.describe(участник="Участник, которому будет выдана роль блокировки")
    @app_commands.check(is_admin)
    async def block(interaction: Interaction, участник: discord.Member):
        guild = interaction.guild
        role = guild.get_role(BLOCK_ROLE_ID)

        if role is None:
            await interaction.response.send_message(
                embed=Embed(description="Роль блокировки не найдена на сервере.", color=0xFF0000),
                ephemeral=True
            )
            return

        if role in участник.roles:
            await interaction.response.send_message(
                embed=Embed(description=f"{участник.mention} уже заблокирован.", color=0xFFA500),
                ephemeral=True
            )
            return

        try:
            await участник.add_roles(role, reason=f"Блокировка администратором {interaction.user}")
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=Embed(description="Недостаточно прав для выдачи роли.", color=0xFF0000),
                ephemeral=True
            )
            return

        embed = Embed(description=f"{участник.mention} заблокирован.", color=0x6e6e6e)
        embed.set_author(name=участник.display_name, icon_url=участник.display_avatar.url)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @block.error
    async def block_error_handler(interaction: Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(
                embed=Embed(description="У вас недостаточно прав для использования этой команды!", color=0x6e6e6e),
                ephemeral=True
            )
        else:
            raise error

    bot.tree.add_command(block)
