# Template: hvp data-layer shell for a project that uses Nix flakes.
# Copy the devShell (or merge its packages and shellHook into the project's own flake).
#
# Why a venv inside the Nix shell: the Python packages are pinned once, in
# requirements-data.lock, so the Nix path and the plain-venv path run exactly the same
# versions. Nix supplies the interpreter and native libraries; the lock supplies packages.
# A pure alternative (python3.withPackages) is possible but would pin through nixpkgs
# instead of the lock, and the two could drift.
{
  description = "hvp data layer shell (MCAP, Parquet, DuckDB, MCP)";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (s: f nixpkgs.legacyPackages.${s});
    in {
      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [ pkgs.python313 ];
          # HVP_SKILL: path to the hardware-verification-plan skill directory
          shellHook = ''
            export HVP_SKILL="''${HVP_SKILL:-$HOME/.claude/skills/hardware-verification-plan}"
            if [ -f "$HVP_SKILL/scripts/hvp_env.py" ]; then
              export HVP_PY="$(python3 "$HVP_SKILL/scripts/hvp_env.py" setup --quiet --print-python)"
              echo "hvp data python: $HVP_PY"
            else
              echo "hvp: set HVP_SKILL to the skill directory to enable the data layer" >&2
            fi
          '';
        };
      });
    };
}
