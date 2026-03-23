package models

type App struct {
	ID          string   `json:"id"`
	Name        string   `json:"name"`
	Description string   `json:"description"`
	Icon        string   `json:"icon"`
	Category    string   `json:"category"`
	Version     string   `json:"version"`
	RequiresGPU bool     `json:"requiresGPU"`
	Ports       []int    `json:"ports"`
	Databases   []string `json:"databases"`
	Enabled     bool     `json:"enabled"`
	URL         string   `json:"url"`
	Status      string   `json:"status"` // "running", "stopped", "installing", "error"
}

type AppConfig struct {
	Domain string            `json:"domain,omitempty"`
	Extra  map[string]string `json:"extra,omitempty"`
}

type AppInstallRequest struct {
	Config *AppConfig `json:"config,omitempty"`
}
