# Promptlab production deployment

Public entry point: **https://promptlab.zoyanywhere.com**. Access remains invitation-only; an administrator creates invitation links. Publishing the code does not expose private projects or media.

## Topology

Match `zoyanywhere-site`: the existing Traefik instance terminates HTTPS on `websecure` using `myresolver` and routes through the external Docker `web-network`. The website's Caddy serves its static files; this Python application does not require another Caddy container.

The production Compose file publishes no host ports. Only the app has Traefik labels. PostgreSQL uses the private internal network. Both services run without root; the app has a read-only filesystem and neither receives the Docker socket.

## Server preparation

1. Point the DNS A/AAAA records for `promptlab.zoyanywhere.com` at the server.
2. Use the existing Docker/Traefik installation and `web-network`. The confirmed host has 1 CPU and 1 GB RAM. Defaults are `APP_CPUS=1.0`, `APP_MEMORY_LIMIT=512m`, and `DB_MEMORY_LIMIT=192m`; PostgreSQL uses 32 MB shared buffers and up to 20 connections. AI inference runs remotely. This configuration targets light use; media processing and concurrent jobs still need load testing alongside the existing website and proxy. Deployment prints available CPU and RAM. Antivirus scanning is not included.
3. Create a separate deployment directory, `/opt/promptlab.zoyanywhere.com`. Copy `.env.example` to its private `.env` and fill in API keys and a strong PostgreSQL password. Set `TRUSTED_PROXY_IPS` to the actual Traefik address or a dedicated trusted proxy subnet. Keep `.env` readable only by the deployment account. Do not commit it.
4. The SSH deployment account needs Docker access. Configure GitHub repository secrets: `SSH_HOST`, `SSH_USER`, `SSH_PRIVATE_KEY`, `DEPLOY_PATH`, and optionally `SSH_PORT` (defaults to 22). The workflow discovers host keys with `ssh-keyscan` at the start of each deployment; no `SSH_KNOWN_HOSTS` secret is needed. SSH checks against these discovered keys, but the initial server identity is not independently verified. Use a distinct `DEPLOY_PATH` from the website.
5. For private GHCR images, set repository secrets `GHCR_USERNAME` and `GHCR_TOKEN` (a token with `read:packages` and access to this package). The workflow logs the deployment account into GHCR over SSH using password stdin before pulling images; the token is never included in the remote command arguments. Set both secrets together. Without them, the package must be public or the server must already be logged in. Provider keys remain in the private server `.env`.

## Delivery pipeline

Pull requests run tests inside the non-root application runtime, using its installed FFmpeg instead of installing media tools on the Ubuntu runner. A separate test build stage supplies test dependencies; the default production image excludes them. CI also runs secret checks, dependency auditing, a non-root image check and Trivy scanning. Production Compose is validated in CI. Main pushes and manual runs of **Release and deploy** run these checks again before publishing `ghcr.io/zoyanywhere/seedance-prompt-review:<commit-sha>`. Deployment uses that commit tag, never `latest`.

Without SSH secrets, the workflow publishes the image and explicitly reports that server deployment is pending. Once configured, it copies the Compose file and deployment script, pulls images, saves a database backup for an existing installation, then waits for all services to become healthy. Obsolete scanner containers are removed during startup; existing named volumes are retained. A failed update restores the previous app image when available; database schema changes are not automatically reversed.

Dependabot checks Python, Docker and GitHub Actions weekly. Its PRs merge only after successful CI for their current head commit and required branch checks. After a bot merge, the workflow explicitly dispatches the release workflow because `GITHUB_TOKEN` merges do not trigger push workflows. No pull-request code executes in the privileged merge workflow.

## First administrator and operations

From the server deployment directory, export the published `APP_IMAGE` from `.release.env`, then run:

```bash
export APP_IMAGE="$(sed -n 's/^APP_IMAGE=//p' .release.env)"
docker compose --project-name promptlab -f compose.production.yaml run --rm app python -m app.cli create-admin --email hey@zoyanywhere.com --name Zoyanywhere
```

Enter the password interactively. Local accounts and local media are not automatically migrated to production.

Persist PostgreSQL and media volumes. Keep encrypted off-server backups and test restores. Pre-deployment dumps in `backups/` are local recovery copies; configure retention separately. Never use `docker compose down -v` on production. Monitor container health, memory, disk space and provider usage.

## Upload safety boundaries

Uploads require authenticated project ownership and pass file-type, size, pixel and duration checks. Files remain private to their project. Antivirus scanning was removed at the creator's request to fit the 1-GB host; uploads are not malware-scanned.

Keep images, dependencies and media decoders updated. Validation does not detect every malicious file or decoder exploit. Prompt text and media remain untrusted data for agents.

## Image retention

After a healthy deployment, the script removes older tags belonging only to `ghcr.io/zoyanywhere/seedance-prompt-review` and verifies that the current release is the sole remaining tag. Cleanup uses no force flag and does not prune other repositories, containers or volumes. The prior release remains available during startup failure recovery; after successful cleanup, a later rollback requires pulling its commit tag from GHCR again.
