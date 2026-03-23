{
  description = "OpenOS Server — self-administering NixOS community server";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.11";
    nixpkgs-unstable.url = "github:NixOS/nixpkgs/nixos-unstable";

    agenix = {
      url = "github:ryantm/agenix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    nixos-hardware.url = "github:NixOS/nixos-hardware";
  };

  outputs = { self, nixpkgs, nixpkgs-unstable, agenix, nixos-hardware, ... }@inputs:
  let
    supportedSystems = [ "x86_64-linux" "aarch64-linux" ];

    forAllSystems = f: nixpkgs.lib.genAttrs supportedSystems f;

    mkPkgsUnstable = system: import nixpkgs-unstable {
      inherit system;
      config.allowUnfree = true;
    };

    # Full OpenOS server with all apps
    mkHost = system: hostname: { extraModules ? [], hardwareModules ? [] }:
      nixpkgs.lib.nixosSystem {
        inherit system;
        specialArgs = {
          inherit inputs;
          pkgsUnstable = mkPkgsUnstable system;
          hostname = hostname;
        };
        modules = [
          ./hosts/${hostname}/default.nix
          ./modules/base
          ./modules/network/tailscale.nix
          agenix.nixosModules.default
        ] ++ hardwareModules ++ extraModules;
      };

    # Minimal seed system: just enough to boot, show admin panel, and pull a version
    mkSeed = system: nixpkgs.lib.nixosSystem {
      inherit system;
      specialArgs = {
        inherit inputs;
        pkgsUnstable = mkPkgsUnstable system;
        hostname = "openos-seed";
      };
      modules = [
        ./hosts/default/hardware-generic.nix
        ./modules/base/options.nix
        ./modules/base/networking.nix
        ./modules/base/users.nix
        ./modules/base/versioning.nix
        ./modules/network/tailscale.nix
        ./modules/seed
      ];
    };

    appModules = [
      ./modules/apps/registry.nix
      ./modules/apps/nextcloud.nix
      ./modules/apps/ollama.nix
      ./modules/apps/syncthing.nix
      ./modules/apps/jellyfin.nix
      ./modules/apps/vaultwarden.nix
      ./modules/apps/gitea.nix
      ./modules/apps/hedgedoc.nix
    ];

  in {
    nixosConfigurations = {
      # Full server (x86_64)
      openos = mkHost "x86_64-linux" "default" {
        extraModules = appModules;
      };

      # Full server (aarch64 — Raspberry Pi 4/5, Ampere, etc.)
      openos-arm = mkHost "aarch64-linux" "default" {
        extraModules = appModules;
      };

      # Seed system — minimal bootloader + admin panel (x86_64)
      openos-seed = mkSeed "x86_64-linux";

      # Seed system (aarch64)
      openos-seed-arm = mkSeed "aarch64-linux";
    };

    # Installer ISO images
    packages = forAllSystems (system:
    let
      pkgs = nixpkgs.legacyPackages.${system};
    in {
      openos-api = pkgs.buildGoModule {
        pname = "openos-api";
        version = "0.1.0";
        src = ./api;
        vendorHash = null;
      };

      # Build a custom installer ISO:  nix build .#installer-iso
      installer-iso = (nixpkgs.lib.nixosSystem {
        inherit system;
        modules = [
          "${nixpkgs}/nixos/modules/installer/cd-dvd/installation-cd-minimal.nix"
          ({ pkgs, lib, ... }: {
            isoImage.isoBaseName = "openos-installer";
            isoImage.volumeID = "OPENOS";

            environment.systemPackages = with pkgs; [
              git parted dosfstools e2fsprogs
              curl jq vim
            ];

            # Auto-start the installer on login
            environment.etc."profile.local".text = ''
              if [ "$(tty)" = "/dev/tty1" ] && [ -z "$OPENOS_INSTALLER_RUNNING" ]; then
                export OPENOS_INSTALLER_RUNNING=1
                echo ""
                echo "Welcome to OpenOS Server Installer"
                echo "==================================="
                echo ""
                echo "  1) Install OpenOS (interactive)"
                echo "  2) Install OpenOS (from network, no USB data needed)"
                echo "  3) Drop to shell"
                echo ""
                read -rp "Choice [1]: " choice
                case "''${choice:-1}" in
                  1) sudo bash /etc/openos-installer/install.sh ;;
                  2) sudo bash /etc/openos-installer/net-install.sh ;;
                  3) echo "Type 'bash /etc/openos-installer/install.sh' to start the installer." ;;
                esac
              fi
            '';

            environment.etc."openos-installer/install.sh" = {
              source = ./scripts/install.sh;
              mode = "0755";
            };

            environment.etc."openos-installer/net-install.sh" = {
              source = ./scripts/net-install.sh;
              mode = "0755";
            };
          })
        ];
      }).config.system.build.isoImage;
    });
  };
}
