package handlers

import (
	"net/http"
	"os"
	"os/exec"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/openos/api/internal/models"
	"github.com/openos/api/internal/nixgen"
)

var startTime = time.Now()

type SystemHandler struct {
	nixGen  *nixgen.Generator
	dataDir string
}

func NewSystemHandler(gen *nixgen.Generator, dataDir string) *SystemHandler {
	return &SystemHandler{nixGen: gen, dataDir: dataDir}
}

func (h *SystemHandler) Status(w http.ResponseWriter, r *http.Request) {
	hostname, _ := os.Hostname()

	version := readVersionFile()
	mode := readModeFile()

	status := models.SystemStatus{
		Version:  version,
		Hostname: hostname,
		Uptime:   int64(time.Since(startTime).Seconds()),
		OS:       runtime.GOOS + "/" + runtime.GOARCH,
		Healthy:  true,
		Mode:     mode,
	}

	WriteJSON(w, http.StatusOK, status)
}

func readVersionFile() string {
	data, err := os.ReadFile("/etc/openos/version")
	if err != nil {
		return "0.1.0-dev"
	}
	return strings.TrimSpace(string(data))
}

func readModeFile() string {
	data, err := os.ReadFile("/etc/openos/mode")
	if err != nil {
		return "full"
	}
	mode := strings.TrimSpace(string(data))
	if mode == "" {
		return "full"
	}
	return mode
}

func (h *SystemHandler) Resources(w http.ResponseWriter, r *http.Request) {
	resources := models.SystemResources{
		CPUUsage:    getCPUUsage(),
		MemoryTotal: getMemInfo("MemTotal"),
		MemoryUsed:  getMemInfo("MemTotal") - getMemInfo("MemAvailable"),
		GPUs:        getGPUInfo(),
	}

	// Disk usage for /data
	var stat syscall.Statfs_t
	if err := syscall.Statfs(h.dataDir, &stat); err == nil {
		resources.DiskTotal = stat.Blocks * uint64(stat.Bsize)
		resources.DiskUsed = (stat.Blocks - stat.Bfree) * uint64(stat.Bsize)
	}

	WriteJSON(w, http.StatusOK, resources)
}

func (h *SystemHandler) Network(w http.ResponseWriter, r *http.Request) {
	info := models.NetworkInfo{}
	info.Hostname, _ = os.Hostname()

	// Get Tailscale status
	cmd := exec.Command("tailscale", "status", "--json")
	out, err := cmd.Output()
	if err == nil {
		info.TailscaleStatus = "connected"
		// Parse IP from tailscale status output
		ipCmd := exec.Command("tailscale", "ip", "-4")
		ipOut, err := ipCmd.Output()
		if err == nil {
			info.TailscaleIP = strings.TrimSpace(string(ipOut))
		}
	} else {
		info.TailscaleStatus = "disconnected"
	}

	// Get connected peers
	peersCmd := exec.Command("tailscale", "status", "--peers")
	peersOut, err := peersCmd.Output()
	if err == nil {
		lines := strings.Split(strings.TrimSpace(string(peersOut)), "\n")
		for _, line := range lines {
			if line != "" && !strings.HasPrefix(line, "#") {
				info.ConnectedPeers = append(info.ConnectedPeers, strings.TrimSpace(line))
			}
		}
	}

	WriteJSON(w, http.StatusOK, info)
}

func (h *SystemHandler) Rebuild(w http.ResponseWriter, r *http.Request) {
	dryRun := r.URL.Query().Get("dry") == "true"

	var result *nixgen.RebuildResult
	var err error

	if dryRun {
		result, err = h.nixGen.RebuildDryRun(r.Context())
	} else {
		result, err = h.nixGen.Rebuild(r.Context())
	}

	if err != nil {
		WriteJSON(w, http.StatusInternalServerError, map[string]interface{}{
			"success":  false,
			"output":   result.Output,
			"duration": result.Duration.String(),
			"error":    err.Error(),
		})
		return
	}

	WriteJSON(w, http.StatusOK, map[string]interface{}{
		"success":  true,
		"output":   result.Output,
		"duration": result.Duration.String(),
		"dryRun":   dryRun,
	})
}

func getCPUUsage() float64 {
	data, err := os.ReadFile("/proc/stat")
	if err != nil {
		return 0
	}
	lines := strings.Split(string(data), "\n")
	if len(lines) == 0 {
		return 0
	}
	fields := strings.Fields(lines[0])
	if len(fields) < 5 {
		return 0
	}
	// Simplified: return idle percentage inverted
	idle, _ := strconv.ParseFloat(fields[4], 64)
	total := float64(0)
	for _, f := range fields[1:] {
		v, _ := strconv.ParseFloat(f, 64)
		total += v
	}
	if total == 0 {
		return 0
	}
	return (1 - idle/total) * 100
}

func getMemInfo(key string) uint64 {
	data, err := os.ReadFile("/proc/meminfo")
	if err != nil {
		return 0
	}
	for _, line := range strings.Split(string(data), "\n") {
		if strings.HasPrefix(line, key+":") {
			fields := strings.Fields(line)
			if len(fields) >= 2 {
				val, _ := strconv.ParseUint(fields[1], 10, 64)
				return val * 1024 // /proc/meminfo reports in kB
			}
		}
	}
	return 0
}

func getGPUInfo() []models.GPUInfo {
	cmd := exec.Command("nvidia-smi",
		"--query-gpu=name,memory.total,memory.used,utilization.gpu",
		"--format=csv,noheader,nounits")
	out, err := cmd.Output()
	if err != nil {
		return nil
	}

	var gpus []models.GPUInfo
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		fields := strings.Split(line, ", ")
		if len(fields) < 4 {
			continue
		}
		memTotal, _ := strconv.ParseUint(strings.TrimSpace(fields[1]), 10, 64)
		memUsed, _ := strconv.ParseUint(strings.TrimSpace(fields[2]), 10, 64)
		util, _ := strconv.ParseFloat(strings.TrimSpace(fields[3]), 64)

		gpus = append(gpus, models.GPUInfo{
			Name:        strings.TrimSpace(fields[0]),
			MemoryTotal: memTotal * 1024 * 1024,
			MemoryUsed:  memUsed * 1024 * 1024,
			Utilization: util,
		})
	}
	return gpus
}
