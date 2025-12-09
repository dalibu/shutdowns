-- Migration 004: Add is_human column to user_activity
-- Stores CAPTCHA verification status permanently

ALTER TABLE user_activity ADD COLUMN is_human INTEGER DEFAULT 0;

-- Mark all existing users as human (they already passed CAPTCHA at some point)
UPDATE user_activity SET is_human = 1 WHERE is_human IS NULL OR is_human = 0;
