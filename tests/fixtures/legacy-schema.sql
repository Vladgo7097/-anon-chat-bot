-- Schema generated from Git a650edd, no production data.

CREATE TABLE users (
	id SERIAL NOT NULL, 
	telegram_id BIGINT NOT NULL, 
	anon_id VARCHAR(16) NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	username VARCHAR(255), 
	first_name VARCHAR(255), 
	gender VARCHAR(1), 
	age INTEGER, 
	bio TEXT, 
	profile_photo VARCHAR(512), 
	extra_photos TEXT, 
	chats_count INTEGER NOT NULL, 
	likes_received INTEGER NOT NULL, 
	dislikes_received INTEGER NOT NULL, 
	total_chat_seconds INTEGER NOT NULL, 
	is_premium BOOLEAN NOT NULL, 
	premium_expires TIMESTAMP WITHOUT TIME ZONE, 
	referrer_id BIGINT, 
	referrals_count INTEGER NOT NULL, 
	referrals_activated INTEGER NOT NULL, 
	streak_days INTEGER NOT NULL, 
	last_active_date DATE, 
	streak_rewards_claimed TEXT NOT NULL, 
	positive_rating_pct FLOAT NOT NULL, 
	last_online TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	is_banned BOOLEAN NOT NULL, 
	ban_expires TIMESTAMP WITHOUT TIME ZONE, 
	settings_gender_filter VARCHAR(1) NOT NULL, 
	settings_age_min INTEGER NOT NULL, 
	settings_age_max INTEGER NOT NULL, 
	total_stars_spent INTEGER NOT NULL, 
	notification_settings TEXT NOT NULL, 
	PRIMARY KEY (id)
)

;
CREATE UNIQUE INDEX ix_users_telegram_id ON users (telegram_id);
CREATE UNIQUE INDEX ix_users_anon_id ON users (anon_id);

CREATE TABLE chats (
	id SERIAL NOT NULL, 
	user1_id BIGINT NOT NULL, 
	user2_id BIGINT NOT NULL, 
	started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	ended_at TIMESTAMP WITHOUT TIME ZONE, 
	duration_seconds INTEGER NOT NULL, 
	user1_rating VARCHAR(1), 
	user2_rating VARCHAR(1), 
	reveal_requested_by TEXT, 
	reveal_mutual BOOLEAN NOT NULL, 
	PRIMARY KEY (id)
)

;
CREATE INDEX ix_chats_user1_id ON chats (user1_id);
CREATE INDEX ix_chats_user2_id ON chats (user2_id);

CREATE TABLE chat_messages (
	id SERIAL NOT NULL, 
	chat_id INTEGER NOT NULL, 
	sender_id BIGINT NOT NULL, 
	content_type VARCHAR(32) NOT NULL, 
	content TEXT NOT NULL, 
	sent_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
)

;
CREATE INDEX ix_chat_messages_chat_id ON chat_messages (chat_id);

CREATE TABLE reports (
	id SERIAL NOT NULL, 
	reporter_id BIGINT NOT NULL, 
	reported_id BIGINT NOT NULL, 
	chat_id INTEGER, 
	reason VARCHAR(64) NOT NULL, 
	message_content TEXT, 
	status VARCHAR(16) NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	resolved_at TIMESTAMP WITHOUT TIME ZONE, 
	resolved_by BIGINT, 
	PRIMARY KEY (id)
)

;
CREATE INDEX ix_reports_reported_id ON reports (reported_id);
CREATE INDEX ix_reports_reporter_id ON reports (reporter_id);

CREATE TABLE bans (
	id SERIAL NOT NULL, 
	user_id BIGINT NOT NULL, 
	reason VARCHAR(64) NOT NULL, 
	duration_hours INTEGER, 
	banned_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITHOUT TIME ZONE, 
	banned_by BIGINT, 
	report_id INTEGER, 
	is_active BOOLEAN NOT NULL, 
	PRIMARY KEY (id)
)

;
CREATE INDEX ix_bans_user_id ON bans (user_id);

CREATE TABLE payments (
	id SERIAL NOT NULL, 
	user_id BIGINT NOT NULL, 
	product VARCHAR(64) NOT NULL, 
	stars_amount INTEGER NOT NULL, 
	telegram_payment_id VARCHAR(255), 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	PRIMARY KEY (id)
)

;
CREATE INDEX ix_payments_user_id ON payments (user_id);

CREATE TABLE premium_subscriptions (
	id SERIAL NOT NULL, 
	user_id BIGINT NOT NULL, 
	plan VARCHAR(32) NOT NULL, 
	stars_paid INTEGER NOT NULL, 
	started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	auto_renew BOOLEAN NOT NULL, 
	PRIMARY KEY (id)
)

;
CREATE INDEX ix_premium_subscriptions_user_id ON premium_subscriptions (user_id);