"""
Complete SQL schema for the Telegram multiplayer game platform.

All 19 tables with proper types, constraints, defaults, and indexes.
Applied as a single transaction via init_db().
"""

SCHEMA_SQL = """
-- ============================================================
-- USERS
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id         INTEGER NOT NULL UNIQUE,
    username            TEXT    DEFAULT '',
    first_name          TEXT    DEFAULT '',
    joined_at           TEXT    NOT NULL DEFAULT (datetime('now')),
    last_active         TEXT    NOT NULL DEFAULT (datetime('now')),
    is_banned           INTEGER NOT NULL DEFAULT 0,
    ban_reason          TEXT    DEFAULT NULL,
    referral_code       TEXT    NOT NULL UNIQUE,
    referred_by         TEXT    DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_telegram_id  ON users (telegram_id);
CREATE INDEX IF NOT EXISTS idx_users_username     ON users (username);
CREATE INDEX IF NOT EXISTS idx_users_referral_code ON users (referral_code);
CREATE INDEX IF NOT EXISTS idx_users_is_banned    ON users (is_banned);

-- ============================================================
-- WALLETS
-- ============================================================
CREATE TABLE IF NOT EXISTS wallets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL UNIQUE,
    balance     REAL    NOT NULL DEFAULT 0.0 CHECK (balance >= 0),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_wallets_user_id ON wallets (user_id);

-- ============================================================
-- TRANSACTIONS
-- ============================================================
CREATE TABLE IF NOT EXISTS transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    type            TEXT    NOT NULL,  -- credit, debit, reward, withdrawal, purchase, referral, promotion, admin_adjust
    amount          REAL    NOT NULL,
    description     TEXT    DEFAULT '',
    reference_id    TEXT    DEFAULT NULL,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_transactions_user_id    ON transactions (user_id);
CREATE INDEX IF NOT EXISTS idx_transactions_type       ON transactions (type);
CREATE INDEX IF NOT EXISTS idx_transactions_created_at ON transactions (created_at);
CREATE INDEX IF NOT EXISTS idx_transactions_reference  ON transactions (reference_id);

-- ============================================================
-- GAMES
-- ============================================================
CREATE TABLE IF NOT EXISTS games (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    slug                TEXT    NOT NULL UNIQUE,
    name                TEXT    NOT NULL,
    creator             TEXT    NOT NULL DEFAULT '',
    description         TEXT    DEFAULT '',
    version             TEXT    NOT NULL DEFAULT '1.0.0',
    game_type           TEXT    NOT NULL DEFAULT 'board',  -- board, card, trivia, action, strategy
    min_players         INTEGER NOT NULL DEFAULT 2,
    max_players         INTEGER NOT NULL DEFAULT 10,
    board_rows          INTEGER DEFAULT NULL,
    board_cols          INTEGER DEFAULT NULL,
    turn_based          INTEGER NOT NULL DEFAULT 1,
    single_message_only INTEGER NOT NULL DEFAULT 0,
    win_condition       TEXT    DEFAULT '',
    reward_sar          REAL    NOT NULL DEFAULT 0.0,
    entry_fee_sar       REAL    NOT NULL DEFAULT 0.0,
    is_approved         INTEGER NOT NULL DEFAULT 0,
    is_active           INTEGER NOT NULL DEFAULT 1,
    manifest_json       TEXT    DEFAULT '{}',
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_games_slug       ON games (slug);
CREATE INDEX IF NOT EXISTS idx_games_game_type  ON games (game_type);
CREATE INDEX IF NOT EXISTS idx_games_is_active  ON games (is_active);
CREATE INDEX IF NOT EXISTS idx_games_is_approved ON games (is_approved);

-- ============================================================
-- GAME SESSIONS
-- ============================================================
CREATE TABLE IF NOT EXISTS game_sessions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id             INTEGER NOT NULL,
    room_id             TEXT    NOT NULL UNIQUE,
    chat_id             INTEGER NOT NULL,
    message_id          INTEGER DEFAULT NULL,
    mode                TEXT    NOT NULL DEFAULT 'public',  -- public, private
    visibility          TEXT    NOT NULL DEFAULT 'open',    -- open, closed
    status              TEXT    NOT NULL DEFAULT 'waiting', -- waiting, active, paused, completed, cancelled
    current_turn_index INTEGER NOT NULL DEFAULT 0,
    current_phase       TEXT    NOT NULL DEFAULT 'lobby',   -- lobby, playing, finished
    entry_fee           REAL    NOT NULL DEFAULT 0.0,
    reward_pool         REAL    NOT NULL DEFAULT 0.0,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    started_at          TEXT    DEFAULT NULL,
    ended_at            TEXT    DEFAULT NULL,
    FOREIGN KEY (game_id) REFERENCES games (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_game_sessions_game_id    ON game_sessions (game_id);
CREATE INDEX IF NOT EXISTS idx_game_sessions_room_id    ON game_sessions (room_id);
CREATE INDEX IF NOT EXISTS idx_game_sessions_chat_id    ON game_sessions (chat_id);
CREATE INDEX IF NOT EXISTS idx_game_sessions_status     ON game_sessions (status);
CREATE INDEX IF NOT EXISTS idx_game_sessions_created_at ON game_sessions (created_at);

-- ============================================================
-- GAME PLAYERS
-- ============================================================
CREATE TABLE IF NOT EXISTS game_players (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    player_index    INTEGER NOT NULL DEFAULT 0,
    role            TEXT    NOT NULL DEFAULT 'player',  -- player, spectator, moderator
    is_alive        INTEGER NOT NULL DEFAULT 1,
    score           REAL    NOT NULL DEFAULT 0.0,
    joined_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES game_sessions (id) ON DELETE CASCADE,
    FOREIGN KEY (user_id)    REFERENCES users (id) ON DELETE CASCADE,
    UNIQUE (session_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_game_players_session_id  ON game_players (session_id);
CREATE INDEX IF NOT EXISTS idx_game_players_user_id     ON game_players (user_id);

-- ============================================================
-- GAME ACTIONS
-- ============================================================
CREATE TABLE IF NOT EXISTS game_actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    action      TEXT    NOT NULL,
    data_json   TEXT    DEFAULT '{}',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES game_sessions (id) ON DELETE CASCADE,
    FOREIGN KEY (user_id)    REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_game_actions_session_id ON game_actions (session_id);
CREATE INDEX IF NOT EXISTS idx_game_actions_user_id    ON game_actions (user_id);
CREATE INDEX IF NOT EXISTS idx_game_actions_created_at ON game_actions (created_at);

-- ============================================================
-- STORE ITEMS
-- ============================================================
CREATE TABLE IF NOT EXISTS store_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    item_type       TEXT    NOT NULL,  -- title, badge, theme, powerup, skin, feature
    name            TEXT    NOT NULL,
    description     TEXT    DEFAULT '',
    price_sar       REAL    NOT NULL CHECK (price_sar >= 0),
    is_active       INTEGER NOT NULL DEFAULT 1,
    metadata_json   TEXT    DEFAULT '{}',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_store_items_item_type ON store_items (item_type);
CREATE INDEX IF NOT EXISTS idx_store_items_is_active ON store_items (is_active);

-- ============================================================
-- PURCHASES
-- ============================================================
CREATE TABLE IF NOT EXISTS purchases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    item_id         INTEGER NOT NULL,
    price_paid      REAL    NOT NULL,
    metadata_json   TEXT    DEFAULT '{}',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY (item_id) REFERENCES store_items (id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_purchases_user_id  ON purchases (user_id);
CREATE INDEX IF NOT EXISTS idx_purchases_item_id  ON purchases (item_id);
CREATE INDEX IF NOT EXISTS idx_purchases_created_at ON purchases (created_at);

-- ============================================================
-- PROMOTIONS
-- ============================================================
CREATE TABLE IF NOT EXISTS promotions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    channel_link    TEXT    NOT NULL,
    price_sar       REAL    NOT NULL CHECK (price_sar >= 0),
    duration_hours  INTEGER NOT NULL DEFAULT 24 CHECK (duration_hours > 0),
    status          TEXT    NOT NULL DEFAULT 'pending',  -- pending, active, expired, cancelled
    started_at      TEXT    DEFAULT NULL,
    expires_at      TEXT    DEFAULT NULL,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_promotions_user_id  ON promotions (user_id);
CREATE INDEX IF NOT EXISTS idx_promotions_status   ON promotions (status);
CREATE INDEX IF NOT EXISTS idx_promotions_expires  ON promotions (expires_at);

-- ============================================================
-- PROMOTION QUEUE
-- ============================================================
CREATE TABLE IF NOT EXISTS promotion_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    promotion_id    INTEGER NOT NULL,
    position        INTEGER NOT NULL DEFAULT 0,
    status          TEXT    NOT NULL DEFAULT 'waiting',  -- waiting, active, completed, cancelled
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (promotion_id) REFERENCES promotions (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_promotion_queue_promotion_id ON promotion_queue (promotion_id);
CREATE INDEX IF NOT EXISTS idx_promotion_queue_status       ON promotion_queue (status);
CREATE INDEX IF NOT EXISTS idx_promotion_queue_position     ON promotion_queue (position);

-- ============================================================
-- WITHDRAWALS
-- ============================================================
CREATE TABLE IF NOT EXISTS withdrawals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    amount          REAL    NOT NULL CHECK (amount > 0),
    method          TEXT    NOT NULL,  -- Western Union, PayPal, Crypto
    account_details TEXT    NOT NULL DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'pending',  -- pending, approved, rejected, processed
    admin_note      TEXT    DEFAULT NULL,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    reviewed_at     TEXT    DEFAULT NULL,
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_withdrawals_user_id    ON withdrawals (user_id);
CREATE INDEX IF NOT EXISTS idx_withdrawals_status     ON withdrawals (status);
CREATE INDEX IF NOT EXISTS idx_withdrawals_created_at ON withdrawals (created_at);

-- ============================================================
-- PROFILES
-- ============================================================
CREATE TABLE IF NOT EXISTS profiles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL UNIQUE,
    title           TEXT    DEFAULT NULL,
    badge           TEXT    DEFAULT NULL,
    bio             TEXT    DEFAULT '',
    is_premium      INTEGER NOT NULL DEFAULT 0,
    featured_until  TEXT    DEFAULT NULL,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_profiles_user_id     ON profiles (user_id);
CREATE INDEX IF NOT EXISTS idx_profiles_is_premium   ON profiles (is_premium);
CREATE INDEX IF NOT EXISTS idx_profiles_featured_until ON profiles (featured_until);

-- ============================================================
-- ADMIN LOGS
-- ============================================================
CREATE TABLE IF NOT EXISTS admin_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id    INTEGER NOT NULL,  -- Telegram user ID of admin (not FK - admins may not be registered users)
    action      TEXT    NOT NULL,
    target_type TEXT    DEFAULT NULL,  -- user, game, session, withdrawal, promotion, etc.
    target_id   INTEGER DEFAULT NULL,
    details     TEXT    DEFAULT '',
    metadata    TEXT    DEFAULT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_admin_logs_admin_id    ON admin_logs (admin_id);
CREATE INDEX IF NOT EXISTS idx_admin_logs_action      ON admin_logs (action);
CREATE INDEX IF NOT EXISTS idx_admin_logs_target      ON admin_logs (target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_admin_logs_created_at  ON admin_logs (created_at);

-- ============================================================
-- REQUIRED CHANNELS
-- ============================================================
CREATE TABLE IF NOT EXISTS required_channels (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id       INTEGER NOT NULL UNIQUE,
    channel_username TEXT    DEFAULT '',
    channel_name     TEXT    NOT NULL DEFAULT '',
    is_enabled       INTEGER NOT NULL DEFAULT 1,
    position         INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_required_channels_channel_id    ON required_channels (channel_id);
CREATE INDEX IF NOT EXISTS idx_required_channels_is_enabled    ON required_channels (is_enabled);
CREATE INDEX IF NOT EXISTS idx_required_channels_position      ON required_channels (position);

-- ============================================================
-- SESSIONS (user session state)
-- ============================================================
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    telegram_id INTEGER NOT NULL,
    data_json   TEXT    DEFAULT '{}',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    expires_at  TEXT    NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id     ON sessions (user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_telegram_id ON sessions (telegram_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at  ON sessions (expires_at);

-- ============================================================
-- REWARD CLAIMS
-- ============================================================
CREATE TABLE IF NOT EXISTS reward_claims (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    claim_type  TEXT    NOT NULL,  -- win, share, participation, referral, daily, admin
    reference_id TEXT   DEFAULT NULL,
    amount      REAL    NOT NULL CHECK (amount > 0),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reward_claims_user_id    ON reward_claims (user_id);
CREATE INDEX IF NOT EXISTS idx_reward_claims_claim_type ON reward_claims (claim_type);
CREATE INDEX IF NOT EXISTS idx_reward_claims_created_at ON reward_claims (created_at);
CREATE INDEX IF NOT EXISTS idx_reward_claims_reference  ON reward_claims (reference_id);

-- ============================================================
-- REFERRALS
-- ============================================================
CREATE TABLE IF NOT EXISTS referrals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_id     INTEGER NOT NULL,
    referred_id     INTEGER NOT NULL,
    reward_given    INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (referrer_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY (referred_id) REFERENCES users (id) ON DELETE CASCADE,
    UNIQUE (referrer_id, referred_id)
);

CREATE INDEX IF NOT EXISTS idx_referrals_referrer_id ON referrals (referrer_id);
CREATE INDEX IF NOT EXISTS idx_referrals_referred_id ON referrals (referred_id);

-- ============================================================
-- OWNED FEATURES
-- ============================================================
CREATE TABLE IF NOT EXISTS owned_features (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    feature_type    TEXT    NOT NULL,  -- title, badge, theme, skin
    feature_id      TEXT    NOT NULL,
    expires_at      TEXT    DEFAULT NULL,  -- NULL = permanent
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    UNIQUE (user_id, feature_type, feature_id)
);

CREATE INDEX IF NOT EXISTS idx_owned_features_user_id       ON owned_features (user_id);
CREATE INDEX IF NOT EXISTS idx_owned_features_feature_type  ON owned_features (feature_type);
CREATE INDEX IF NOT EXISTS idx_owned_features_expires_at    ON owned_features (expires_at);

-- ============================================================
-- GAME OWNERSHIP
-- ============================================================
CREATE TABLE IF NOT EXISTS game_ownership (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id   INTEGER NOT NULL,
    game_slug       TEXT    NOT NULL,
    creator_name    TEXT    NOT NULL DEFAULT '',
    rights_status   TEXT    NOT NULL DEFAULT 'owned',  -- owned, licensed, disputed, revoked
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (owner_user_id) REFERENCES users (id) ON DELETE CASCADE,
    FOREIGN KEY (game_slug)     REFERENCES games (slug) ON DELETE CASCADE,
    UNIQUE (owner_user_id, game_slug)
);

CREATE INDEX IF NOT EXISTS idx_game_ownership_owner     ON game_ownership (owner_user_id);
CREATE INDEX IF NOT EXISTS idx_game_ownership_slug      ON game_ownership (game_slug);
CREATE INDEX IF NOT EXISTS idx_game_ownership_rights    ON game_ownership (rights_status);

-- ============================================================
-- BUILDER SESSIONS
-- ============================================================
CREATE TABLE IF NOT EXISTS builder_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT    NOT NULL UNIQUE,
    user_id         INTEGER NOT NULL,
    chat_id         INTEGER NOT NULL,
    message_id      INTEGER DEFAULT NULL,
    current_step    TEXT    NOT NULL DEFAULT 'HOME',
    config_json     TEXT    NOT NULL DEFAULT '{}',
    status          TEXT    NOT NULL DEFAULT 'active',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_builder_sessions_session_id ON builder_sessions (session_id);
CREATE INDEX IF NOT EXISTS idx_builder_sessions_user_id    ON builder_sessions (user_id);
CREATE INDEX IF NOT EXISTS idx_builder_sessions_status     ON builder_sessions (status);

-- ============================================================
-- GAME DRAFTS
-- ============================================================
CREATE TABLE IF NOT EXISTS game_drafts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    session_id      TEXT    DEFAULT NULL,
    config_json     TEXT    NOT NULL DEFAULT '{}',
    current_step    TEXT    NOT NULL DEFAULT 'HOME',
    status          TEXT    NOT NULL DEFAULT 'active',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_game_drafts_user_id    ON game_drafts (user_id);
CREATE INDEX IF NOT EXISTS idx_game_drafts_status     ON game_drafts (status);

-- ============================================================
-- GAME REGISTRY
-- ============================================================
CREATE TABLE IF NOT EXISTS game_registry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    slug            TEXT    NOT NULL UNIQUE,
    name            TEXT    NOT NULL,
    creator         TEXT    NOT NULL DEFAULT '',
    version         TEXT    NOT NULL DEFAULT '1.0.0',
    game_type       TEXT    NOT NULL DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'pending',
    file_path       TEXT    DEFAULT '',
    publish_state   TEXT    NOT NULL DEFAULT 'draft',
    validation_state TEXT   NOT NULL DEFAULT 'pending',
    is_active       INTEGER NOT NULL DEFAULT 1,
    manifest_json   TEXT    DEFAULT '{}',
    last_updated    TEXT    NOT NULL DEFAULT (datetime('now')),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    owner_user_id   INTEGER DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_game_registry_slug     ON game_registry (slug);
CREATE INDEX IF NOT EXISTS idx_game_registry_status   ON game_registry (status);
CREATE INDEX IF NOT EXISTS idx_game_registry_publish  ON game_registry (publish_state);
CREATE INDEX IF NOT EXISTS idx_game_registry_owner    ON game_registry (owner_user_id);
"""
