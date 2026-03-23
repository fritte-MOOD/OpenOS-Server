package models

import "time"

type Community struct {
	ID        string    `json:"id"`
	Slug      string    `json:"slug"`
	Name      string    `json:"name"`
	Subtitle  string    `json:"subtitle"`
	Color     string    `json:"color"`
	Icon      string    `json:"icon"`
	CreatedAt time.Time `json:"createdAt"`
	MemberCount int     `json:"memberCount"`
}

type CommunityCreateRequest struct {
	Name     string `json:"name"`
	Subtitle string `json:"subtitle"`
	Color    string `json:"color"`
	Icon     string `json:"icon"`
}

type Member struct {
	ID       string `json:"id"`
	Name     string `json:"name"`
	Nickname string `json:"nickname,omitempty"`
	Role     string `json:"role"`
	JoinedAt time.Time `json:"joinedAt"`
}
