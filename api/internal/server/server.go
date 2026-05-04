package server

import (
	"context"
	"net/http"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/homeserver/api/internal/nixgen"
)

type Config struct {
	ListenAddr   string
	DB           *pgxpool.Pool
	NixGen       *nixgen.Generator
	RegistryPath string
	DataDir      string
}

type Server struct {
	httpServer *http.Server
	config     Config
}

func New(cfg Config) *Server {
	s := &Server{config: cfg}

	mux := http.NewServeMux()
	s.registerRoutes(mux)

	s.httpServer = &http.Server{
		Addr:         cfg.ListenAddr,
		Handler:      withMiddleware(mux, cfg),
		ReadTimeout:  30 * time.Second,
		WriteTimeout: 300 * time.Second,
		IdleTimeout:  120 * time.Second,
	}

	return s
}

func (s *Server) ListenAndServe(ctx context.Context) error {
	errCh := make(chan error, 1)
	go func() {
		errCh <- s.httpServer.ListenAndServe()
	}()

	select {
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		return s.httpServer.Shutdown(shutdownCtx)
	case err := <-errCh:
		return err
	}
}
