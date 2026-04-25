"""
Profile Handlers

Handles profile viewing, title/badge customization,
detailed statistics, and referral information.
"""

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import CURRENCY_NAME, REFERRAL_BONUS
from database import async_fetchone, async_fetchall, async_execute

logger = logging.getLogger(__name__)

AVAILABLE_BADGES = ["🟢", "🔵", "🟣", "🔴", "🟡", "⭐", "💎", "👑", "🏆", "🎯"]
PREMIUM_BADGES = ["⭐", "💎", "👑", "🏆", "🎯"]

PREMIUM_TITLES = [
    "Champion", "Legend", "Master", "Elite", "Veteran",
    "Conqueror", "Paladin", "Warlord", "Titan", "Guardian",
]

FREE_TITLES = [
    "New Player", "Rookie", "Adventurer", "Competitor", "Regular",
]


async def profile_view_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show user profile with balance, wins, games, badges, titles, owned items."""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id

    user = await async_fetchone(
        "SELECT user_id, username, first_name, last_name, created_at FROM users WHERE user_id = ?",
        (user_id,),
    )
    if not user:
        await query.answer("Profile not found. Use /start to register.", show_alert=True)
        return

    wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
    balance = wallet["balance"] if wallet else 0.0

    profile = await async_fetchone(
        "SELECT title, badge, wins, losses, games_played FROM profiles WHERE user_id = ?",
        (user_id,),
    )

    owned_features = await async_fetchall(
        "SELECT feature FROM owned_features WHERE user_id = ?",
        (user_id,),
    )
    feature_list = [f["feature"] for f in owned_features]

    referral_count = await async_fetchone(
        "SELECT COUNT(*) as cnt FROM referrals WHERE referrer_id = ?",
        (user_id,),
    )
    total_referrals = referral_count["cnt"] if referral_count else 0

    purchases = await async_fetchall(
        "SELECT p.id, si.name FROM purchases p "
        "JOIN store_items si ON p.item_id = si.id "
        "WHERE p.user_id = ? AND p.status = 'completed' "
        "ORDER BY p.created_at DESC LIMIT 5",
        (user_id,),
    )

    badge = profile["badge"] if profile else "🟢"
    title = profile["title"] if profile else "New Player"
    wins = profile["wins"] if profile else 0
    losses = profile["losses"] if profile else 0
    games = profile["games_played"] if profile else 0
    win_rate = (wins / games * 100) if games > 0 else 0.0

    name = user["first_name"] or user["username"] or "Player"

    text = (
        f"👤 <b>Player Profile</b>\n\n"
        f"{badge} <b>{name}</b>\n"
        f"🏷 Title: <b>{title}</b>\n\n"
        f"┌─────────────────────┐\n"
        f"│ 💰 Balance: <b>{balance:.2f} {CURRENCY_NAME}</b>\n"
        f"│ 🏆 Wins: <b>{wins}</b>\n"
        f"│ 💔 Losses: <b>{losses}</b>\n"
        f"│ 🎮 Games: <b>{games}</b>\n"
        f"│ 📊 Win Rate: <b>{win_rate:.1f}%</b>\n"
        f"└─────────────────────┘\n\n"
    )

    if feature_list:
        features_text = ", ".join(feature_list)
        text += f"🎒 <b>Owned Features:</b> {features_text}\n\n"

    if purchases:
        text += "🛍 <b>Recent Purchases:</b>\n"
        for p in purchases:
            text += f"  • {p['name']}\n"
        text += "\n"

    text += (
        f"👥 <b>Referrals:</b> {total_referrals}\n"
        f"🔗 <b>Referral Code:</b> <code>ref_{user_id}</code>\n"
        f"   Share to earn <b>{REFERRAL_BONUS} {CURRENCY_NAME}</b> per referral!\n"
    )

    is_premium = "premium_profile" in feature_list
    if is_premium:
        text += "\n⭐ <b>Premium Member</b>"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏆 Set Title", callback_data="profile_set_title"),
            InlineKeyboardButton("🎖 Set Badge", callback_data="profile_set_badge"),
        ],
        [InlineKeyboardButton("📈 Detailed Stats", callback_data="profile_stats")],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="back_to_main")],
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def profile_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all profile-related callback queries."""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "profile_view":
        await profile_view_handler(update, context)
    elif data == "profile_set_title":
        await _show_title_selection(query, context)
    elif data == "profile_set_badge":
        await _show_badge_selection(query, context)
    elif data == "profile_stats":
        await _show_detailed_stats(query, context)
    elif data.startswith("profile_title_select:"):
        title_key = data.split(":", 1)[1]
        await _set_title(query, context, title_key)
    elif data.startswith("profile_badge_select:"):
        badge = data.split(":", 1)[1]
        await _set_badge(query, context, badge)
    elif data == "profile_title_free":
        await _show_free_titles(query, context)
    elif data == "profile_title_premium":
        await _show_premium_titles(query, context)
    else:
        await profile_view_handler(update, context)


