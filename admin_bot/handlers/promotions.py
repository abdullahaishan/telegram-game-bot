"""
Admin Promotions Handler
Promotion management: list, detail, approve/reject, cancel, queue, rotate.
"""

from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import ADMIN_IDS, CURRENCY_NAME, PROMOTION_MIN_SAR, PROMOTION_MAX_ACTIVE
from database import async_fetchone, async_fetchall, async_execute
from admin_bot.utils import admin_guard, log_admin_action


def _back_to_dashboard_button() -> InlineKeyboardButton:
    return InlineKeyboardButton("🔙 Dashboard", callback_data="admin_dashboard")


def _back_to_promotions_button() -> InlineKeyboardButton:
    return InlineKeyboardButton("📢 Promotions", callback_data="admin_promotions")


async def cb_promotions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all promotions."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    admin_id = update.effective_user.id

    await log_admin_action(admin_id, "promotions_list", details="Viewed promotions list")

    # Count by status
    active_count = await async_fetchone("SELECT COUNT(*) as cnt FROM promotions WHERE status = 'active'")
    pending_count = await async_fetchone("SELECT COUNT(*) as cnt FROM promotions WHERE status = 'pending'")
    expired_count = await async_fetchone("SELECT COUNT(*) as cnt FROM promotions WHERE status = 'expired'")
    rejected_count = await async_fetchone("SELECT COUNT(*) as cnt FROM promotions WHERE status = 'rejected'")

    active = active_count["cnt"] if active_count else 0
    pending = pending_count["cnt"] if pending_count else 0
    expired = expired_count["cnt"] if expired_count else 0
    rejected = rejected_count["cnt"] if rejected_count else 0

    # Get recent promotions
    promos = await async_fetchall(
        """
        SELECT p.*, u.username as creator_name, u.first_name as creator_first_name
        FROM promotions p
        LEFT JOIN users u ON p.user_id = u.id
        ORDER BY
            CASE p.status
                WHEN 'pending' THEN 1
                WHEN 'active' THEN 2
                WHEN 'expired' THEN 3
                WHEN 'rejected' THEN 4
                WHEN 'cancelled' THEN 5
            END,
            p.created_at DESC
        LIMIT 20
        """
    )

    lines = [
        f"📢 *Promotions Overview*\n",
        f"━━━━━━━━━━━━━━━━━━",
        f"✅ Active: {active}/{PROMOTION_MAX_ACTIVE}",
        f"⏳ Pending: {pending}",
        f"⌛ Expired: {expired}",
        f"❌ Rejected: {rejected}",
    ]

    if promos:
        lines.append(f"\n📋 *Recent Promotions:*")
        for p in promos:
            status_emoji = {
                "pending": "⏳", "active": "✅", "expired": "⌛",
                "rejected": "❌", "cancelled": "🚫",
            }.get(p["status"], "❓")
            creator = p.get("creator_first_name") or p.get("creator_name") or "System"
            lines.append(
                f"  {status_emoji} #{p['id']} — {p.get('channel_link', 'N/A')} "
                f"({p.get('price_sar', 0):.2f} SAR) by {creator}"
            )

    text = "\n".join(lines)

    # Build buttons
    promo_buttons = []
    for p in promos[:10]:
        status_emoji = {
            "pending": "⏳", "active": "✅", "expired": "⌛",
            "rejected": "❌", "cancelled": "🚫",
        }.get(p["status"], "❓")
        title = p.get("channel_link", "Untitled")[:15]
        promo_buttons.append(
            InlineKeyboardButton(
                f"{status_emoji} #{p['id']} {title}",
                callback_data=f"admin_promotion_detail:{p['id']}",
            )
        )

    promo_rows = [promo_buttons[i:i + 2] for i in range(0, len(promo_buttons), 2)]

    keyboard = InlineKeyboardMarkup(
        promo_rows + [
            [
                InlineKeyboardButton("📋 Queue", callback_data="admin_promotion_queue"),
                InlineKeyboardButton("🔄 Rotate", callback_data="admin_rotate_promotions"),
            ],
            [_back_to_dashboard_button()],
        ]
    )

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def cb_promotion_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show promotion details."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    promotion_id = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    await log_admin_action(
        admin_id, "promotion_detail", target_type="promotion", target_id=str(promotion_id)
    )

    promo = await async_fetchone(
        """
        SELECT p.*, u.username as creator_name, u.first_name as creator_first_name
        FROM promotions p
        LEFT JOIN users u ON p.user_id = u.id
        WHERE p.id = ?
        """,
        (promotion_id,),
    )

    if not promo:
        await query.edit_message_text(
            "❌ Promotion not found.",
            reply_markup=InlineKeyboardMarkup([[_back_to_promotions_button()]]),
        )
        return

    # Get claim count from promotion_queue
    claims = await async_fetchone(
        "SELECT COUNT(*) as cnt FROM promotion_queue WHERE promotion_id = ? AND status = 'completed'",
        (promotion_id,),
    )
    claim_count = claims["cnt"] if claims else 0

    creator = promo.get("creator_first_name") or promo.get("creator_name") or "System"

    status_emoji = {
        "pending": "⏳", "active": "✅", "expired": "⌛",
        "rejected": "❌", "cancelled": "🚫",
    }.get(promo["status"], "❓")

    text = (
        f"📢 *Promotion Details*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"ID: `{promo['id']}`\n"
        f"Status: {status_emoji} {promo['status'].title()}\n"
        f"Creator: {creator}\n"
        f"Channel Link: {promo.get('channel_link', 'N/A')}\n"
        f"Price: {promo.get('price_sar', 0):.2f} SAR\n"
        f"Duration: {promo.get('duration_hours', 24)} hours\n"
        f"Queue Claims: {claim_count}\n"
        f"Started: {promo.get('started_at', 'N/A')}\n"
        f"Expires: {promo.get('expires_at', 'N/A')}\n"
        f"Created: {promo.get('created_at', 'N/A')}\n"
    )

    # Action buttons based on status
    action_rows = []
    if promo["status"] == "pending":
        action_rows.append([
            InlineKeyboardButton("✅ Approve", callback_data=f"admin_approve_promotion:{promotion_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"admin_reject_promotion:{promotion_id}"),
        ])
    elif promo["status"] == "active":
        action_rows.append([
            InlineKeyboardButton("🚫 Cancel", callback_data=f"admin_cancel_promotion:{promotion_id}"),
        ])

    keyboard = InlineKeyboardMarkup(
        action_rows + [
            [_back_to_promotions_button(), _back_to_dashboard_button()],
        ]
    )

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def cb_approve_promotion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Approve a pending promotion."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    promotion_id = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    promo = await async_fetchone("SELECT * FROM promotions WHERE id = ?", (promotion_id,))
    if not promo:
        await query.answer("❌ Promotion not found.", show_alert=True)
        return

    if promo["status"] != "pending":
        await query.answer("Promotion is not pending.", show_alert=True)
        return

    # Check if we can activate (max active check)
    active_count = await async_fetchone("SELECT COUNT(*) as cnt FROM promotions WHERE status = 'active'")
    current_active = active_count["cnt"] if active_count else 0

    if current_active >= PROMOTION_MAX_ACTIVE:
        # Approve but keep in queue - set started_at so it can be activated later
        await async_execute(
            "UPDATE promotions SET status = 'active', started_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), promotion_id),
        )
        await log_admin_action(
            admin_id, "approve_promotion", target_type="promotion", target_id=str(promotion_id),
            details=f"Approved promotion #{promotion_id}"
        )
        await query.answer()
        await query.edit_message_text(
            f"✅ Promotion #{promotion_id} approved and activated!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Promotion Detail", callback_data=f"admin_promotion_detail:{promotion_id}")],
                [_back_to_promotions_button()],
            ]),
            parse_mode="Markdown",
        )
    else:
        # Activate immediately
        started_at = datetime.utcnow().isoformat()
        from datetime import timedelta
        expires_at = (datetime.utcnow() + timedelta(hours=promo.get("duration_hours", 24))).isoformat()
        await async_execute(
            """
            UPDATE promotions
            SET status = 'active', started_at = ?, expires_at = ?
            WHERE id = ?
            """,
            (started_at, expires_at, promotion_id),
        )
        await log_admin_action(
            admin_id, "approve_promotion", target_type="promotion", target_id=str(promotion_id),
            details=f"Approved and activated promotion #{promotion_id}"
        )
        await query.answer()
        await query.edit_message_text(
            f"✅ Promotion #{promotion_id} approved and activated!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Promotion Detail", callback_data=f"admin_promotion_detail:{promotion_id}")],
                [_back_to_promotions_button()],
            ]),
            parse_mode="Markdown",
        )


