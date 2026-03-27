package models

import "time"

type User struct {
	ID        string    `json:"id"`
	Username  string    `json:"username"`
	Name      string    `json:"name"`
	Email     string    `json:"email,omitempty"`
	CreatedAt time.Time `json:"createdAt"`
}

type Session struct {
	ID        string    `json:"id"`
	Token     string    `json:"token"`
	UserID    string    `json:"userId"`
	ExpiresAt time.Time `json:"expiresAt"`
}

type InviteRequest struct {
	CommunityID string `json:"communityId"`
	Role        string `json:"role"`
}

type InviteResponse struct {
	InviteURL string    `json:"inviteUrl"`
	ExpiresAt time.Time `json:"expiresAt"`
}

type SystemStatus struct {
	Version   string `json:"version"`
	Hostname  string `json:"hostname"`
	Uptime    int64  `json:"uptimeSeconds"`
	OS        string `json:"os"`
	Healthy   bool   `json:"healthy"`
	Mode      string `json:"mode"` // "setup" (first boot) or "full"
}

type SystemResources struct {
	CPUUsage    float64       `json:"cpuUsagePercent"`
	MemoryTotal uint64        `json:"memoryTotalBytes"`
	MemoryUsed  uint64        `json:"memoryUsedBytes"`
	DiskTotal   uint64        `json:"diskTotalBytes"`
	DiskUsed    uint64        `json:"diskUsedBytes"`
	GPUs        []GPUInfo     `json:"gpus"`
}

type GPUInfo struct {
	Name        string  `json:"name"`
	MemoryTotal uint64  `json:"memoryTotalBytes"`
	MemoryUsed  uint64  `json:"memoryUsedBytes"`
	Utilization float64 `json:"utilizationPercent"`
}

type NetworkInfo struct {
	TailscaleIP     string   `json:"tailscaleIP"`
	TailscaleStatus string   `json:"tailscaleStatus"`
	ConnectedPeers  []string `json:"connectedPeers"`
	Hostname        string   `json:"hostname"`
}
