package server

import (
	"net/http"

	"github.com/homeserver/api/internal/handlers"
)

func (s *Server) registerRoutes(mux *http.ServeMux) {
	appHandler := handlers.NewAppHandler(s.config.NixGen, s.config.RegistryPath)
	sysHandler := handlers.NewSystemHandler(s.config.NixGen, s.config.DataDir)
	versionHandler := handlers.NewVersionHandler(s.config.NixGen)
	communityHandler := handlers.NewCommunityHandler(s.config.DB)
	userHandler := handlers.NewUserHandler(s.config.DB)

	// Status
	mux.HandleFunc("GET /api/v1/status", sysHandler.Status)

	// Apps
	mux.HandleFunc("GET /api/v1/apps", appHandler.List)
	mux.HandleFunc("POST /api/v1/apps/{id}/install", appHandler.Install)
	mux.HandleFunc("DELETE /api/v1/apps/{id}/uninstall", appHandler.Uninstall)
	mux.HandleFunc("GET /api/v1/apps/{id}/status", appHandler.Status)
	mux.HandleFunc("PATCH /api/v1/apps/{id}/config", appHandler.UpdateConfig)

	// System
	mux.HandleFunc("GET /api/v1/system/resources", sysHandler.Resources)
	mux.HandleFunc("GET /api/v1/system/network", sysHandler.Network)
	mux.HandleFunc("POST /api/v1/system/rebuild", sysHandler.Rebuild)

	// Versioning & Updates
	mux.HandleFunc("GET /api/v1/system/versions", versionHandler.ListVersions)
	mux.HandleFunc("GET /api/v1/system/generations", versionHandler.ListGenerations)
	mux.HandleFunc("GET /api/v1/system/update-status", versionHandler.UpdateStatus)
	mux.HandleFunc("POST /api/v1/system/check-updates", versionHandler.CheckForUpdates)
	mux.HandleFunc("POST /api/v1/system/upgrade", versionHandler.Upgrade)
	mux.HandleFunc("POST /api/v1/system/apply-staged", versionHandler.ApplyStaged)
	mux.HandleFunc("POST /api/v1/system/rollback", versionHandler.Rollback)
	mux.HandleFunc("GET /api/v1/system/upgrade-history", versionHandler.UpgradeHistory)

	// Communities
	mux.HandleFunc("GET /api/v1/communities", communityHandler.List)
	mux.HandleFunc("POST /api/v1/communities", communityHandler.Create)
	mux.HandleFunc("GET /api/v1/communities/{id}/members", communityHandler.Members)

	// Users
	mux.HandleFunc("GET /api/v1/users", userHandler.List)
	mux.HandleFunc("POST /api/v1/users/invite", userHandler.Invite)
}
