# Radon factory

Foreman for [`joemccann/radon`](https://github.com/joemccann/radon). Vendored from [`vercel-labs/eve-software-factory-template`](https://github.com/vercel-labs/eve-software-factory-template) (`factory/UPSTREAM`). Operator contract: [`docs/factory.md`](../docs/factory.md).

Label a GitHub issue `factory`. Four stations (classifier, analyst, implementer, reviewer) produce a draft pull request on a `factory/` branch. A person marks ready and merges. The factory never merges and never pushes `main`.

Linear is not connected. GitHub Connect + Vercel Blob + Vercel Sandbox only.

```bash
cd factory
pnpm install
pnpm validate
# vercel link && vercel env pull && pnpm dev
# eve deploy
```

`FACTORY_SETUP_COMMAND` is `bash scripts/factory_sandbox_setup.sh` and runs inside the **radon** checkout, not this directory.
