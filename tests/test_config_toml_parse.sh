#!/usr/bin/env bash
# T5 — assert configuration.toml is valid TOML and contains the keys
# PR-Agent needs (provider config, git provider, model name).
set -euo pipefail

cd "$(dirname "$0")/.."

CONFIG_FILE="configuration.toml"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "FAIL: $CONFIG_FILE does not exist"
    exit 1
fi

# Use Python tomllib to parse and validate.
python - "$CONFIG_FILE" <<'PY'
import sys
import tomllib

path = sys.argv[1]
with open(path, "rb") as f:
    cfg = tomllib.load(f)

required_sections = ["config"]
for s in required_sections:
    if s not in cfg:
        print(f"FAIL: missing [{s}] section in {path}")
        sys.exit(1)

# PR-Agent reads model name from config.model sub-table.
config = cfg["config"]
if "model" not in config:
    print("FAIL: [config] missing 'model' sub-table")
    sys.exit(1)
model_cfg = config["model"]
if not isinstance(model_cfg, dict):
    print(f"FAIL: [config.model] must be a sub-table, got {type(model_cfg).__name__}")
    sys.exit(1)
if "model_name" not in model_cfg:
    print(f"FAIL: [config.model] missing 'model_name' (got keys: {list(model_cfg.keys())})")
    sys.exit(1)

# Git provider config must exist as a sub-table (this project is GitLab-only).
if "git_provider" not in config:
    print("FAIL: [config] missing 'git_provider' sub-table")
    sys.exit(1)
gp = config["git_provider"]
if not isinstance(gp, dict):
    print(f"FAIL: [config.git_provider] must be a sub-table, got {type(gp).__name__}")
    sys.exit(1)

print(f"PASS: {path} parses, model={config['model']['model_name']}, "
      f"provider_config_keys={list(gp.keys())}")
PY
