package nixgen

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"

	"github.com/openos/api/internal/models"
)

func (g *Generator) ListGenerations(ctx context.Context) ([]models.Generation, error) {
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()

	cmd := exec.CommandContext(ctx, "/etc/openos/list-generations.sh")
	var stdout bytes.Buffer
	cmd.Stdout = &stdout

	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("list generations: %w", err)
	}

	var gens []models.Generation
	if err := json.Unmarshal(stdout.Bytes(), &gens); err != nil {
		return nil, fmt.Errorf("parse generations: %w", err)
	}
	return gens, nil
}

func (g *Generator) RollbackToGeneration(ctx context.Context, generation int) (*RebuildResult, error) {
	start := time.Now()

	ctx, cancel := context.WithTimeout(ctx, 5*time.Minute)
	defer cancel()

	cmd := exec.CommandContext(ctx, "sudo", "/etc/openos/rollback-to.sh",
		fmt.Sprintf("%d", generation))

	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	err := cmd.Run()
	duration := time.Since(start)

	output := stdout.String()
	if stderr.Len() > 0 {
		output += "\n" + stderr.String()
	}

	return &RebuildResult{
		Success:  err == nil,
		Output:   output,
		Duration: duration,
	}, err
}

func (g *Generator) ListVersions(ctx context.Context, channel string) ([]models.Version, error) {
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()

	if channel == "" {
		channel = "all"
	}

	cmd := exec.CommandContext(ctx, "/etc/openos/list-versions.sh", channel)
	var stdout bytes.Buffer
	cmd.Stdout = &stdout

	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("list versions: %w", err)
	}

	var versions []models.Version
	if err := json.Unmarshal(stdout.Bytes(), &versions); err != nil {
		return nil, fmt.Errorf("parse versions: %w", err)
	}
	return versions, nil
}

func (g *Generator) GetUpdateStatus() (*models.UpdateStatus, error) {
	data, err := os.ReadFile("/var/lib/openos-api/update-status.json")
	if err != nil {
		if os.IsNotExist(err) {
			return &models.UpdateStatus{
				Channel:         "unknown",
				CurrentVersion:  g.getCurrentVersion(),
				UpdateAvailable: false,
				CheckedAt:       "never",
			}, nil
		}
		return nil, fmt.Errorf("read update status: %w", err)
	}

	var status models.UpdateStatus
	if err := json.Unmarshal(data, &status); err != nil {
		return nil, fmt.Errorf("parse update status: %w", err)
	}

	staged, err := os.ReadFile("/var/lib/openos-api/staged-update")
	if err == nil {
		status.StagedUpdate = strings.TrimSpace(string(staged))
	}

	return &status, nil
}

func (g *Generator) UpgradeToVersion(ctx context.Context, version string) (*RebuildResult, error) {
	start := time.Now()

	ctx, cancel := context.WithTimeout(ctx, 15*time.Minute)
	defer cancel()

	cmd := exec.CommandContext(ctx, "sudo", "/etc/openos/upgrade-to-version.sh", version)

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

func (g *Generator) ApplyStagedUpdate(ctx context.Context) (*RebuildResult, error) {
	start := time.Now()

	ctx, cancel := context.WithTimeout(ctx, 15*time.Minute)
	defer cancel()

	cmd := exec.CommandContext(ctx, "sudo", "/etc/openos/apply-staged-update.sh")

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

func (g *Generator) CheckForUpdates(ctx context.Context) (*models.UpdateStatus, error) {
	ctx, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()

	cmd := exec.CommandContext(ctx, "sudo", "systemctl", "start", "openos-update-check.service")
	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("trigger update check: %w", err)
	}

	time.Sleep(2 * time.Second)
	return g.GetUpdateStatus()
}

func (g *Generator) GetUpgradeHistory() ([]models.UpgradeHistoryEntry, error) {
	data, err := os.ReadFile("/var/lib/openos-api/upgrade-history")
	if err != nil {
		if os.IsNotExist(err) {
			return []models.UpgradeHistoryEntry{}, nil
		}
		return nil, fmt.Errorf("read history: %w", err)
	}

	var entries []models.UpgradeHistoryEntry
	for _, line := range strings.Split(strings.TrimSpace(string(data)), "\n") {
		if line == "" {
			continue
		}
		entry := models.UpgradeHistoryEntry{}
		parts := strings.Fields(line)
		if len(parts) >= 1 {
			entry.Timestamp = parts[0]
		}
		for _, p := range parts[1:] {
			kv := strings.SplitN(p, "=", 2)
			if len(kv) != 2 {
				continue
			}
			switch kv[0] {
			case "gen":
				fmt.Sscanf(kv[1], "%d", &entry.Generation)
			case "version":
				entry.Version = kv[1]
			case "status":
				entry.Status = kv[1]
			}
		}
		entries = append(entries, entry)
	}
	return entries, nil
}

func (g *Generator) getCurrentVersion() string {
	data, err := os.ReadFile("/etc/openos/version")
	if err != nil {
		return "unknown"
	}
	return strings.TrimSpace(string(data))
}
