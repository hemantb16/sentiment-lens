# ADR-001: Hosting

Status: on hold · Date: 2026-11-15

**Update, 2026-11-16:** the owner decided not to deploy for now. The project lives
on GitHub only and runs locally or from the Docker image. The analysis below
stands as the plan if that changes; its free-tier figures must be re-checked
first, because they will have aged.

## Context

The app must be public, cost nothing, and need no credit card. The original plan
named Hugging Face Spaces (Docker SDK) as the host.

## What was checked on 2026-11-15

| Host | Finding | Source |
|---|---|---|
| Hugging Face Spaces (Docker) | **No longer free to create.** "Gradio and Docker Spaces run on compute and require a paid plan to create: PRO for personal accounts." CPU Basic hardware itself (2 vCPU, 16 GB) has no hourly cost, but only Static Spaces are free for everyone. | https://huggingface.co/docs/hub/spaces-overview |
| Render free web service | 512 MB RAM, a fraction of one CPU (0.1 CPU per Render's docs). Spins down after 15 min without traffic, about 1 min to wake. 750 instance hours per workspace per month. One instance only, ephemeral filesystem, no shell. Hobby plan: 5 GB bandwidth and 500 build minutes per month. Runs Docker images. | https://render.com/docs/free, https://render.com/pricing.md |
| Koyeb | No free web service listed any more. | https://www.koyeb.com/pricing |
| Fly.io, Google Cloud Run, AWS, Azure | Card required. | https://flaviocopes.com/hosting-free-tiers/ |
| GitHub Actions | Standard runners are free in public repositories. | https://docs.github.com/en/billing/concepts/product-billing/github-actions |

Not confirmed: whether Render asks for a card at sign-up. Third-party sources say
no; Render's own pages do not state it. One July 2026 review lists the free
instance at 256 MB, which conflicts with Render's pricing page (512 MB). Both are
re-checked at sign-up in Sprint 5.

## Decision

Deploy one Docker container to a **Render free web service**. Code on GitHub
(public repo), CI on GitHub Actions.

## Consequences

- 512 MB RAM rules out the transformer "accurate mode" on the deployed site. It
  can exist only as a local-only feature flag. The TF-IDF + logistic regression
  model is the product.
- A fraction of one CPU means the "50k rows in under 30 s on 2 vCPU" target is a
  local benchmark, not a promise for the live site. The live row cap is set from
  a measurement on Render in Sprint 5.
- One Uvicorn worker. Uvicorn's own `--workers` flag is used instead of Gunicorn:
  same process model, one dependency fewer.
- The app stays stateless, so moving to a larger host later is a config change.
- Cold starts of about a minute are shown honestly in the UI. No keep-alive pings.
- If Render turns out to require a card at sign-up, no card-free container host
  was found today. The honest alternative is then a local-run app with a
  recorded demo. This is raised with Hemant before Sprint 5.
