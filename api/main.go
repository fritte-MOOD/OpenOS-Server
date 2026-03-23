package main

import (
	"context"
	"log"
	"os"
	"os/signal"
	"syscall"

	"github.com/openos/api/internal/db"
	"github.com/openos/api/internal/nixgen"
	"github.com/openos/api/internal/server"
)

func main() {
	if len(os.Args) < 2 || os.Args[1] != "serve" {
		log.Fatal("Usage: openos-api serve")
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	listenAddr := envOr("OPENOS_LISTEN_ADDR", "127.0.0.1:8090")
	dbHost := envOr("OPENOS_DB_HOST", "/run/postgresql")
	dbName := envOr("OPENOS_DB_NAME", "openos")
	dbUser := envOr("OPENOS_DB_USER", "openos-api")
	appsNixPath := envOr("OPENOS_APPS_NIX_PATH", "/etc/openos/apps.nix")
	flakePath := envOr("OPENOS_FLAKE_PATH", "/etc/openos/flake")
	registryPath := envOr("OPENOS_REGISTRY_PATH", "/etc/openos/registry.json")
	dataDir := envOr("OPENOS_DATA_DIR", "/data")

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

	log.Printf("OpenOS API listening on %s", listenAddr)
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
