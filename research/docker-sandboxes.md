# Research: Docker Sandboxes (sbx)

Researched 2026-09-25. Sources at the end. Docker Sandboxes is moving fast; re-verify before implementation.

## What it is

Docker Sandboxes are isolated environments for running AI coding agents, either on the developer's machine or on Docker-managed cloud compute. The `sbx` CLI creates and manages both kinds.

## Isolation model

- **microVM per sandbox** with its **own kernel** (hardware-boundary isolation). Docker built its own VMM on each OS's native hypervisor: Apple Hypervisor.framework (macOS), Windows Hypervisor Platform, KVM (Linux).
- **Private Docker daemon inside the VM**: agents get full `docker build/run/compose` without mounting the host socket.
- **Credentials injected at runtime from outside the VM**; secrets are never baked into the environment. The workshop's kits show this as proxy-managed credentials per domain (the real key never enters the VM).
- **Scoped filesystem access** defined before the agent runs.
- **Network egress allowlist** defined before the agent runs and enforced by the infrastructure.
- Disposable: delete and recreate in seconds; nothing persists on the host after deletion.

## Install

- macOS: `brew install docker/tap/sbx`
- Windows: `winget install Docker.sbx`
- Linux: supported via KVM (best-effort in the workshop)

## Cloud sandboxes (reference only — not planned for Bayto)

| Size | CPUs | Memory |
| --- | --- | --- |
| micro | 1 | 2 GiB |
| small (default) | 2 | 4 GiB |
| medium | 4 | 8 GiB |
| large | 8 | 16 GiB |
| xl | 16 | 32 GiB |

- Architecture: `--platform linux/amd64` or `linux/arm64`.
- **TTL:** default 1 hour (`--ttl`), extendable (`sbx --cloud ttl +30m NAME`) up to **24 hours from creation**. On timeout: `stop` (resumable), `restart` or `delete`.
- **Stop/resume:** `sbx --cloud stop NAME` preserves memory and filesystem; compute is not metered while stopped.
- **Persistent volumes** supported. MCP servers can be loaded.
- **Ports:** `sbx --cloud ports NAME --publish 8080` returns a public HTTPS URL.
- **Files:** `sbx --cloud cp ./src NAME:/home/agent/workspace/src`; no local workspace path mounting.
- **SSH:** `sbx --cloud setup ssh`.
- **Credentials** for cloud are configured separately from local ones.
- **Limitations:** no `docker exec` and no healthchecks; resource flags (`--cpus`, `--memory`, `--platform`, `--ttl`, `--env`, `--allow-network`) are rejected on reused sandboxes, so choose them at creation.

## Cloud Sandboxes API / SDK (reference only — not planned)

- Status: **Experimental** ("features, interfaces, and behavior may change").
- Documented SDK: TypeScript, `npm install @docker/sandboxes`. (The docker/sandboxes-api repo is described as Go, TypeScript and Python SDKs; not verified.)
- Auth: OAuth device flow for interactive use; **PAT** for CI and unattended servers.
- Operations: `client.kits.launchAndWait('shell')`, `sandbox.processes.run({ args })` (stdout/stderr), `sandbox.refresh()`, `sandbox.delete({ force: true })`, `waitUntilDeleted()`, `client.get(name)`; `timeoutMs` option.
- Closing the client does not delete sandboxes.

## Pricing

- `sbx` CLI and local sandbox compute: free, including commercial use.
- Cloud compute: pay-as-you-go subscription. Model-provider charges are separate.
- Organization policy management (central network/filesystem/MCP policies) is a paid tier.

## Implications for Bayto (local sandboxes only, decided 2026-09-28)

- The orchestrator drives one team sandbox per task with the local `sbx` CLI (`sbx env create`, `sbx exec`, `sbx mount`, `sbx env rm`), as the workshop does. All agents of the task run inside it (decided 2026-09-28).
- Kits provide the runtime, network allowlist and proxy-managed credentials. With one shared sandbox, the network allowlist is the union of all roles' needs.
- A host-side MCP server works through the SBX MCP gateway; no remote MCP needed.
- The cloud sections above are kept for reference only; the `SandboxProvider` interface leaves room to add a remote runtime later.

## Sources

- Docker Sandboxes docs: https://docs.docker.com/ai/sandboxes/
- Why MicroVMs: The Architecture Behind Docker Sandboxes: https://www.docker.com/blog/why-microvms-the-architecture-behind-docker-sandboxes/
- Run your first cloud sandbox (API/SDK): https://docs.docker.com/ai/sandboxes-api/get-started/
- Use cloud sandboxes: https://docs.docker.com/ai/sandboxes/cloud/usage/
- Product page: https://www.docker.com/products/docker-sandboxes/
- InfoWorld, Docker Sandboxes and microVMs, explained: https://www.infoworld.com/article/4177309/docker-sandboxes-and-microvms-explained.html
- Building AI Teams with Docker Sandboxes & Docker Agent: https://www.docker.com/blog/building-ai-teams-docker-sandboxes-agent/
- SDK repo: https://github.com/docker/sandboxes-api
