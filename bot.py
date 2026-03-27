import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, timezone

import config
import db

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ── helpers ──────────────────────────────────────────────────────────────────

def get_role_id_for_sp(sp: int) -> int:
    for min_sp, max_sp, role_id in config.SP_ROLES:
        if min_sp is None and sp < max_sp:
            return role_id
        if max_sp is None and sp >= min_sp:
            return role_id
        if min_sp is not None and max_sp is not None and min_sp <= sp < max_sp:
            return role_id
    return config.SP_ROLES[-1][2]

async def update_member_roles(member: discord.Member, sp: int):
    all_sp_role_ids = {role_id for _, _, role_id in config.SP_ROLES}
    ban_role = member.guild.get_role(config.SP_BAN_ROLE_ID) if config.SP_BAN_ROLE_ID else None

    if sp <= config.SP_BAN_THRESHOLD:
        roles_to_remove = [r for r in member.roles if r.id in all_sp_role_ids]
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason="SP ban")
        if ban_role and ban_role not in member.roles:
            await member.add_roles(ban_role, reason=f"SP досяг {sp}")
            for channel in member.guild.channels:
                try:
                    await channel.set_permissions(member, view_channel=False, reason=f"SP ban ({sp})")
                except discord.Forbidden:
                    pass
    else:
        if ban_role and ban_role in member.roles:
            await member.remove_roles(ban_role, reason="SP ban lifted")
            for channel in member.guild.channels:
                try:
                    await channel.set_permissions(member, overwrite=None, reason="SP ban lifted")
                except discord.Forbidden:
                    pass
        target_role_id = get_role_id_for_sp(sp)
        roles_to_remove = [r for r in member.roles if r.id in all_sp_role_ids and r.id != target_role_id]
        target_role = member.guild.get_role(target_role_id)
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason="SP role update")
        if target_role and target_role not in member.roles:
            await member.add_roles(target_role, reason="SP role update")

def has_sp_admin_role():
    async def predicate(interaction: discord.Interaction) -> bool:
        role = interaction.guild.get_role(config.SP_ADMIN_ROLE_ID)
        if role and role in interaction.user.roles:
            return True
        raise app_commands.MissingRole(config.SP_ADMIN_ROLE_ID)
    return app_commands.check(predicate)

STATUS_UA = {
    "open":    "🟢 Відкрите",
    "taken":   "🟡 Взяте",
    "pending": "🕐 На перевірці",
    "done":    "✅ Виконане",
}

def format_deadline(row) -> str | None:
    """Повертає рядок з дедлайном або None якщо не встановлено."""
    # row: id,guild_id,title,description,sp_reward,status,taken_by,proof_msg_id,created_by,deadline_hours,taken_at
    deadline_hours = row[9]
    taken_at_str = row[10]
    if not deadline_hours:
        return None
    if taken_at_str:
        taken_at = datetime.fromisoformat(taken_at_str)
        now = datetime.now(timezone.utc)
        elapsed = (now - taken_at).total_seconds() / 3600
        remaining = deadline_hours - elapsed
        if remaining <= 0:
            return "⏰ Дедлайн минув!"
        h = int(remaining)
        m = int((remaining - h) * 60)
        return f"⏳ Залишилось: **{h}г {m}хв** (з {deadline_hours}г)"
    return f"⏳ Дедлайн: **{deadline_hours} год** після взяття"

def task_embed(task: tuple, guild: discord.Guild) -> discord.Embed:
    tid, _, title, description, sp_reward, status, taken_by, _, _, deadline_hours, taken_at = task
    color = {"open": 0x57F287, "taken": 0xFEE75C, "pending": 0xEB459E, "done": 0x95A5A6}.get(status, 0xFFFFFF)
    embed = discord.Embed(title=f"📋 Завдання #{tid} — {title}", description=description, color=color)
    embed.add_field(name="Нагорода", value=f"**{sp_reward} SP**")
    embed.add_field(name="Статус", value=STATUS_UA.get(status, status))
    if taken_by:
        m = guild.get_member(taken_by)
        embed.add_field(name="Виконує", value=m.mention if m else f"<@{taken_by}>")
    dl = format_deadline(task)
    if dl:
        embed.add_field(name="Дедлайн", value=dl, inline=False)
    return embed

