# Promptlab production deployment

Public entry point: **https://promptlab.zoyanywhere.com**. Access remains invitation-only; an administrator creates invitation links. Publishing the code does not expose private projects or media.

## Topology

Match `zoyanywhere-site`: the existing Traefik instance terminates HTTPS on `websecure` using `myresolver` and routes through the external Docker `web-network`. The website's Caddy serves its static files; this Python application does not require another Caddy container.

The production Compose file is standalone. It publishes no host ports. Only the app has Traefik labels. PostgreSQL and ClamAV communicate over the private internal network. ClamAV has a separate outbound network for signature updates, and its unauthenticated TCP port is never published. The application runs as UID/GID 10001 with a read-only filesystem; the scanner uses the official non-root `clamav` startup. Neither receives the Docker socket.

## Server preparation

1. Point the DNS A/AAAA records for `promptlab.zoyanywhere.com` at the server.
2. Use the existing Docker/Traefik installation and `web-network`. Reserve at least 4 GB for ClamAV in addition to app/database/OS memory.
3. Create a separate deployment directory, for example `/opt/promptlab`. Copy `.env.example` to its private `.env` and fill in API keys and a strong PostgreSQL password. Set `TRUSTED_PROXY_IPS` to the actual Traefik address or a dedicated trusted proxy subnet. Keep `.env` readable only by the deployment account. Do not commit it.
4. The SSH deployment account needs Docker access. Configure GitHub repository secrets: `SSH_HOST`, `SSH_USER`, `SSH_PRIVATE_KEY`, `SSH_KNOWN_HOSTS`, `DEPLOY_PATH`, and optionally `SSH_PORT` (defaults to 22). Obtain the known-hosts entry from a trusted server fingerprint. Host-key checking is mandatory. Use a distinct `DEPLOY_PATH` from the website.
5. Make the GHCR package public for anonymous server pulls, or log the server into GHCR once with a read-packages token. Registry credentials and provider keys stay on the server.

## Delivery pipeline

Pull requests run tests, secret checks, dependency auditing, a non-root image check and Trivy scanning. Production Compose is validated in CI. Main pushes and manual runs of **Release and deploy** run these checks again before publishing `ghcr.io/zoyanywhere/seedance-prompt-review:<commit-sha>`. Deployment uses that commit tag, never `latest`.

Without SSH secrets, the workflow publishes the image and explicitly reports that server deployment is pending. Once configured, it copies the Compose file and deployment script, pulls images, saves a database backup for an existing installation, then waits for all services to become healthy. Initial ClamAV signature downloads can take several minutes. A failed update restores the previous app image when available; database schema changes are not automatically reversed.

Dependabot checks Python, Docker and GitHub Actions weekly. Its PRs merge only after successful CI for their current head commit and required branch checks. After a bot merge, the workflow explicitly dispatches the release workflow because `GITHUB_TOKEN` merges do not trigger push workflows. No pull-request code executes in the privileged merge workflow.

## First administrator and operations

From the server deployment directory, export the published `APP_IMAGE` from `.release.env`, then run:

```bash
export APP_IMAGE="$(sed -n 's/^APP_IMAGE=//p' .release.env)"
docker compose --project-name promptlab -f compose.production.yaml run --rm app python -m app.cli create-admin --email hey@zoyanywhere.com --name Zoyanywhere
```

Enter the password interactively. Local accounts and local media are not automatically migrated to production.

Persist PostgreSQL, media and signature volumes. Keep encrypted off-server backups of PostgreSQL and private media, and test restores before accepting valuable projects. Pre-deployment dumps in `backups/` are only local recovery copies; set retention and off-server backup separately. Never use `docker compose down -v` on the production installation. Monitor container health, signature updates, free disk space and provider usage. A scanner failure returns HTTP 503 for new uploads rather than accepting unchecked files.

## Upload safety boundaries

Every production upload is streamed to ClamAV **before** Pillow/FFmpeg inspection and permanent media storage. Only an explicit clean response permits decoding. Malware is rejected; errors, timeouts and malformed responses block the upload. Existing file-size, pixel, duration and MIME checks remain in place. Files stay private to their owning project.

ClamAV is one protective layer, not protection against every decoder vulnerability or prompt injection. Keep images and dependencies updated. Prompt text and media content remain untrusted data for the agents; antivirus scanning does not establish their instruction authority.

Official scanner reference: https://docs.clamav.net/manual/Installing/Docker.html
