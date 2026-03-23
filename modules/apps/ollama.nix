{ config, lib, pkgs, pkgsUnstable, ... }:
let
  cfg = config.openos.apps.ollama;
  dataDir = "${config.openos.dataDir}/apps/ollama";
in {
  options.openos.apps.ollama = {
    enable = lib.mkEnableOption "Ollama local LLM server";

    port = lib.mkOption {
      type = lib.types.port;
      default = 11434;
      description = "Port for the Ollama API.";
    };

    acceleration = lib.mkOption {
      type = lib.types.enum [ "cuda" "rocm" "false" ];
      default = "false";
      description = "GPU acceleration backend. 'cuda' for NVIDIA, 'rocm' for AMD, 'false' for CPU-only.";
    };

    models = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [];
      example = [ "llama3.2" "mistral" "codellama" ];
      description = "Models to pull on first start.";
    };

    domain = lib.mkOption {
      type = lib.types.str;
      default = "llm.${config.openos.domain}";
      description = "Domain for the Ollama web interface.";
    };
  };

  config = lib.mkIf cfg.enable {
    services.ollama = {
      enable = true;
      port = cfg.port;
      host = "0.0.0.0";
      home = dataDir;
      acceleration = if cfg.acceleration == "false" then null else cfg.acceleration;
      loadModels = cfg.models;
    };

    # Open WebUI as a frontend for Ollama
    services.open-webui = {
      enable = true;
      port = 3100;
      environment = {
        OLLAMA_API_BASE_URL = "http://127.0.0.1:${toString cfg.port}";
        WEBUI_AUTH = "false";
      };
    };

    # NVIDIA driver support when using CUDA
    hardware.nvidia = lib.mkIf (cfg.acceleration == "cuda") {
      modesetting.enable = true;
      open = false;
    };
    services.xserver.videoDrivers = lib.mkIf (cfg.acceleration == "cuda") [ "nvidia" ];

    services.nginx.virtualHosts."${cfg.domain}" = {
      locations."/" = {
        proxyPass = "http://127.0.0.1:3100";
        proxyWebsockets = true;
      };
      locations."/api" = {
        proxyPass = "http://127.0.0.1:${toString cfg.port}";
        proxyWebsockets = true;
        extraConfig = ''
          proxy_read_timeout 600s;
        '';
      };
    };

    openos.appRegistry.ollama = {
      name = "Ollama";
      description = "Local LLM server with web interface — share GPU-powered AI with your community";
      icon = "brain";
      category = "ai";
      version = "latest";
      requiresGPU = true;
      ports = [ cfg.port 3100 ];
      databases = [ ];
      enabled = true;
      url = "https://${cfg.domain}";
    };
  };
}
