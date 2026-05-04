package handlers

import (
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/homeserver/api/internal/models"
)

type CommunityHandler struct {
	db *pgxpool.Pool
}

func NewCommunityHandler(db *pgxpool.Pool) *CommunityHandler {
	return &CommunityHandler{db: db}
}

func (h *CommunityHandler) List(w http.ResponseWriter, r *http.Request) {
	rows, err := h.db.Query(r.Context(), `
		SELECT g.id, g.slug, g.name, g.subtitle, g.color, g.icon, g."createdAt",
		       COUNT(m.id) as member_count
		FROM "Group" g
		LEFT JOIN "Membership" m ON m."groupId" = g.id
		WHERE g."parentId" IS NULL AND g."isTemplate" = false
		GROUP BY g.id
		ORDER BY g."createdAt" DESC
	`)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to query communities")
		return
	}
	defer rows.Close()

	var communities []models.Community
	for rows.Next() {
		var c models.Community
		if err := rows.Scan(&c.ID, &c.Slug, &c.Name, &c.Subtitle, &c.Color, &c.Icon, &c.CreatedAt, &c.MemberCount); err != nil {
			continue
		}
		communities = append(communities, c)
	}

	if communities == nil {
		communities = []models.Community{}
	}

	WriteJSON(w, http.StatusOK, communities)
}

func (h *CommunityHandler) Create(w http.ResponseWriter, r *http.Request) {
	var req models.CommunityCreateRequest
	if err := DecodeJSON(r, &req); err != nil {
		WriteError(w, http.StatusBadRequest, "invalid request body")
		return
	}

	if req.Name == "" {
		WriteError(w, http.StatusBadRequest, "name is required")
		return
	}

	slug := slugify(req.Name)
	id := fmt.Sprintf("c_%d", time.Now().UnixNano())

	_, err := h.db.Exec(r.Context(), `
		INSERT INTO "Group" (id, slug, name, subtitle, color, icon, visibility, "isTemplate", "createdAt", "updatedAt")
		VALUES ($1, $2, $3, $4, $5, $6, 'public', false, NOW(), NOW())
	`, id, slug, req.Name, req.Subtitle, req.Color, req.Icon)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to create community")
		return
	}

	WriteJSON(w, http.StatusCreated, models.Community{
		ID:       id,
		Slug:     slug,
		Name:     req.Name,
		Subtitle: req.Subtitle,
		Color:    req.Color,
		Icon:     req.Icon,
	})
}

func (h *CommunityHandler) Members(w http.ResponseWriter, r *http.Request) {
	communityID := r.PathValue("id")
	if communityID == "" {
		WriteError(w, http.StatusBadRequest, "missing community id")
		return
	}

	rows, err := h.db.Query(r.Context(), `
		SELECT u.id, u.name, u.nickname, m.role, m."joinedAt"
		FROM "Membership" m
		JOIN "User" u ON u.id = m."userId"
		WHERE m."groupId" = $1
		ORDER BY m."joinedAt" ASC
	`, communityID)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to query members")
		return
	}
	defer rows.Close()

	var members []models.Member
	for rows.Next() {
		var m models.Member
		var nickname *string
		if err := rows.Scan(&m.ID, &m.Name, &nickname, &m.Role, &m.JoinedAt); err != nil {
			continue
		}
		if nickname != nil {
			m.Nickname = *nickname
		}
		members = append(members, m)
	}

	if members == nil {
		members = []models.Member{}
	}

	WriteJSON(w, http.StatusOK, members)
}

func slugify(s string) string {
	s = strings.ToLower(s)
	s = strings.Map(func(r rune) rune {
		if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') {
			return r
		}
		if r == ' ' || r == '-' || r == '_' {
			return '-'
		}
		return -1
	}, s)
	for strings.Contains(s, "--") {
		s = strings.ReplaceAll(s, "--", "-")
	}
	return strings.Trim(s, "-")
}