async def cb_reject_promotion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reject a pending promotion."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    promotion_id = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    promo = await async_fetchone("SELECT * FROM promotions WHERE id = ?", (promotion_id,))
    if not promo:
        await query.answer("❌ Promotion not found.", show_alert=True)
        return

    await async_execute("UPDATE promotions SET status = 'rejected' WHERE id = ?", (promotion_id,))

    await log_admin_action(
        admin_id, "reject_promotion", target_type="promotion", target_id=str(promotion_id),
        details=f"Rejected promotion #{promotion_id}"
    )

    await query.answer()

    await query.edit_message_text(
        f"❌ Promotion #{promotion_id} has been rejected.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Promotion Detail", callback_data=f"admin_promotion_detail:{promotion_id}")],
            [_back_to_promotions_button()],
        ]),
        parse_mode="Markdown",
    )


async def cb_cancel_promotion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel an active promotion."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    promotion_id = int(query.data.split(":")[1])
    admin_id = update.effective_user.id

    promo = await async_fetchone("SELECT * FROM promotions WHERE id = ?", (promotion_id,))
    if not promo:
        await query.answer("❌ Promotion not found.", show_alert=True)
        return

    if promo["status"] != "active":
        await query.answer("Promotion is not active.", show_alert=True)
        return

    await async_execute(
        "UPDATE promotions SET status = 'cancelled' WHERE id = ?",
        (promotion_id,),
    )

    await log_admin_action(
        admin_id, "cancel_promotion", target_type="promotion", target_id=str(promotion_id),
        details=f"Cancelled promotion #{promotion_id}"
    )

    await query.answer()

    await query.edit_message_text(
        f"🚫 Promotion #{promotion_id} has been cancelled.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Promotions", callback_data="admin_promotions")],
            [_back_to_dashboard_button()],
        ]),
        parse_mode="Markdown",
    )