# ── background task: перевірка дедлайнів ─────────────────────────────────────

@tasks.loop(minutes=5)
async def check_deadlines():
    expired = await db.get_expired_tasks()
    for row in expired:
        # id,guild_id,title,description,sp_reward,status,taken_by,proof_msg_id,created_by,deadline_hours,taken_at
        task_id   = row[0]
        guild_id  = row[1]
        title     = row[2]
        sp_reward = row[4]
        taken_by  = row[6]

        # знімаємо SP
        new_sp = await db.add_sp(taken_by, guild_id, -sp_reward)
        await db.expire_task(task_id)

        guild = bot.get_guild(guild_id)
        if not guild:
            continue

        member = guild.get_member(taken_by)
        if member:
            await update_member_roles(member, new_sp)

        # сповіщення в системний канал або перший доступний текстовий
        channel = guild.system_channel or next(
            (c for c in guild.text_channels if c.permissions_for(guild.me).send_messages), None
        )
        if channel:
            await channel.send(
                f"⏰ Дедлайн для завдання **#{task_id} — {title}** минув!\n"
                f"{member.mention if member else f'<@{taken_by}>'} не встиг і втрачає "
                f"**{sp_reward} SP** (залишок: **{new_sp} SP**).\n"
                f"Завдання знову відкрите."
            )

# ── events ────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    await db.init_db()
    await bot.tree.sync()
    check_deadlines.start()
    print(f"Бот запущено як {bot.user} (ID: {bot.user.id})")

# ── SP команди ───────────────────────────────────────────────────────────────

@bot.tree.command(name="addsp", description="Додати SP користувачу (тільки вища посада)")
@app_commands.describe(member="Користувач", amount="Кількість SP (може бути від'ємною)")
@has_sp_admin_role()
async def addsp(interaction: discord.Interaction, member: discord.Member, amount: int):
    new_sp = await db.add_sp(member.id, interaction.guild_id, amount)
    await update_member_roles(member, new_sp)
    role = interaction.guild.get_role(get_role_id_for_sp(new_sp))
    await interaction.response.send_message(
        f"✅ {member.mention} тепер має **{new_sp} SP** → {role.mention if role else ''}"
    )

@bot.tree.command(name="setsp", description="Встановити SP користувачу (тільки вища посада)")
@app_commands.describe(member="Користувач", amount="Нова кількість SP")
@has_sp_admin_role()
async def setsp(interaction: discord.Interaction, member: discord.Member, amount: int):
    await db.set_sp(member.id, interaction.guild_id, amount)
    await update_member_roles(member, amount)
    role = interaction.guild.get_role(get_role_id_for_sp(amount))
    await interaction.response.send_message(
        f"✅ {member.mention} встановлено **{amount} SP** → {role.mention if role else ''}"
    )

@bot.tree.command(name="sp", description="Переглянути свій або чужий SP")
@app_commands.describe(member="Користувач (необов'язково)")
async def sp_cmd(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    sp = await db.get_sp(target.id, interaction.guild_id)
    role = interaction.guild.get_role(get_role_id_for_sp(sp))
    await interaction.response.send_message(
        f"🏅 {target.mention} має **{sp} SP** — {role.mention if role else ''}"
    )

@bot.tree.command(name="leaderboard", description="Топ-10 гравців за SP")
async def leaderboard(interaction: discord.Interaction):
    rows = await db.get_leaderboard(interaction.guild_id)
    if not rows:
        await interaction.response.send_message("Поки що немає даних.", ephemeral=True)
        return
    lines = []
    medals = ["🥇", "🥈", "🥉"]
    for i, (user_id, sp) in enumerate(rows):
        prefix = medals[i] if i < 3 else f"`{i+1}.`"
        m = interaction.guild.get_member(user_id)
        name = m.display_name if m else f"<@{user_id}>"
        role = interaction.guild.get_role(get_role_id_for_sp(sp))
        lines.append(f"{prefix} {name} — **{sp} SP** ({role.name if role else '?'})")
    embed = discord.Embed(title="🏆 Рейтинг SP", description="\n".join(lines), color=0xFFD700)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="roles_info", description="Показати всі рівні SP та ролі")
