package main

import (
	"context"
	"log"
	"os"
	"os/signal"
	"syscall"

	"github.com/homeserver/api/internal/db"
	"github.com/homeserver/api/internal/nixgen"
	"github.com/homeserver/api/internal/server"
)

func main() {
	if len(os.Args) < 2 || os.Args[1] != "serve" {
		log.Fatal("Usage: homeserver-api serve")
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	listenAddr := envOr("HOMESERVER_LISTEN_ADDR", "127.0.0.1:8090")
	dbHost := envOr("HOMESERVER_DB_HOST", "/run/postgresql")
	dbName := envOr("HOMESERVER_DB_NAME", "homeserver")
	dbUser := envOr("HOMESERVER_DB_USER", "homeserver-api")
	appsNixPath := envOr("HOMESERVER_APPS_NIX_PATH", "/etc/homeserver/apps.nix")
	flakePath := envOr("HOMESERVER_FLAKE_PATH", "/etc/homeserver/flake")
	registryPath := envOr("HOMESERVER_REGISTRY_PATH", "/etc/homeserver/registry.json")
	dataDir := envOr("HOMESERVER_DATA_DIR", "/data")

	pool, err := db.Connect(ctx, dbHost, dbName, dbUser)
	if err != nil {
		log.Fatalf("Failed to connect to database: %v", err)
	}
	defer pool.Close()

	gen := nixgen.New(appsNixPath, flakePath)

	srv := server.New(server.Config{
		ListenAddr:   listenAddr,
		DB:           pool,
		NixGen:       gen,
		RegistryPath: registryPath,
		DataDir:      dataDir,
	})

	go func() {
		sigCh := make(chan os.Signal, 1)
		signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
		<-sigCh
		log.Println("Shutting down...")
		cancel()
	}()

	log.Printf("homeserver OS API listening on %s", listenAddr)
	if err := srv.ListenAndServe(ctx); err != nil {
		log.Fatalf("Server error: %v", err)
	}
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
