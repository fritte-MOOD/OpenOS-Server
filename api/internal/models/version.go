package models

type Generation struct {
	Number  int    `json:"generation"`
	Date    string `json:"date"`
	Current bool   `json:"current"`
}

type Version struct {
	Version string `json:"version"`
	Date    string `json:"date"`
	Commit  string `json:"commit"`
	Stable  bool   `json:"stable"`
	Current bool   `json:"current"`
}

type UpdateStatus struct {
	Channel          string `json:"channel"`
	CurrentRef       string `json:"currentRef"`
	CurrentVersion   string `json:"currentVersion"`
	LatestRef        string `json:"latestRef"`
	LatestVersion    string `json:"latestVersion"`
	UpdateAvailable  bool   `json:"updateAvailable"`
	CheckedAt        string `json:"checkedAt"`
	StagedUpdate     string `json:"stagedUpdate,omitempty"`
}

type UpgradeRequest struct {
	Version string `json:"version"`
}

type RollbackRequest struct {
	Generation int `json:"generation"`
}

type UpgradeHistoryEntry struct {
	Timestamp  string `json:"timestamp"`
	Generation int    `json:"generation"`
	Version    string `json:"version"`
	Status     string `json:"status"`
}
