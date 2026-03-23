package handlers

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"strings"

	"github.com/openos/api/internal/models"
	"github.com/openos/api/internal/nixgen"
)

type AppHandler struct {
	nixGen       *nixgen.Generator
	registryPath string
}

func NewAppHandler(gen *nixgen.Generator, registryPath string) *AppHandler {
	return &AppHandler{nixGen: gen, registryPath: registryPath}
}

func (h *AppHandler) List(w http.ResponseWriter, r *http.Request) {
	registry, err := h.loadRegistry()
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to load app registry")
		return
	}

	state, err := h.nixGen.ReadState()
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to read app state")
		return
	}

	apps := make([]models.App, 0, len(registry))
	for id, reg := range registry {
		app := models.App{
			ID:          id,
			Name:        reg.Name,
			Description: reg.Description,
			Icon:        reg.Icon,
			Category:    reg.Category,
			Version:     reg.Version,
			RequiresGPU: reg.RequiresGPU,
			Ports:       reg.Ports,
			Databases:   reg.Databases,
			URL:         reg.URL,
		}

		if s, ok := state[id]; ok && s.Enabled {
			app.Enabled = true
			app.Status = serviceStatus(id)
		} else {
			app.Enabled = false
			app.Status = "stopped"
		}

		apps = append(apps, app)
	}

	WriteJSON(w, http.StatusOK, apps)
}

func (h *AppHandler) Install(w http.ResponseWriter, r *http.Request) {
	appID := r.PathValue("id")
	if appID == "" {
		WriteError(w, http.StatusBadRequest, "missing app id")
		return
	}

	registry, err := h.loadRegistry()
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to load registry")
		return
	}

	if _, ok := registry[appID]; !ok {
		WriteError(w, http.StatusNotFound, fmt.Sprintf("app %q not found in registry", appID))
		return
	}

	var req models.AppInstallRequest
	if r.Body != nil {
		_ = DecodeJSON(r, &req)
	}

	config := make(map[string]string)
	if req.Config != nil {
		if req.Config.Domain != "" {
			config["domain"] = req.Config.Domain
		}
		for k, v := range req.Config.Extra {
			config[k] = v
		}
	}

	if err := h.nixGen.EnableApp(appID, config); err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to enable app in Nix config")
		return
	}

	result, err := h.nixGen.Rebuild(r.Context())
	if err != nil {
		WriteJSON(w, http.StatusInternalServerError, map[string]interface{}{
			"error":  "rebuild failed",
			"output": result.Output,
		})
		return
	}

	WriteJSON(w, http.StatusOK, map[string]interface{}{
		"status":   "installed",
		"appId":    appID,
		"output":   result.Output,
		"duration": result.Duration.String(),
	})
}

func (h *AppHandler) Uninstall(w http.ResponseWriter, r *http.Request) {
	appID := r.PathValue("id")
	if appID == "" {
		WriteError(w, http.StatusBadRequest, "missing app id")
		return
	}

	if err := h.nixGen.DisableApp(appID); err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to disable app")
		return
	}

	result, err := h.nixGen.Rebuild(r.Context())
	if err != nil {
		WriteJSON(w, http.StatusInternalServerError, map[string]interface{}{
			"error":  "rebuild failed",
			"output": result.Output,
		})
		return
	}

	WriteJSON(w, http.StatusOK, map[string]interface{}{
		"status":   "uninstalled",
		"appId":    appID,
		"duration": result.Duration.String(),
	})
}

func (h *AppHandler) Status(w http.ResponseWriter, r *http.Request) {
	appID := r.PathValue("id")
	if appID == "" {
		WriteError(w, http.StatusBadRequest, "missing app id")
		return
	}

	enabled := h.nixGen.IsEnabled(appID)
	status := "stopped"
	if enabled {
		status = serviceStatus(appID)
	}

	WriteJSON(w, http.StatusOK, map[string]interface{}{
		"appId":   appID,
		"enabled": enabled,
		"status":  status,
	})
}

func (h *AppHandler) UpdateConfig(w http.ResponseWriter, r *http.Request) {
	appID := r.PathValue("id")
	if appID == "" {
		WriteError(w, http.StatusBadRequest, "missing app id")
		return
	}

	var config map[string]string
	if err := DecodeJSON(r, &config); err != nil {
		WriteError(w, http.StatusBadRequest, "invalid config JSON")
		return
	}

	if err := h.nixGen.EnableApp(appID, config); err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to update config")
		return
	}

	result, err := h.nixGen.Rebuild(r.Context())
	if err != nil {
		WriteJSON(w, http.StatusInternalServerError, map[string]interface{}{
			"error":  "rebuild failed after config update",
			"output": result.Output,
		})
		return
	}

	WriteJSON(w, http.StatusOK, map[string]interface{}{
		"status":   "updated",
		"appId":    appID,
		"duration": result.Duration.String(),
	})
}

// registryEntry mirrors the JSON structure from /etc/openos/registry.json
type registryEntry struct {
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
}

func (h *AppHandler) loadRegistry() (map[string]registryEntry, error) {
	data, err := os.ReadFile(h.registryPath)
	if err != nil {
		return nil, fmt.Errorf("read registry: %w", err)
	}

	var registry map[string]registryEntry
	if err := json.Unmarshal(data, &registry); err != nil {
		return nil, fmt.Errorf("parse registry: %w", err)
	}
	return registry, nil
}

// serviceStatus checks if a systemd service is active.
// Maps app IDs to their primary systemd service names.
var serviceNames = map[string]string{
	"nextcloud":   "phpfpm-nextcloud",
	"ollama":      "ollama",
	"syncthing":   "syncthing",
	"jellyfin":    "jellyfin",
	"vaultwarden": "vaultwarden",
	"gitea":       "gitea",
	"hedgedoc":    "hedgedoc",
}

func serviceStatus(appID string) string {
	svcName, ok := serviceNames[appID]
	if !ok {
		svcName = appID
	}

	cmd := exec.Command("systemctl", "is-active", svcName+".service")
	out, err := cmd.Output()
	if err != nil {
		return "stopped"
	}

	status := strings.TrimSpace(string(out))
	switch status {
	case "active":
		return "running"
	case "activating":
		return "starting"
	case "failed":
		return "error"
	default:
		return "stopped"
	}
}
