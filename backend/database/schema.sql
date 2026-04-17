-- ══════════════════════════════════════════
--  StudyBot — MySQL Database Schema
--  Run this in MySQL Workbench or command line
--  Command: mysql -u root -p < schema.sql
-- ══════════════════════════════════════════

CREATE DATABASE IF NOT EXISTS studybot_db
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE studybot_db;

-- ── Users ─────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    name          VARCHAR(100)  NOT NULL,
    email         VARCHAR(150)  NOT NULL UNIQUE,
    password_hash VARCHAR(255)  DEFAULT NULL,
    google_id     VARCHAR(150)  DEFAULT NULL UNIQUE,
    avatar_url    VARCHAR(500)  DEFAULT NULL,
    role          ENUM('student','admin') DEFAULT 'student',
    is_active     TINYINT(1)    DEFAULT 1,
    created_at    DATETIME      DEFAULT CURRENT_TIMESTAMP,
    last_login    DATETIME      DEFAULT NULL,
    INDEX idx_email    (email),
    INDEX idx_google   (google_id)
);

-- ── Study Materials ───────────────────────
CREATE TABLE IF NOT EXISTS study_materials (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    user_id        INT          NOT NULL,
    title          VARCHAR(255) NOT NULL,
    subject        VARCHAR(100) DEFAULT '',
    filename       VARCHAR(255) NOT NULL,
    file_path      VARCHAR(500) NOT NULL,
    file_type      ENUM('pdf','docx','txt','image') NOT NULL,
    file_size      INT          DEFAULT 0,
    extracted_text LONGTEXT     DEFAULT NULL,
    key_topics     TEXT         DEFAULT NULL,
    created_at     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_user_mat (user_id)
);

CREATE TABLE IF NOT EXISTS subjects (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    user_id    INT          NOT NULL,
    name       VARCHAR(150) NOT NULL,
    created_at DATETIME     DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uniq_user_subject (user_id, name),
    INDEX idx_subject_user (user_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

ALTER TABLE study_materials
    ADD COLUMN IF NOT EXISTS subject_id INT NULL,
    ADD INDEX IF NOT EXISTS idx_subject_id (subject_id);

-- ── Chat Sessions ─────────────────────────
CREATE TABLE IF NOT EXISTS chat_sessions (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    user_id      INT          NOT NULL,
    material_id  INT          DEFAULT NULL,
    session_name VARCHAR(255) DEFAULT 'New Chat',
    created_at   DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id)     REFERENCES users(id)            ON DELETE CASCADE,
    FOREIGN KEY (material_id) REFERENCES study_materials(id)  ON DELETE SET NULL,
    INDEX idx_user_sess (user_id)
);

-- ── Chat Messages ─────────────────────────
CREATE TABLE IF NOT EXISTS chat_messages (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    session_id INT          NOT NULL,
    role       ENUM('user','assistant') NOT NULL,
    content    TEXT         NOT NULL,
    source     VARCHAR(50)  DEFAULT 'ai',
    created_at DATETIME     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE,
    INDEX idx_sess_msg (session_id)
);

-- ── Tests ─────────────────────────────────
CREATE TABLE IF NOT EXISTS tests (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    user_id     INT          NOT NULL,
    material_id INT          NOT NULL,
    title       VARCHAR(255) NOT NULL,
    test_type   ENUM('mcq','short_answer','mixed') NOT NULL,
    questions   LONGTEXT     NOT NULL,
    difficulty  ENUM('easy','medium','hard') DEFAULT 'medium',
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id)     REFERENCES users(id)           ON DELETE CASCADE,
    FOREIGN KEY (material_id) REFERENCES study_materials(id) ON DELETE CASCADE
);

-- ── Test Attempts ─────────────────────────
CREATE TABLE IF NOT EXISTS test_attempts (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    test_id         INT            NOT NULL,
    user_id         INT            NOT NULL,
    answers         LONGTEXT       NOT NULL,
    score           DECIMAL(5,2)   DEFAULT 0,
    total_questions INT            DEFAULT 0,
    correct_answers INT            DEFAULT 0,
    time_taken      INT            DEFAULT 0,
    feedback        LONGTEXT       DEFAULT NULL,
    completed_at    DATETIME       DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (test_id) REFERENCES tests(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_user_attempt (user_id)
);

-- ── User Progress ─────────────────────────
CREATE TABLE IF NOT EXISTS user_progress (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    user_id             INT          NOT NULL UNIQUE,
    total_tests         INT          DEFAULT 0,
    avg_score           DECIMAL(5,2) DEFAULT 0,
    materials_uploaded  INT          DEFAULT 0,
    chat_sessions       INT          DEFAULT 0,
    study_streak        INT          DEFAULT 0,
    last_study_date     DATE         DEFAULT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- ── Admin Logs ────────────────────────────
CREATE TABLE IF NOT EXISTS admin_logs (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    admin_id    INT          NOT NULL,
    action      VARCHAR(255) NOT NULL,
    target_type VARCHAR(50)  DEFAULT NULL,
    target_id   INT          DEFAULT NULL,
    details     TEXT         DEFAULT NULL,
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (admin_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ai_usage_counters (
    user_id         INT NOT NULL,
    usage_date      DATE NOT NULL,
    request_count   INT NOT NULL DEFAULT 0,
    quota_hits      INT NOT NULL DEFAULT 0,
    last_request_at DATETIME DEFAULT NULL,
    blocked_until   DATETIME DEFAULT NULL,
    PRIMARY KEY (user_id, usage_date),
    INDEX idx_ai_usage_date (usage_date),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- ══════════════════════════════════════════
-- Admin account
-- Create the admin manually after deployment.
-- ══════════════════════════════════════════
-- Create admin users manually with a unique password.
-- Do not ship a shared default admin account in production.

SELECT 'Database setup complete!' AS Status;
