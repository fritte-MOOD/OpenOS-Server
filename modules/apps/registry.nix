{ config, lib, pkgs, ... }:
{
  # The app registry is a pure data structure that the Go API reads
  # to know which apps are available, their metadata, and current state.
  # Each app module sets its own entry via config.openos.apps.<name>.

  options.openos.appRegistry = lib.mkOption {
    type = lib.types.attrsOf (lib.types.submodule {
      options = {
        name = lib.mkOption { type = lib.types.str; };
        description = lib.mkOption { type = lib.types.str; };
        icon = lib.mkOption { type = lib.types.str; };
        category = lib.mkOption {
          type = lib.types.enum [ "files" "communication" "media" "ai" "development" "security" "tools" ];
        };
        version = lib.mkOption { type = lib.types.str; default = ""; };
        requiresGPU = lib.mkOption { type = lib.types.bool; default = false; };
        ports = lib.mkOption { type = lib.types.listOf lib.types.port; default = []; };
        databases = lib.mkOption { type = lib.types.listOf lib.types.str; default = []; };
        enabled = lib.mkOption { type = lib.types.bool; default = false; };
        url = lib.mkOption { type = lib.types.str; default = ""; };
      };
    });
    default = {};
    description = "Registry of all available OpenOS apps and their metadata.";
  };

  config = {
    # Write the registry as JSON so the Go API can read it
    environment.etc."openos/registry.json".text = builtins.toJSON config.openos.appRegistry;
  };
}
