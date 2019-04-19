CREATE DATABASE fetchlinks;

CREATE TABLE fetchlinks.posts (
idx INT AUTO_INCREMENT PRIMARY KEY,
source VARCHAR(100),
author VARCHAR(500),
description VARCHAR(1500),
direct_link VARCHAR(250),
date_created DATETIME,
unique_id_string VARCHAR(750),
url_1 VARCHAR(2200),
url_2 VARCHAR(2200),
url_3 VARCHAR(2200),
url_4 VARCHAR(2200),
url_5 VARCHAR(2200),
url_6 VARCHAR(2200),
urls_missing BOOLEAN NOT NULL DEFAULT 0,
UNIQUE KEY ukey_unique_id_string (unique_id_string)
);

CREATE INDEX idx_unique_id_string ON fetchlinks.posts(unique_id_string);
ALTER TABLE fetchlinks.posts CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

CREATE TABLE fetchlinks.twitter (
idx INT AUTO_INCREMENT PRIMARY KEY,
last_accessed_id VARCHAR(100),
time_created DATETIME
);

CREATE TABLE fetchlinks.urls (
idx INT AUTO_INCREMENT PRIMARY KEY,
url VARCHAR(4000),
unique_id VARCHAR(64),
UNIQUE KEY ukey_unique_id (unique_id)
);

CREATE INDEX idx_unique_id ON fetchlinks.urls(unique_id);
