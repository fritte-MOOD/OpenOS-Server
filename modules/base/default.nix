{ lib, ... }:
let
  dir = ./.;
  files = builtins.attrNames (builtins.readDir dir);
  nixFiles = builtins.filter
    (f: f != "default.nix" && lib.hasSuffix ".nix" f)
    files;
in {
  imports = map (f: dir + "/${f}") nixFiles;
}
