# Research: sandbox runtime options

2026-09-25. Question: which isolation technology should run the agents' sandboxes? **Outcome: Docker Sandboxes** (see docker-sandboxes.md).

## Options compared

| | Firecracker (microVM) | gVisor (sandboxed container) | Hardened Docker containers | Docker Sandboxes (sbx) |
| --- | --- | --- | --- | --- |
| Isolation | Own kernel per VM (KVM); same model as AWS Lambda | User-space kernel intercepts syscalls; smaller shared-kernel surface | Shares the host kernel; relies on namespaces, seccomp, AppArmor, caps | microVM with own kernel, managed by Docker |
| Startup | ~125 ms boot; faster from snapshot | Container-like, sub-second | Sub-second | Near-instant (per Docker) |
| Snapshots | Mature memory + disk snapshots | Checkpoint/restore, less mature | Volumes only (CRIU checkpoint experimental) | Stop/resume preserves memory + filesystem (cloud); persistent volumes |
| Overhead | Low CPU; each VM reserves memory | Noticeable on syscall/IO-heavy work | Lowest | microVM overhead, managed |
| Compatibility | Any Linux workload | Some syscalls/kernel features missing | Full | Full, incl. Docker inside the sandbox |
| Host needs | Bare metal or nested virtualization (KVM) | Any VM; Kubernetes RuntimeClass, GKE Sandbox | Any Docker host | macOS/Windows/Linux locally; Docker cloud for hosted |
| Ops effort (self-hosted) | High: networking, images, snapshot pipeline | Moderate | Low | Low: Docker runs it |
| Managed option | E2B and similar | GKE Sandbox | n/a | Docker Cloud Sandboxes (experimental API) |

## Factors specific to Bayto

- Imported agents and code-execution tools mean running untrusted code, which favors a VM boundary.
- The lifecycle relies on snapshots (warm pools, task-scoped memory, replay/fork).
- Turns are mostly LLM-bound, so runtime CPU overhead matters little.
- A solo founder can't afford to run a hypervisor fleet early.

## History of the decision

1. Initial recommendation: managed Firecracker (E2B) behind a `SandboxProvider` interface.
2. Kibrom chose Docker; first read as hardened Docker containers (Docker Engine API), with gVisor `runsc` as a later hardening option.
3. Clarified as **Docker's Sandboxes product**, which gives microVM isolation (closing the shared-kernel gap) with Docker-managed operations.
4. 2026-09-28: local sandboxes only, and all agents of a task share one team sandbox for now. The per-agent isolation factors above matter again only if isolated seats are added.

## Hardening checklist kept for any container-based fallback

Rootless Docker, `--cap-drop=ALL`, `no-new-privileges`, default seccomp + AppArmor, read-only root filesystem with tmpfs scratch, non-root user, `--cpus/--memory/--pids-limit`, per-agent network through an egress proxy, secrets as tmpfs files, image scanning, dedicated sandbox hosts.
