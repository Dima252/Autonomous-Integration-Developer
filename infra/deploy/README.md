# Deploying to AWS Lightsail

The production stack is one Lightsail instance running `docker compose` (ADR 016):
Caddy in front, the UI at `/`, the API under `/api`, with Postgres and Redis on the
compose network. Merging to `main` redeploys it once CI passes
([deploy.yml](../../.github/workflows/deploy.yml)).

| File | Where it runs | What it does |
|---|---|---|
| [compose.yml](compose.yml) | VM | The production stack; images come from GHCR |
| [Caddyfile](Caddyfile) | VM | HTTPS, routing, security headers |
| [env.example](env.example) | VM | Template for `/opt/aidev/.env`, the only place secrets live |
| [deploy.sh](deploy.sh) | VM | Pull, migrate, restart, wait for health checks |
| [bootstrap-data.sh](bootstrap-data.sh) | VM | One-time catalog ingest, docs index and embeddings |
| [setup-vm.sh](setup-vm.sh) | VM | One-time Docker, swap and auto-update setup |

## One-time setup

**1. Create the instance** (Lightsail console → Create instance):
- Linux/Unix, **OS only → Ubuntu 24.04 LTS**, in any region.
- Plan: **2 GB RAM, dual-stack** (about $12/month, with public IPv4). The IPv6-only
  plans are cheaper, but GitHub Actions runners have no IPv6, so CD couldn't reach them.
- Under SSH key, create or upload a key you keep. That key is for you; CD gets its own.

**2. Networking** (instance → Networking):
- Attach a **static IP**. It's free while attached, and the address survives restarts.
- In the IPv4 firewall, keep SSH (22) and HTTP (80), and add **HTTPS (443) TCP**.
  HTTP/3 also needs **443 UDP**, but that one is optional.

**3. Prepare the VM** (SSH in as `ubuntu`):

```sh
curl -fsSL https://raw.githubusercontent.com/Dima252/Autonomous-Integration-Developer/main/infra/deploy/setup-vm.sh | bash
exit   # log back in so the docker group applies
```

Then create `/opt/aidev/.env` from [env.example](env.example): the site address, a
generated Postgres password and the API keys.

**4. Give CD access.** Generate a key pair just for deploys, on your machine:

```sh
ssh-keygen -t ed25519 -f aidev-deploy -N "" -C "github-actions deploy"
# Append aidev-deploy.pub to /home/ubuntu/.ssh/authorized_keys on the VM, then:
ssh-keyscan <static-ip>   # the output is DEPLOY_KNOWN_HOSTS
```

In the GitHub repo, under Settings → Secrets and variables → Actions:

| Name | Kind | Value |
|---|---|---|
| `DEPLOY_HOST` | secret | the static IP |
| `DEPLOY_SSH_KEY` | secret | the contents of the private key `aidev-deploy` |
| `DEPLOY_KNOWN_HOSTS` | secret | the `ssh-keyscan` output |
| `DEPLOY_USER` | secret (optional) | defaults to `ubuntu` |
| `SITE_URL` | variable | e.g. `https://203-0-113-7.sslip.io`, used by the smoke test |

**5. First rollout.** Run the Deploy workflow by hand (Actions → Deploy → Run workflow),
then fill the database on the VM:

```sh
nohup /opt/aidev/bootstrap-data.sh > bootstrap.log 2>&1 &
```

Docs search falls back to full-text search until the embeddings finish (~2.5 h at the
free Voyage pacing).

## Day to day

- **Deploy:** merge to `main`. To roll back, run `/opt/aidev/deploy.sh <older-sha>` on
  the VM. Every build is tagged with its commit.
- **Logs:** `cd /opt/aidev && docker compose logs -f api`
- **Spend:** `docker compose run --rm api python -m aidev.llm usage`
- **Backups:** turn on Lightsail automatic snapshots (billed per GB) if the data
  becomes worth keeping.
  Today everything can be rebuilt from the repo with `bootstrap-data.sh`.