async def cb_promotion_queue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """View the promotion queue."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    admin_id = update.effective_user.id

    await log_admin_action(admin_id, "promotion_queue", details="Viewed promotion queue")

    # Get queued promotions
    queued = await async_fetchall(
        """
        SELECT p.*, u.username as creator_name, u.first_name as creator_first_name
        FROM promotions p
        LEFT JOIN users u ON p.user_id = u.id
        WHERE p.status = 'pending'
        ORDER BY p.created_at ASC
        """
    )

    # Active count
    active_count = await async_fetchone("SELECT COUNT(*) as cnt FROM promotions WHERE status = 'active'")
    current_active = active_count["cnt"] if active_count else 0

    lines = [
        f"📋 *Promotion Queue*\n",
        f"━━━━━━━━━━━━━━━━━━",
        f"Active: {current_active}/{PROMOTION_MAX_ACTIVE}",
        f"In Queue: {len(queued)}\n",
    ]

    if queued:
        for i, p in enumerate(queued, 1):
            creator = p.get("creator_first_name") or p.get("creator_name") or "System"
            lines.append(
                f"{i}. #{p['id']} — {p.get('channel_link', 'N/A')} "
                f"({p.get('price_sar', 0):.2f} SAR) by {creator}"
            )
    else:
        lines.append("No promotions in queue.")

    text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Rotate", callback_data="admin_rotate_promotions"),
            InlineKeyboardButton("📢 Promotions", callback_data="admin_promotions"),
        ],
        [_back_to_dashboard_button()],
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def cb_rotate_promotions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manually rotate promotions - expire old ones, activate queued ones."""
    query = update.callback_query
    if not await admin_guard(update, context):
        return

    await query.answer()
    admin_id = update.effective_user.id

    await log_admin_action(admin_id, "rotate_promotions", details="Manually triggered promotion rotation")

    # Expire active promotions that have passed their end time
    await async_execute(
        """
        UPDATE promotions
        SET status = 'expired'
        WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at < ?
        """,
        (datetime.utcnow().isoformat(),),
    )

    # Count how many slots are available
    active_count = await async_fetchone("SELECT COUNT(*) as cnt FROM promotions WHERE status = 'active'")
    current_active = active_count["cnt"] if active_count else 0
    available_slots = max(0, PROMOTION_MAX_ACTIVE - current_active)

    activated = 0
    if available_slots > 0:
        # Activate the next promotions in queue
        queued = await async_fetchall(
            """
            SELECT * FROM promotions
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (available_slots,),
        )

        for p in queued:
            from datetime import timedelta
            started_at = datetime.utcnow().isoformat()
            expires_at = (datetime.utcnow() + timedelta(hours=p.get("duration_hours", 24))).isoformat()
            await async_execute(
                """
                UPDATE promotions
                SET status = 'active', started_at = ?, expires_at = ?
                WHERE id = ?
                """,
                (started_at, expires_at, p["id"]),
            )
            activated += 1

    text = (
        f"🔄 *Promotion Rotation Complete*\n\n"
        f"Expired: Promotions past end date\n"
        f"Available Slots: {available_slots}\n"
        f"Newly Activated: {activated}\n"
        f"Current Active: {current_active + activated}/{PROMOTION_MAX_ACTIVE}"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 Queue", callback_data="admin_promotion_queue"),
            InlineKeyboardButton("📢 Promotions", callback_data="admin_promotions"),
        ],
        [_back_to_dashboard_button()],
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
