# agenix secret declarations
# Each .age file is encrypted with the public keys listed here.
#
# Usage:
#   1. Add your SSH public key below
#   2. Create a secret: agenix -e secrets/my-secret.age
#   3. Reference in NixOS: config.age.secrets.my-secret.path
#
# See: https://github.com/ryantm/agenix
let
  # Add server SSH host keys and admin user keys here
  serverKey = "ssh-ed25519 AAAA..."; # Replace with actual key
  adminKey = "ssh-ed25519 AAAA...";  # Replace with actual key
  allKeys = [ serverKey adminKey ];
in {
  "nextcloud-admin-pass.age".publicKeys = allKeys;
  "postgres-password.age".publicKeys = allKeys;
  "api-secret.age".publicKeys = allKeys;
}