async def roles_info(interaction: discord.Interaction):
    lines = []
    for min_sp, max_sp, role_id in config.SP_ROLES:
        role = interaction.guild.get_role(role_id)
        role_str = role.mention if role else f"<@&{role_id}>"
        if min_sp is None:
            lines.append(f"SP < {max_sp} → {role_str}")
        elif max_sp is None:
            lines.append(f"SP ≥ {min_sp} → {role_str}")
        else:
            lines.append(f"{min_sp} ≤ SP < {max_sp} → {role_str}")
    embed = discord.Embed(title="📋 Рівні SP", description="\n".join(lines), color=0x5865F2)
    await interaction.response.send_message(embed=embed)

# ── Завдання ──────────────────────────────────────────────────────────────────

@bot.tree.command(name="task_create", description="Створити завдання (тільки вища посада)")
@app_commands.describe(
    title="Назва",
    description="Опис завдання",
    sp_reward="Кількість SP за виконання",
    deadline_hours="Дедлайн у годинах після взяття (необов'язково)"
)
@has_sp_admin_role()
async def task_create(interaction: discord.Interaction, title: str, description: str,
                      sp_reward: int, deadline_hours: int = None):
    if sp_reward <= 0:
        await interaction.response.send_message("❌ Нагорода має бути більше 0.", ephemeral=True)
        return
    if deadline_hours is not None and deadline_hours <= 0:
        await interaction.response.send_message("❌ Дедлайн має бути більше 0 годин.", ephemeral=True)
        return
    task_id = await db.create_task(
        interaction.guild_id, title, description, sp_reward,
        interaction.user.id, deadline_hours
    )
    dl_str = f" | Дедлайн: **{deadline_hours}г**" if deadline_hours else ""
    await interaction.response.send_message(
        f"✅ Завдання **#{task_id} — {title}** створено! Нагорода: **{sp_reward} SP**{dl_str}"
    )

@bot.tree.command(name="tasks", description="Список завдань")
@app_commands.describe(filter="Фільтр статусу")
@app_commands.choices(filter=[
    app_commands.Choice(name="Відкриті", value="open"),
    app_commands.Choice(name="Взяті", value="taken"),
    app_commands.Choice(name="На перевірці", value="pending"),
    app_commands.Choice(name="Виконані", value="done"),
    app_commands.Choice(name="Всі", value="all"),
])
async def tasks_list(interaction: discord.Interaction, filter: str = "open"):
    status = None if filter == "all" else filter
    rows = await db.get_tasks(interaction.guild_id, status)
    if not rows:
        await interaction.response.send_message("Завдань не знайдено.", ephemeral=True)
        return
    lines = []
    for row in rows:
        tid, _, title, _, sp_reward, status, *_ = row
        icon = {"open": "🟢", "taken": "🟡", "pending": "🕐", "done": "✅"}.get(status, "❓")
        dl = f" ⏳{row[9]}г" if row[9] else ""
        lines.append(f"{icon} **#{tid}** {title} — {sp_reward} SP{dl}")
    embed = discord.Embed(title="📋 Завдання", description="\n".join(lines), color=0x5865F2)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="task_info", description="Детальна інформація про завдання")
@app_commands.describe(task_id="ID завдання")
async def task_info(interaction: discord.Interaction, task_id: int):
    task = await db.get_task(task_id)
    if not task or task[1] != interaction.guild_id:
        await interaction.response.send_message("❌ Завдання не знайдено.", ephemeral=True)
        return
    await interaction.response.send_message(embed=task_embed(task, interaction.guild))

