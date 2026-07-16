---
name: cad-deploy
description: Deploying to prod (cad.enzomaruffa.dev on webrato-remote) — the two deploy paths (app code vs project files), container verification, ports, and running Python/slicer probes inside the container. Use for any deploy or prod-side debugging/verification.
---

# Deploying & verifying on prod

Server `webrato-remote` (SSH alias, agent-forwarded), repo at `~/cad-kit`, container `cad-kit-web-1`. **Two different deploy paths — don't mix them up:**

## 1. App code (backend/app, frontend, viewer) → git + image rebuild

```bash
git push   # personal repo: commit/push without asking (one-liner conventional commits)
ssh -A webrato-remote "cd ~/cad-kit && git pull --ff-only && docker compose build web && docker compose up -d web"
```
The Dockerfile builds the frontend into the image (`./static`), so one rebuild ships both FE and BE.

## 2. Project files (backend/projects/<p>) → volume copy, NO rebuild

Local `backend/projects/` is gitignored; prod projects live on the `/data/projects` volume and are discovered by directory scan (drop-in, no restart):

```bash
cd backend/projects && COPYFILE_DISABLE=1 tar --no-xattrs -czf /tmp/p.tgz <proj>
scp /tmp/p.tgz webrato-remote:/tmp/
ssh -A webrato-remote "docker cp /tmp/p.tgz cad-kit-web-1:/tmp/ && \
  docker exec cad-kit-web-1 sh -c 'rm -rf /data/projects/<proj> && cd /data/projects && tar -xzf /tmp/p.tgz && find /data/projects -name \"._*\" -delete'"
```
The `._*` delete is mandatory — macOS AppleDouble files break `_materialize`'s read_text. `rm -rf` first when files were renamed/deleted (extract-over leaves stale modules).

## Verify in the container (after EVERY deploy)

Bare `python` in the container lacks deps — always `/app/backend/.venv/bin/python`, workdir `/app/backend`. For anything non-trivial, write a local script, `scp` + `docker cp` it in (inline `python -c` via ssh quoting is a bug farm — and f-strings with `{'...':<10}` formatting break in `sh -c` quoting):

```bash
ssh -A webrato-remote "docker exec -w /app/backend cad-kit-web-1 /app/backend/.venv/bin/python /tmp/verify.py"
```

The verify script: run every part (with the `show()` preview wrapper) + every scene via `run_project`, count failures (see `cad-verify`). Import project modules inside the materialized workspace as `projects.<p>.parts.<x>` (bare `parts.<x>` won't resolve). Building parts directly (`app.printplan._build_part`) needs the ambient DSL bound: wrap in `_ambient_dsl(_make_namespace()[0])` and materialize via `_materialize(tmp, {}, "_none")` under `_RUN_LOCK` with tmp on sys.path.

## Prod-only capabilities & plumbing

- **Real slicer** (headless OrcaSlicer under xvfb) exists ONLY on prod — ground-truth slices (`app.kernel.slicer.slice_minutes`, orientation probes) must run in the container; local degrades gracefully.
- Ports: app listens on 8000 in-container → host `127.0.0.1:21310` (`curl` that for HTTP API checks). Local dev backend runs on 8787.
- The public site is behind Cloudflare Access — you cannot screenshot/browse it; verify via the container/API and render images locally instead.
- Beads: file + close a `bd` issue per unit of work. Project-file changes have no git commit; the bd close reason is the record.
