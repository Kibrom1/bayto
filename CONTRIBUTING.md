# Contributing to Bayto

## Version Pinning
To ensure stability across different developer environments and the team sandbox, all external tool versions must be strictly pinned.

- **Python**: 3.12
- **uv**: latest stable
- **sbx**: latest stable (installed via brew install docker/tap/sbx)
- **Postgres**: 16 (alpine)
- **Claude CLI**: latest stable

When upgrading a tool, please update the `versions.env` file and notify the team to avoid breaking the sandbox environment.