@bot.tree.command(name="task_take", description="Взяти завдання")
@app_commands.describe(task_id="ID завдання")
async def task_take(interaction: discord.Interaction, task_id: int):
    task = await db.get_task(task_id)
    if not task or task[1] != interaction.guild_id:
        await interaction.response.send_message("❌ Завдання не знайдено.", ephemeral=True)
        return
    ok = await db.take_task(task_id, interaction.user.id)
    if not ok:
        await interaction.response.send_message("❌ Завдання вже взяте або недоступне.", ephemeral=True)
        return
    dl_str = f"\n⏳ У тебе є **{task[9]} годин** щоб виконати його, інакше втратиш **{task[4]} SP**." if task[9] else ""
    await interaction.response.send_message(
        f"✅ {interaction.user.mention} взяв завдання **#{task_id} — {task[2]}**.\n"
        f"Виконай і подай доказ через `/task_proof`.{dl_str}"
    )

@bot.tree.command(name="task_abandon", description="Відмовитись від завдання яке ти взяв")
@app_commands.describe(task_id="ID завдання")
async def task_abandon(interaction: discord.Interaction, task_id: int):
    task = await db.get_task(task_id)
    if not task or task[1] != interaction.guild_id:
        await interaction.response.send_message("❌ Завдання не знайдено.", ephemeral=True)
        return
    ok = await db.abandon_task(task_id, interaction.user.id)
    if not ok:
        await interaction.response.send_message(
            "❌ Ти не можеш відмовитись від цього завдання (не ти брав або вже подав доказ).",
            ephemeral=True
        )
        return
    new_sp = await db.add_sp(interaction.user.id, interaction.guild_id, -2)
    await update_member_roles(interaction.user, new_sp)
    await interaction.response.send_message(
        f"↩️ {interaction.user.mention} відмовився від завдання **#{task_id} — {task[2]}**.\n"
        f"Штраф: **-2 SP** (залишок: **{new_sp} SP**). Завдання знову відкрите."
    )

@bot.tree.command(name="task_proof", description="Подати доказ виконання завдання")
@app_commands.describe(task_id="ID завдання", proof="Опис або посилання на доказ")
async def task_proof(interaction: discord.Interaction, task_id: int, proof: str):
    task = await db.get_task(task_id)
    if not task or task[1] != interaction.guild_id:
        await interaction.response.send_message("❌ Завдання не знайдено.", ephemeral=True)
        return
    await interaction.response.defer()
    msg = await interaction.followup.send(
        f"📨 {interaction.user.mention} подає доказ для завдання **#{task_id} — {task[2]}**\n"
        f"**Доказ:** {proof}\n\n"
        f"Адміністратор: `/task_approve {task_id}` або `/task_reject {task_id}`"
    )
    ok = await db.submit_proof(task_id, interaction.user.id, msg.id)
    if not ok:
        await msg.delete()
        await interaction.followup.send(
            "❌ Ти не можеш подати доказ для цього завдання.", ephemeral=True
        )

@bot.tree.command(name="task_approve", description="Підтвердити виконання завдання (тільки вища посада)")
@app_commands.describe(task_id="ID завдання")
@has_sp_admin_role()
async def task_approve(interaction: discord.Interaction, task_id: int):
    data = await db.approve_task(task_id)
    if not data:
        await interaction.response.send_message("❌ Завдання не знайдено або не на перевірці.", ephemeral=True)
        return
    new_sp = await db.add_sp(data["taken_by"], interaction.guild_id, data["sp_reward"])
    member = interaction.guild.get_member(data["taken_by"])
    if member:
        await update_member_roles(member, new_sp)
    role = interaction.guild.get_role(get_role_id_for_sp(new_sp))
    taken_mention = member.mention if member else f"<@{data['taken_by']}>"
    await interaction.response.send_message(
        f"✅ Завдання **#{task_id} — {data['title']}** підтверджено!\n"
        f"{taken_mention} отримує "
        f"**+{data['sp_reward']} SP** (всього: **{new_sp} SP**) {role.mention if role else ''}"
    )

