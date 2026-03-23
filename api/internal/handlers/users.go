package handlers

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"net/http"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/openos/api/internal/models"
)

type UserHandler struct {
	db *pgxpool.Pool
}

func NewUserHandler(db *pgxpool.Pool) *UserHandler {
	return &UserHandler{db: db}
}

func (h *UserHandler) List(w http.ResponseWriter, r *http.Request) {
	rows, err := h.db.Query(r.Context(), `
		SELECT id, username, name, email, "createdAt"
		FROM "User"
		WHERE "isDemo" = false
		ORDER BY "createdAt" DESC
	`)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to query users")
		return
	}
	defer rows.Close()

	var users []models.User
	for rows.Next() {
		var u models.User
		var email *string
		if err := rows.Scan(&u.ID, &u.Username, &u.Name, &email, &u.CreatedAt); err != nil {
			continue
		}
		if email != nil {
			u.Email = *email
		}
		users = append(users, u)
	}

	if users == nil {
		users = []models.User{}
	}

	WriteJSON(w, http.StatusOK, users)
}

func (h *UserHandler) Invite(w http.ResponseWriter, r *http.Request) {
	var req models.InviteRequest
	if err := DecodeJSON(r, &req); err != nil {
		WriteError(w, http.StatusBadRequest, "invalid request body")
		return
	}

	if req.CommunityID == "" {
		WriteError(w, http.StatusBadRequest, "communityId is required")
		return
	}

	if req.Role == "" {
		req.Role = "member"
	}

	token := generateToken(32)
	id := fmt.Sprintf("inv_%d", time.Now().UnixNano())
	expiresAt := time.Now().Add(7 * 24 * time.Hour)

	_, err := h.db.Exec(r.Context(), `
		INSERT INTO invites (id, community_id, role, token, created_by, expires_at)
		VALUES ($1, $2, $3, $4, $5, $6)
	`, id, req.CommunityID, req.Role, token, "system", expiresAt)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to create invite")
		return
	}

	WriteJSON(w, http.StatusCreated, models.InviteResponse{
		InviteURL: fmt.Sprintf("/invite/%s", token),
		ExpiresAt: expiresAt,
	})
}

func generateToken(length int) string {
	b := make([]byte, length)
	rand.Read(b)
	return hex.EncodeToString(b)
}
