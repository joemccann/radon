-- Promote client-only UI preferences (theme, table column visibility) from
-- localStorage into the per-user profile row so they follow the operator
-- across devices. JSON object, written by web /api/profile PUT.
ALTER TABLE user_profiles ADD COLUMN ui_preferences TEXT;
