# Running CI on a NodeRings self-hosted runner

CI runs on GitHub-hosted runners by default. It moves to our own runner when
both of these are true:

1. A runner with the labels `self-hosted` and `noderings` is online.
2. The repository variable `NODERINGS_RUNNER` is `true`.

Clear the variable (or set it to anything else) to go back to GitHub-hosted
runners at once, for example while the runner is down.

These always stay on GitHub-hosted runners, whatever the variable says:

- pull requests from forks (their code is untrusted)
- the `CI result` summary job
- building and publishing releases (`release.yml`)
- the GitHub Pages deploy job

## Recommended setup: one dedicated NodeRings VM, repository-level runner

Use a small Ubuntu 24.04 (x64) virtual machine ordered on NodeRings, used for
nothing else, with the GitHub Actions runner installed as a service.

Why this option:

- **A dedicated VM, not the production Kubernetes cluster.** CI runs code from
  pull requests. Keep it away from production workloads, cluster credentials
  and Argo CD. actions-runner-controller on the prod cluster would put CI
  pods next to production.
- **The GitHub Actions runner, not Woodpecker or Forgejo.** The code, pull
  requests and branch protection live on GitHub. The native runner reports
  ordinary check runs, so branch protection, approvals and logs work
  unchanged. Woodpecker or a Forgejo runner would need a separate CI server,
  webhooks and mirroring for the same result.
- **Repository-level, not organization-level.** The noderings organization
  is on the Free plan, which only has the Default runner group. An
  organization runner there would serve every public repository in the
  organization. A repository runner only takes jobs from this repository.
  Register one per repository (one VM can host several runner services, each
  in its own directory and user), or move to an organization runner group
  limited to selected repositories once the organization is on a paid plan.

### 1. Lock down fork pull requests first

GitHub warns that self-hosted runners on public repositories can run code
from anyone who can open a pull request. A fork can edit the workflow files in
its own pull request, so the `runs-on` guard in our workflows is not enough by
itself.

- Repository → Settings → Actions → General → *Approval for running fork pull
  request workflows from contributors* → **Require approval for all external
  contributors**. (This repository already has this setting.)
- Before approving a fork run, read every change under `.github/`. Never
  approve a run that changes `runs-on` or adds `self-hosted`.
- Never use `pull_request_target` together with a checkout of the pull
  request's code.

### 2. Prepare the VM

As root on the new VM:

```bash
apt-get update
apt-get install -y --no-install-recommends \
  git curl ca-certificates jq make build-essential \
  libenchant-2-2 python3-dev python3-venv
# An unprivileged user for the runner, without sudo.
useradd --create-home --shell /bin/bash runner
# Tool cache for actions/setup-python.
install -d -o runner -g runner /opt/hostedtoolcache
```

Keep the VM patched (`unattended-upgrades`), open no inbound ports (the runner
only makes outbound HTTPS connections to GitHub), and do not store other
credentials on it.

### 3. Register the runner

1. Open <https://github.com/noderings/saltext-noderings/settings/actions/runners/new>
   (Settings → Actions → Runners → **New self-hosted runner**), pick
   **Linux** and **x64**. The page shows a registration token that is valid
   for one hour. Use it only in the VM's shell. Do not paste it anywhere else.
2. As the `runner` user on the VM:

```bash
mkdir -p ~/actions-runner && cd ~/actions-runner
curl -fsSLO https://github.com/actions/runner/releases/download/v2.338.2/actions-runner-linux-x64-2.338.2.tar.gz
echo "baebf204307bac6193480852732f0aba2ae97b6c5597c244f4d5558390147c40  actions-runner-linux-x64-2.338.2.tar.gz" | sha256sum -c
tar xzf actions-runner-linux-x64-2.338.2.tar.gz
echo "AGENT_TOOLSDIRECTORY=/opt/hostedtoolcache" >> .env
./config.sh --unattended \
  --url https://github.com/noderings/saltext-noderings \
  --token <TOKEN FROM THE PAGE> \
  --name noderings-ci-1 \
  --labels noderings \
  --work _work
```

   `self-hosted`, `Linux` and `X64` are added automatically. `noderings` is
   the label the workflows ask for.
3. Back as root, install and start the service:

```bash
cd /home/runner/actions-runner
./svc.sh install runner
./svc.sh start
```

4. The runner should now show as **Idle** on
   <https://github.com/noderings/saltext-noderings/settings/actions/runners>.

### 4. Switch CI over

Repository → Settings → Secrets and variables → Actions → **Variables** →
New repository variable: `NODERINGS_RUNNER` = `true`.
Then re-run CI on `main` (Actions → CI → Run workflow) and check that the jobs
show the runner name `noderings-ci-1`.

To switch back, delete the variable or set it to `false`.

### Optional: live API tests

Live tests run only when the repository secret `NODERINGS_TOKEN` exists. It
should be a service account token for a separate test organization with a
small credit balance. Fork pull requests never get secrets. Tests that order
billed servers only run from a manual CI run with *billed* ticked.

### Later: ephemeral runners

A persistent runner keeps its work directory between jobs. Our workflows clean
up after themselves and only trusted code reaches the runner, so this is
acceptable to start with. To give every job a fresh machine, register with
`--ephemeral`, then re-register after each job. This needs a GitHub App with
the *Administration: write* repository permission so the VM can request
registration tokens itself.