async def _show_title_selection(query, context) -> None:
    """Show title selection menu."""
    user_id = query.from_user.id

    is_premium = await _is_premium(user_id)

    text = (
        "🏆 <b>Choose Your Title</b>\n\n"
        "Select a title category:"
    )

    keyboard_rows = [
        [InlineKeyboardButton("📋 Free Titles", callback_data="profile_title_free")],
    ]

    if is_premium:
        keyboard_rows.append([
            InlineKeyboardButton("⭐ Premium Titles", callback_data="profile_title_premium"),
        ])
    else:
        keyboard_rows.append([
            InlineKeyboardButton("🔒 Premium (Requires Premium Profile)", callback_data="profile_view"),
        ])

    keyboard_rows.append([
        InlineKeyboardButton("🔙 Back to Profile", callback_data="profile_view"),
    ])

    keyboard = InlineKeyboardMarkup(keyboard_rows)

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _show_free_titles(query, context) -> None:
    """Show free title options."""
    text = (
        "📋 <b>Free Titles</b>\n\n"
        "Choose a title:"
    )

    keyboard_rows = []
    for i in range(0, len(FREE_TITLES), 2):
        row = []
        for j in range(2):
            if i + j < len(FREE_TITLES):
                title = FREE_TITLES[i + j]
                row.append(InlineKeyboardButton(
                    title,
                    callback_data=f"profile_title_select:{title}",
                ))
        keyboard_rows.append(row)

    keyboard_rows.append([
        InlineKeyboardButton("🔙 Back to Titles", callback_data="profile_set_title"),
    ])

    keyboard = InlineKeyboardMarkup(keyboard_rows)

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _show_premium_titles(query, context) -> None:
    """Show premium title options."""
    user_id = query.from_user.id
    is_premium = await _is_premium(user_id)

    if not is_premium:
        text = (
            "🔒 <b>Premium Titles</b>\n\n"
            "You need a Premium Profile Pack to access premium titles.\n"
            "Purchase one from the Marketplace!"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Marketplace", callback_data="marketplace_view")],
            [InlineKeyboardButton("🔙 Back to Titles", callback_data="profile_set_title")],
        ])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        return

    text = (
        "⭐ <b>Premium Titles</b>\n\n"
        "Choose a premium title:"
    )

    keyboard_rows = []
    for i in range(0, len(PREMIUM_TITLES), 2):
        row = []
        for j in range(2):
            if i + j < len(PREMIUM_TITLES):
                title = PREMIUM_TITLES[i + j]
                row.append(InlineKeyboardButton(
                    f"⭐ {title}",
                    callback_data=f"profile_title_select:{title}",
                ))
        keyboard_rows.append(row)

    keyboard_rows.append([
        InlineKeyboardButton("📋 Free Titles", callback_data="profile_title_free"),
    ])
    keyboard_rows.append([
        InlineKeyboardButton("🔙 Back to Titles", callback_data="profile_set_title"),
    ])

    keyboard = InlineKeyboardMarkup(keyboard_rows)

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _set_title(query, context, title: str) -> None:
    """Set the user's profile title."""
    user_id = query.from_user.id

    is_premium = await _is_premium(user_id)
    if title in PREMIUM_TITLES and not is_premium:
        await query.answer("You need Premium Profile to use this title!", show_alert=True)
        return

    current_profile = await async_fetchone(
        "SELECT title FROM profiles WHERE user_id = ?", (user_id,)
    )

    if title not in FREE_TITLES and title not in PREMIUM_TITLES:
        await query.answer("Invalid title selection.", show_alert=True)
        return

    await async_execute(
        "UPDATE profiles SET title = ? WHERE user_id = ?",
        (title, user_id),
    )

    old_title = current_profile["title"] if current_profile else "New Player"
    text = (
        f"✅ <b>Title Updated!</b>\n\n"
        f"Old: {old_title}\n"
        f"New: <b>{title}</b>\n\n"
        f"Your profile has been updated."
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 View Profile", callback_data="profile_view")],
        [InlineKeyboardButton("🏆 More Titles", callback_data="profile_set_title")],
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _show_badge_selection(query, context) -> None:
    """Show badge selection menu."""
    user_id = query.from_user.id
    is_premium = await _is_premium(user_id)

    current_profile = await async_fetchone(
        "SELECT badge FROM profiles WHERE user_id = ?", (user_id,)
    )
    current_badge = current_profile["badge"] if current_profile else "🟢"

    text = (
        "🎖 <b>Choose Your Badge</b>\n\n"
        f"Current badge: {current_badge}\n\n"
    )

    if is_premium:
        text += "⭐ All badges are available (Premium Member)\n\n"
        available = AVAILABLE_BADGES
    else:
        text += "🔒 Premium badges require Premium Profile Pack\n\n"
        available = [b for b in AVAILABLE_BADGES if b not in PREMIUM_BADGES]

    text += "Select a badge:"

    keyboard_rows = []
    for i in range(0, len(available), 5):
        row = []
        for j in range(5):
            if i + j < len(available):
                badge = available[i + j]
                marker = " ✓" if badge == current_badge else ""
                row.append(InlineKeyboardButton(
                    f"{badge}{marker}",
                    callback_data=f"profile_badge_select:{badge}",
                ))
        keyboard_rows.append(row)

    keyboard_rows.append([
        InlineKeyboardButton("🔙 Back to Profile", callback_data="profile_view"),
    ])

    keyboard = InlineKeyboardMarkup(keyboard_rows)

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _set_badge(query, context, badge: str) -> None:
    """Set the user's profile badge."""
    user_id = query.from_user.id

    if badge not in AVAILABLE_BADGES:
        await query.answer("Invalid badge selection.", show_alert=True)
        return

    is_premium = await _is_premium(user_id)
    if badge in PREMIUM_BADGES and not is_premium:
        await query.answer("You need Premium Profile to use this badge!", show_alert=True)
        return

    await async_execute(
        "UPDATE profiles SET badge = ? WHERE user_id = ?",
        (badge, user_id),
    )

    text = (
        f"✅ <b>Badge Updated!</b>\n\n"
        f"Your new badge: {badge}\n\n"
        f"Your profile has been updated."
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 View Profile", callback_data="profile_view")],
        [InlineKeyboardButton("🎖 More Badges", callback_data="profile_set_badge")],
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _show_detailed_stats(query, context) -> None:
    """Show detailed player statistics."""
    user_id = query.from_user.id

    profile = await async_fetchone(
        "SELECT wins, losses, games_played FROM profiles WHERE user_id = ?",
        (user_id,),
    )

    if not profile:
        await query.answer("Profile not found.", show_alert=True)
        return

    wins = profile["wins"]
    losses = profile["losses"]
    games = profile["games_played"]
    win_rate = (wins / games * 100) if games > 0 else 0.0

    wallet = await async_fetchone("SELECT balance FROM wallets WHERE user_id = ?", (user_id,))
    balance = wallet["balance"] if wallet else 0.0

    total_earned = await async_fetchone(
        "SELECT COALESCE(SUM(amount), 0) as total FROM transactions "
        "WHERE user_id = ? AND type = 'game_win'",
        (user_id,),
    )
    earned = total_earned["total"] if total_earned else 0.0

    total_spent = await async_fetchone(
        "SELECT COALESCE(SUM(ABS(amount)), 0) as total FROM transactions "
        "WHERE user_id = ? AND type IN ('purchase', 'game_entry', 'promotion')",
        (user_id,),
    )
    spent = total_spent["total"] if total_spent else 0.0

    total_deposited = await async_fetchone(
        "SELECT COALESCE(SUM(amount), 0) as total FROM transactions "
        "WHERE user_id = ? AND type = 'deposit'",
        (user_id,),
    )
    deposited = total_deposited["total"] if total_deposited else 0.0

    total_withdrawn = await async_fetchone(
        "SELECT COALESCE(SUM(ABS(amount)), 0) as total FROM transactions "
        "WHERE user_id = ? AND type = 'withdrawal'",
        (user_id,),
    )
    withdrawn = total_withdrawn["total"] if total_withdrawn else 0.0

    referral_count = await async_fetchone(
        "SELECT COUNT(*) as cnt FROM referrals WHERE referrer_id = ?",
        (user_id,),
    )
    referrals = referral_count["cnt"] if referral_count else 0

    purchase_count = await async_fetchone(
        "SELECT COUNT(*) as cnt FROM purchases WHERE user_id = ? AND status = 'completed'",
        (user_id,),
    )
    purchases = purchase_count["cnt"] if purchase_count else 0

    promotion_count = await async_fetchone(
        "SELECT COUNT(*) as cnt FROM promotions WHERE user_id = ?",
        (user_id,),
    )
    promotions = promotion_count["cnt"] if promotion_count else 0

    streak = await _calculate_win_streak(user_id)

    text = (
        f"📈 <b>Detailed Statistics</b>\n\n"
        f"🎮 <b>Game Stats</b>\n"
        f"  Total Games: <b>{games}</b>\n"
        f"  Wins: <b>{wins}</b>\n"
        f"  Losses: <b>{losses}</b>\n"
        f"  Win Rate: <b>{win_rate:.1f}%</b>\n"
        f"  Current Win Streak: <b>{streak}</b>\n\n"
        f"💰 <b>Financial Stats</b>\n"
        f"  Current Balance: <b>{balance:.2f} {CURRENCY_NAME}</b>\n"
        f"  Total Earned (Wins): <b>{earned:.2f} {CURRENCY_NAME}</b>\n"
        f"  Total Spent: <b>{spent:.2f} {CURRENCY_NAME}</b>\n"
        f"  Total Deposited: <b>{deposited:.2f} {CURRENCY_NAME}</b>\n"
        f"  Total Withdrawn: <b>{withdrawn:.2f} {CURRENCY_NAME}</b>\n\n"
        f"👥 <b>Community Stats</b>\n"
        f"  Referrals: <b>{referrals}</b>\n"
        f"  Purchases: <b>{purchases}</b>\n"
        f"  Promotions: <b>{promotions}</b>\n"
    )

    if win_rate >= 70 and games >= 10:
        text += "\n🏆 <b>Badge Unlocked: Elite Player</b>"
    if games >= 100:
        text += "\n🎮 <b>Badge Unlocked: Veteran</b>"
    if referrals >= 10:
        text += "\n👥 <b>Badge Unlocked: Social Butterfly</b>"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 View Profile", callback_data="profile_view")],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="back_to_main")],
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _is_premium(user_id: int) -> bool:
    """Check if a user has premium profile."""
    result = await async_fetchone(
        "SELECT id FROM owned_features WHERE user_id = ? AND feature = 'premium_profile'",
        (user_id,),
    )
    return result is not None


async def _calculate_win_streak(user_id: int) -> int:
    """Calculate the current win streak for a user."""
    recent_games = await async_fetchall(
        "SELECT type FROM transactions "
        "WHERE user_id = ? AND type IN ('game_win', 'game_entry') "
        "ORDER BY created_at DESC LIMIT 20",
        (user_id,),
    )

    streak = 0
    for game in recent_games:
        if game["type"] == "game_win":
            streak += 1
        else:
            break

    return streak
