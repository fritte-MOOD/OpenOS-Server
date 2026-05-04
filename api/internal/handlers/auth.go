package handlers

import (
	"context"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/homeserver/api/internal/models"
)

// ValidateSession checks a session token against the shared PostgreSQL database.
// This is the same Session table used by the Global Stack client (Prisma schema).
func ValidateSession(ctx context.Context, db *pgxpool.Pool, token string) (*models.User, error) {
	var user models.User
	var expiresAt time.Time

	err := db.QueryRow(ctx, `
		SELECT u.id, u.username, u.name, u.email, s."expiresAt"
		FROM "Session" s
		JOIN "User" u ON u.id = s."userId"
		WHERE s.token = $1
	`, token).Scan(&user.ID, &user.Username, &user.Name, &user.Email, &expiresAt)
	if err != nil {
		return nil, fmt.Errorf("session not found: %w", err)
	}

	if time.Now().After(expiresAt) {
		return nil, fmt.Errorf("session expired")
	}

	return &user, nil
}