@bot.tree.command(name="task_reject", description="Відхилити доказ завдання (тільки вища посада)")
@app_commands.describe(task_id="ID завдання", reason="Причина відхилення")
@has_sp_admin_role()
async def task_reject(interaction: discord.Interaction, task_id: int, reason: str = "Не вказано"):
    task = await db.get_task(task_id)
    ok = await db.reject_task(task_id)
    if not ok:
        await interaction.response.send_message("❌ Завдання не знайдено або не на перевірці.", ephemeral=True)
        return
    taken_by = task[6]
    member = interaction.guild.get_member(taken_by) if taken_by else None
    await interaction.response.send_message(
        f"❌ Доказ для завдання **#{task_id}** відхилено. Причина: {reason}\n"
        f"{member.mention + ', ' if member else ''}завдання знову відкрите."
    )

@bot.tree.command(name="task_list_pending", description="Всі завдання що чекають підтвердження (тільки вища посада)")
@has_sp_admin_role()
async def task_list_pending(interaction: discord.Interaction):
    rows = await db.get_tasks(interaction.guild_id, "pending")
    if not rows:
        await interaction.response.send_message("✅ Немає завдань на перевірці.", ephemeral=True)
        return
    lines = []
    for row in rows:
        tid, _, title, _, sp_reward, _, taken_by, _, _, deadline_hours, _ = row
        m = interaction.guild.get_member(taken_by)
        name = m.mention if m else f"<@{taken_by}>"
        lines.append(f"🕐 **#{tid}** {title} — {sp_reward} SP | виконує {name}")
    embed = discord.Embed(title="🕐 На перевірці", description="\n".join(lines), color=0xEB459E)
    embed.set_footer(text=f"Всього: {len(rows)}")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="profile", description="Профіль гравця")
@app_commands.describe(member="Користувач (необов'язково)")
async def profile(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    sp = await db.get_sp(target.id, interaction.guild_id)
    stats = await db.get_user_stats(target.id, interaction.guild_id)
    rank = await db.get_rank(target.id, interaction.guild_id)
    role = interaction.guild.get_role(get_role_id_for_sp(sp))

    embed = discord.Embed(title=f"👤 {target.display_name}", color=role.color if role else 0x5865F2)
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Роль", value=role.mention if role else "—")
    embed.add_field(name="SP", value=f"**{sp}**")
    embed.add_field(name="Місце в рейтингу", value=f"**#{rank}**")
    embed.add_field(name="Виконано завдань", value=f"**{stats['completed']}**")
    embed.add_field(name="Зароблено SP із завдань", value=f"**{stats['earned']}**")
    await interaction.response.send_message(embed=embed)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    print(f"[ERROR] {interaction.command.name}: {error}")
    if isinstance(error, (app_commands.MissingRole, app_commands.MissingPermissions)):
        msg = "❌ Тільки вища посада може виконувати цю дію."
    else:
        msg = f"❌ Помилка: {error}"
    try:
        await interaction.response.send_message(msg, ephemeral=True)
    except discord.InteractionResponded:
        await interaction.followup.send(msg, ephemeral=True)

# ── error handlers ────────────────────────────────────────────────────────────

@addsp.error
@setsp.error
@task_create.error
@task_approve.error
@task_reject.error
@task_list_pending.error
async def role_error(interaction: discord.Interaction, error):
    if isinstance(error, (app_commands.MissingRole, app_commands.MissingPermissions)):
        await interaction.response.send_message(
            "❌ Тільки вища посада може виконувати цю дію.", ephemeral=True
        )

# ── run ───────────────────────────────────────────────────────────────────────

bot.run(config.TOKEN)
