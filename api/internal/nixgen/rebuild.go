package nixgen

import (
	"bytes"
	"context"
	"fmt"
	"os/exec"
	"time"
)

type RebuildResult struct {
	Success  bool      `json:"success"`
	Output   string    `json:"output"`
	Duration time.Duration `json:"duration"`
}

func (g *Generator) Rebuild(ctx context.Context) (*RebuildResult, error) {
	start := time.Now()

	ctx, cancel := context.WithTimeout(ctx, 10*time.Minute)
	defer cancel()

	cmd := exec.CommandContext(ctx, "sudo", "nixos-rebuild", "switch",
		"--flake", g.flakePath+"#homeserver",
		"--impure",
		"--no-write-lock-file",
	)

	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	err := cmd.Run()
	duration := time.Since(start)

	output := stdout.String()
	if stderr.Len() > 0 {
		output += "\n--- stderr ---\n" + stderr.String()
	}

	if err != nil {
		return &RebuildResult{
			Success:  false,
			Output:   output,
			Duration: duration,
		}, fmt.Errorf("nixos-rebuild failed: %w\n%s", err, output)
	}

	return &RebuildResult{
		Success:  true,
		Output:   output,
		Duration: duration,
	}, nil
}

func (g *Generator) RebuildDryRun(ctx context.Context) (*RebuildResult, error) {
	start := time.Now()

	ctx, cancel := context.WithTimeout(ctx, 5*time.Minute)
	defer cancel()

	cmd := exec.CommandContext(ctx, "sudo", "nixos-rebuild", "dry-activate",
		"--flake", g.flakePath+"#homeserver",
		"--impure",
		"--no-write-lock-file",
	)

	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	err := cmd.Run()
	duration := time.Since(start)

	output := stdout.String()
	if stderr.Len() > 0 {
		output += "\n--- stderr ---\n" + stderr.String()
	}

	return &RebuildResult{
		Success:  err == nil,
		Output:   output,
		Duration: duration,
	}, err
}
