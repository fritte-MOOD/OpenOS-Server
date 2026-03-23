package handlers

import (
	"net/http"

	"github.com/openos/api/internal/models"
	"github.com/openos/api/internal/nixgen"
)

type VersionHandler struct {
	nixGen *nixgen.Generator
}

func NewVersionHandler(gen *nixgen.Generator) *VersionHandler {
	return &VersionHandler{nixGen: gen}
}

// GET /api/v1/system/versions
func (h *VersionHandler) ListVersions(w http.ResponseWriter, r *http.Request) {
	channel := r.URL.Query().Get("channel")

	versions, err := h.nixGen.ListVersions(r.Context(), channel)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to list versions: "+err.Error())
		return
	}

	WriteJSON(w, http.StatusOK, versions)
}

// GET /api/v1/system/generations
func (h *VersionHandler) ListGenerations(w http.ResponseWriter, r *http.Request) {
	gens, err := h.nixGen.ListGenerations(r.Context())
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to list generations: "+err.Error())
		return
	}

	WriteJSON(w, http.StatusOK, gens)
}

// GET /api/v1/system/update-status
func (h *VersionHandler) UpdateStatus(w http.ResponseWriter, r *http.Request) {
	status, err := h.nixGen.GetUpdateStatus()
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to get update status: "+err.Error())
		return
	}

	WriteJSON(w, http.StatusOK, status)
}

// POST /api/v1/system/check-updates
func (h *VersionHandler) CheckForUpdates(w http.ResponseWriter, r *http.Request) {
	status, err := h.nixGen.CheckForUpdates(r.Context())
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to check for updates: "+err.Error())
		return
	}

	WriteJSON(w, http.StatusOK, status)
}

// POST /api/v1/system/upgrade
func (h *VersionHandler) Upgrade(w http.ResponseWriter, r *http.Request) {
	var req models.UpgradeRequest
	if err := DecodeJSON(r, &req); err != nil {
		WriteError(w, http.StatusBadRequest, "invalid request body")
		return
	}

	if req.Version == "" {
		WriteError(w, http.StatusBadRequest, "version is required (e.g. \"v1.0.0\")")
		return
	}

	result, err := h.nixGen.UpgradeToVersion(r.Context(), req.Version)
	if err != nil {
		status := http.StatusInternalServerError
		WriteJSON(w, status, map[string]interface{}{
			"success":  false,
			"output":   result.Output,
			"duration": result.Duration.String(),
			"error":    err.Error(),
		})
		return
	}

	WriteJSON(w, http.StatusOK, map[string]interface{}{
		"success":  true,
		"version":  req.Version,
		"output":   result.Output,
		"duration": result.Duration.String(),
	})
}

// POST /api/v1/system/apply-staged
func (h *VersionHandler) ApplyStaged(w http.ResponseWriter, r *http.Request) {
	result, err := h.nixGen.ApplyStagedUpdate(r.Context())
	if err != nil {
		WriteJSON(w, http.StatusInternalServerError, map[string]interface{}{
			"success": false,
			"output":  result.Output,
			"error":   err.Error(),
		})
		return
	}

	WriteJSON(w, http.StatusOK, map[string]interface{}{
		"success":  true,
		"output":   result.Output,
		"duration": result.Duration.String(),
	})
}

// POST /api/v1/system/rollback
func (h *VersionHandler) Rollback(w http.ResponseWriter, r *http.Request) {
	var req models.RollbackRequest
	if err := DecodeJSON(r, &req); err != nil {
		WriteError(w, http.StatusBadRequest, "invalid request body")
		return
	}

	if req.Generation <= 0 {
		WriteError(w, http.StatusBadRequest, "generation must be a positive integer")
		return
	}

	result, err := h.nixGen.RollbackToGeneration(r.Context(), req.Generation)
	if err != nil {
		WriteJSON(w, http.StatusInternalServerError, map[string]interface{}{
			"success":    false,
			"generation": req.Generation,
			"output":     result.Output,
			"error":      err.Error(),
		})
		return
	}

	WriteJSON(w, http.StatusOK, map[string]interface{}{
		"success":    true,
		"generation": req.Generation,
		"output":     result.Output,
		"duration":   result.Duration.String(),
	})
}

// GET /api/v1/system/upgrade-history
func (h *VersionHandler) UpgradeHistory(w http.ResponseWriter, r *http.Request) {
	history, err := h.nixGen.GetUpgradeHistory()
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to read upgrade history: "+err.Error())
		return
	}

	WriteJSON(w, http.StatusOK, history)
}
