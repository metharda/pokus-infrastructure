-- The application role (no password: local peer authentication only) and its database.
SELECT 'CREATE ROLE app LOGIN' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app')\gexec
SELECT 'CREATE DATABASE appdb OWNER app' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'appdb')\gexec
