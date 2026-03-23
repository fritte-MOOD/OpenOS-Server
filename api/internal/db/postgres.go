package db

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5/pgxpool"
)

func Connect(ctx context.Context, host, dbname, user string) (*pgxpool.Pool, error) {
	var connStr string
	if host[0] == '/' {
		connStr = fmt.Sprintf("host=%s dbname=%s user=%s sslmode=disable", host, dbname, user)
	} else {
		connStr = fmt.Sprintf("host=%s dbname=%s user=%s sslmode=disable", host, dbname, user)
	}

	config, err := pgxpool.ParseConfig(connStr)
	if err != nil {
		return nil, fmt.Errorf("parse config: %w", err)
	}

	config.MaxConns = 20
	config.MinConns = 2

	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return nil, fmt.Errorf("create pool: %w", err)
	}

	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("ping: %w", err)
	}

	if err := ensureSchema(ctx, pool); err != nil {
		pool.Close()
		return nil, fmt.Errorf("schema: %w", err)
	}

	return pool, nil
}

func ensureSchema(ctx context.Context, pool *pgxpool.Pool) error {
	schema := `
		CREATE TABLE IF NOT EXISTS invites (
			id          TEXT PRIMARY KEY,
			community_id TEXT NOT NULL,
			role        TEXT NOT NULL DEFAULT 'member',
			token       TEXT UNIQUE NOT NULL,
			created_by  TEXT NOT NULL,
			expires_at  TIMESTAMPTZ NOT NULL,
			used_at     TIMESTAMPTZ,
			used_by     TEXT
		);

		CREATE TABLE IF NOT EXISTS rebuild_log (
			id         SERIAL PRIMARY KEY,
			started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
			finished_at TIMESTAMPTZ,
			status     TEXT NOT NULL DEFAULT 'running',
			output     TEXT,
			triggered_by TEXT
		);
	`
	_, err := pool.Exec(ctx, schema)
	return err
}
