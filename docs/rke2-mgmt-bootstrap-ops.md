# RKE2 Mgmt Cluster Bootstrap (cert-manager → Rancher → Argo CD)

Operational runbook for the **one-time** Helm bootstrap that runs after RKE2 is up and before `homelab-gitops` takes over.

This is the scope-narrow companion to [`rke2-mgmt-ops.md`](rke2-mgmt-ops.md). RKE2-the-distribution lives there; everything *inside* the cluster that has to exist before Argo CD can manage it lives here.

---

## What this covers

| Component | Why it's in Ansible (not gitops) |
|---|---|
| **cert-manager** | Rancher's pre-install hook requires the cert-manager webhook to be live. Has to exist before Rancher. |
| **Rancher** | Multi-cluster control plane. Installed once with Rancher's self-signed CA; real TLS comes later via gitops. |
| **Argo CD** | The handoff point. Installed bare, then immediately repointed at `homelab-gitops/bootstrap` (app-of-apps) — from that moment, Argo manages its own config from Git. |

Anything else (Harbor, observability, ClusterIssuers, ingress configuration, Rancher's real TLS swap) lives in `homelab-gitops`, not here.

---

## Prereqs

### On the workstation (Ansible controller)

- `helm` v3.x on `$PATH` — the bootstrap plays delegate Helm execution to `localhost`.
- `kubernetes.core` collection installed: `ansible-galaxy collection install -r requirements.yml`.
- `kubeconfig-rke2-mgmt.yaml` present at the repo root — fetch with `playbooks/rke2-mgmt-kubeconfig.yml`.

### In the cluster

- RKE2 mgmt cluster healthy: 3 nodes `Ready`, kube-vip holds `10.0.3.40`, API reachable on `https://10.0.3.40:6443`.
- Built-in `ingress-nginx` running (RKE2 ships it by default).

### In DNS (Rancher only)

`rancher_hostname` (default `rancher.yavin.internal`) must resolve to the RKE2 ingress entrypoint from wherever you're browsing. For the homelab plan this means the internal-DNS Pi (Phase 1 / Week 5) — until that exists, a `/etc/hosts` entry on your workstation is fine for first-login. Avoid `.dev` (HSTS preload) and other ICANN-allocated TLDs; `.internal` (ICANN-reserved for private use) and `.home.arpa` (RFC 8375) are the safe bets.

### Secrets file

The bootstrap reads Rancher's initial password from a gitignored sibling of `main.yml`. Before the first run:

```bash
cp inventory/group_vars/rke2-mgmt/secrets.yml.example \
   inventory/group_vars/rke2-mgmt/secrets.yml
$EDITOR inventory/group_vars/rke2-mgmt/secrets.yml
```

The `.example` file is committed and is the source of truth for which keys live in `secrets.yml`. The actual `secrets.yml` is gitignored via `inventory/group_vars/*/secrets.yml` and is loaded by:

- `vars_files:` in each bootstrap playbook (explicit, since the bootstrap plays target `localhost`)
- Ansible's automatic `group_vars/<group>/` merge for any play targeting the `rke2-mgmt` inventory group (the existing RKE2 install/update/reboot plays). Those plays don't currently consume any of the secret keys, so a missing `secrets.yml` is harmless for them — but the bootstrap plays will fail fast with a `file not found` error.

Rotate the password from inside Rancher after the first login; the value in `secrets.yml` is only consulted on the very first install.

---

## Variables

Non-secret defaults live in `inventory/group_vars/rke2-mgmt/main.yml` under the **One-time Helm bootstrap** section. The secret bootstrap password lives in `inventory/group_vars/rke2-mgmt/secrets.yml` (gitignored — copied from `secrets.yml.example`).

| Var | File | Default | Notes |
|---|---|---|---|
| `bootstrap_kubeconfig` | `main.yml` | `kubeconfig-rke2-mgmt.yaml` at repo root | The admin kubeconfig the local helm task uses |
| `certmanager_chart_version` | `main.yml` | pinned | Bump deliberately; check Rancher compatibility before raising |
| `rancher_chart_version` | `main.yml` | pinned | Match to the K8s minor version RKE2 is running |
| `rancher_hostname` | `main.yml` | `rancher.yavin.internal` | **Override per environment** — must be reachable in DNS |
| `rancher_bootstrap_password` | `secrets.yml` | placeholder | **Override locally** — value never enters git |
| `argocd_chart_version` | `main.yml` | pinned | Bare install — extra config comes via gitops |

---

## Running the bootstrap

### All-in-one (Phase 2 teardown drill)

```bash
ansible-playbook playbooks/rke2-mgmt-bootstrap.yml \
  --extra-vars "rancher_hostname=rancher.<your-lab>.internal"
```

The wrapper preflights the kubeconfig and API reachability, then imports each component playbook in order: cert-manager → Rancher → Argo CD. Re-running is safe (`helm upgrade --install` semantics on every release).

### Single-component (Week-by-week, plan-aligned)

The plan stages these across weeks 5 and 8. Run them piecemeal:

```bash
# Week 5: cert-manager + Rancher
ansible-playbook playbooks/rke2-mgmt-certmanager.yml
ansible-playbook playbooks/rke2-mgmt-rancher.yml

# Week 8: Argo CD
ansible-playbook playbooks/rke2-mgmt-argocd.yml
```

The single-component plays skip the preflight in the wrapper. If the kubeconfig is missing or the cluster is unreachable, the Helm task itself fails with a clear error. All three plays load `secrets.yml` via `vars_files`, so a missing file fails at parse time with `ERROR! could not find file ... secrets.yml` — copy the example and retry.

---

## After Rancher comes up

1. Browse to `https://<rancher_hostname>`. Browser will warn about the self-signed cert — that's expected.
2. Log in with the bootstrap password.
3. Set a real admin password.
4. The plan's Week 6 work (importing the Pi K3s cluster, registering Harvester) happens through this UI.

### Swapping to real TLS later

When the Cloudflare DNS-01 ClusterIssuer is up (via `homelab-gitops`), re-template the Rancher release with:

```yaml
ingress:
  tls:
    source: secret
  extraAnnotations:
    cert-manager.io/cluster-issuer: letsencrypt-cloudflare
```

That change lives in the gitops repo, **not in this Ansible repo**. Do not re-run `rke2-mgmt-rancher.yml` with self-signed after gitops takes over — you'll fight Argo for ownership.

---

## After Argo CD comes up

The plan calls for Argo to take over its own configuration *immediately*. While Argo is still on the bootstrap install:

```bash
# Open the UI
kubectl -n argocd port-forward svc/argocd-server 8080:443

# Get the initial admin password
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d ; echo
```

Then point Argo at `homelab-gitops/bootstrap` (app-of-apps). Once that root Application is synced, Argo owns its own config — including the swap from this bootstrap install to a fully-templated one with ingress, RBAC, SSO, etc.

From that moment, the plan's operating rule #1 applies: **Git or it didn't happen.** Don't keep running these bootstrap plays for routine work — they exist only for the rebuild drill.

---

## What this playbook deliberately does *not* do

| Not here | Where it goes |
|---|---|
| Cloudflare DNS-01 ClusterIssuer | `homelab-gitops/platform/cert-manager/` |
| Wildcard Certificate for `*.lab.<domain>` | `homelab-gitops/platform/cert-manager/` |
| Rancher's real TLS cert (`ingress.tls.source=secret`) | Re-template the Rancher Application in `homelab-gitops` |
| Argo CD ingress / RBAC / SSO / repo creds | `homelab-gitops/platform/argocd/` |
| Harbor, observability, External-DNS, gateway | `homelab-gitops/platform/<component>/` |
| Downstream cluster registration (Pi K3s, Harvester) | Rancher UI (Phase 1) then `homelab-gitops` (Phase 6) |

Keeping this list short *is* the design — every line of bootstrap is one more thing a rebuild drill has to redo by hand.

---

## Reference: file layout

```
/
├── inventory/
│   └── group_vars/rke2-mgmt/
│       ├── main.yml                    # Bootstrap vars under the "One-time Helm bootstrap" header
│       ├── secrets.yml                 # Gitignored — your bootstrap password lives here
│       └── secrets.yml.example         # Committed template; copy to secrets.yml
├── playbooks/
│   ├── rke2-mgmt-bootstrap.yml         # Preflight + import of the three component plays
│   ├── rke2-mgmt-certmanager.yml       # Single-component: cert-manager
│   ├── rke2-mgmt-rancher.yml           # Single-component: Rancher
│   ├── rke2-mgmt-argocd.yml            # Single-component: Argo CD
│   └── roles/
│       ├── certmanager/tasks/main.yml
│       ├── rancher/tasks/main.yml
│       └── argocd/tasks/main.yml
└── docs/
    └── rke2-mgmt-bootstrap-ops.md      # This file
```
