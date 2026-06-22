# Ansible Management for Homelab

This repo manages OSes for the fleet of hardware and VMs running in the homelab.

Current roles:
- Apply a safe baseline for all OSes
- Manage tagged upgrades
- Manage Portainer for the simple Docker containers needed in the lab
- Manage code-server
- Provision and tear down a K3s dev cluster on Raspberry Pis (see [docs/k3s-ops.md](docs/k3s-ops.md))
- Provision and tear down the RKE2 management cluster on Proxmox VMs (see [docs/rke2-mgmt-ops.md](docs/rke2-mgmt-ops.md))
- One-time bootstrap of cert-manager, Rancher, and Argo CD on the RKE2 mgmt cluster — the last manual Helm step before `homelab-gitops` takes over (see [docs/rke2-mgmt-bootstrap-ops.md](docs/rke2-mgmt-bootstrap-ops.md))
- Share a single `kubevip` role between both clusters for the ARP-mode control-plane VIP
- Run a one-shot post-install on fresh Proxmox VE nodes (see [docs/proxmox-ops.md](docs/proxmox-ops.md))
- Keep roles semantic and not procedural

---

## Managed Hosts
| Hostname           | Purpose                                       | OS                   |
|--------------------|-----------------------------------------------|----------------------|
| `portainer-pi`     | Portainer (joined to `nginx-proxy-network`)   | Raspberry Pi OS Lite |
| `codeserver-pi`    | code-server                                   | Raspberry Pi OS Lite |
| `k3s-dev-node-1`   | K3s dev cluster — server + worker (bootstrap) | Raspberry Pi OS Lite |
| `k3s-dev-node-2`   | K3s dev cluster — server + worker             | Raspberry Pi OS Lite |
| `k3s-dev-node-3`   | K3s dev cluster — server + worker             | Raspberry Pi OS Lite |
| `proxmox-node-1`   | Proxmox VE hypervisor                         | Proxmox VE 9.2.2     |
| `rke2-mgmt-node-1` | RKE2 mgmt cluster — server (bootstrap)        | Ubuntu Server 26.04  |
| `rke2-mgmt-node-2` | RKE2 mgmt cluster — server                    | Ubuntu Server 26.04  |
| `rke2-mgmt-node-3` | RKE2 mgmt cluster — server                    | Ubuntu Server 26.04  |

All three k3s-dev nodes run as K3s servers (HA control plane with embedded etcd) and are untainted, so they also schedule workloads. The same shape applies to the three rke2-mgmt VMs running on the Proxmox NUC. K3s-specific ops live in [docs/k3s-ops.md](docs/k3s-ops.md). RKE2-specific ops live in [docs/rke2-mgmt-ops.md](docs/rke2-mgmt-ops.md). Proxmox-specific ops live in [docs/proxmox-ops.md](docs/proxmox-ops.md).

---

## Repo Structure

```text
├── docs
│ ├── k3s-ops.md
│ ├── proxmox-ops.md
│ ├── rke2-mgmt-bootstrap-ops.md
│ └── rke2-mgmt-ops.md
├── inventory
│ ├── group_vars
│ │ ├── all.yml
│ │ ├── k3s-dev.yml
│ │ └── rke2-mgmt
│ │     ├── main.yml
│ │     └── secrets.yml.example
│ ├── host_vars
│ │ └── codeserver-pi.yml
│ └── hosts.ini
├── playbooks
│ ├── codeserver.yml
│ ├── k3s-dev-install.yml
│ ├── k3s-dev-kubeconfig.yml
│ ├── k3s-dev-reboot.yml
│ ├── k3s-dev-remove-node.yml
│ ├── k3s-dev-uninstall.yml
│ ├── k3s-dev-update.yml
│ ├── portainer.yml
│ ├── rke2-mgmt-argocd.yml
│ ├── rke2-mgmt-bootstrap.yml
│ ├── rke2-mgmt-certmanager.yml
│ ├── rke2-mgmt-install.yml
│ ├── rke2-mgmt-kubeconfig.yml
│ ├── rke2-mgmt-rancher.yml
│ ├── rke2-mgmt-reboot.yml
│ ├── rke2-mgmt-remove-node.yml
│ ├── rke2-mgmt-uninstall.yml
│ ├── rke2-mgmt-update.yml
│ ├── roles
│ │ ├── argocd
│ │ │ └── tasks
│ │ │     └── main.yml
│ │ ├── base
│ │ │ └── tasks
│ │ │     └── main.yml
│ │ ├── certmanager
│ │ │ └── tasks
│ │ │     └── main.yml
│ │ ├── codeserver
│ │ │ └── tasks
│ │ │     └── main.yml
│ │ ├── k3s
│ │ │ └── tasks
│ │ │     ├── fetch-kubeconfig.yml
│ │ │     ├── install.yml
│ │ │     ├── main.yml
│ │ │     ├── rpi-prerequisites.yml
│ │ │     ├── uninstall.yml
│ │ │     └── update.yml
│ │ ├── kubevip
│ │ │ ├── files
│ │ │ │ └── kube-vip-rbac.yaml
│ │ │ ├── tasks
│ │ │ │ └── main.yml
│ │ │ └── templates
│ │ │     └── kube-vip.yaml.j2
│ │ ├── portainer
│ │ │ └── tasks
│ │ │     └── main.yml
│ │ ├── proxmox
│ │ │ ├── files
│ │ │ │ └── whiptail
│ │ │ └── tasks
│ │ │     └── main.yml
│ │ ├── rancher
│ │ │ └── tasks
│ │ │     └── main.yml
│ │ └── rke2
│ │     ├── tasks
│ │     │ ├── fetch-kubeconfig.yml
│ │     │ ├── install.yml
│ │     │ ├── main.yml
│ │     │ ├── prerequisites.yml
│ │     │ ├── safe-reboot.yml
│ │     │ ├── uninstall.yml
│ │     │ └── update.yml
│ │     └── templates
│ │         └── config.yaml.j2
│ └── site.yml
```

### Layout notes

- Groups define behavior rather than host-specific conditionals
- OS management and application management are separate playbooks
- Upgrades are opt-in via tags

## Commands

1. Sanity check

	```bash
	ansible -i inventory/hosts.ini rpis -m ping
	```

2. Apply base config (sans-upgrades)

	```bash
	ansible-playbook -i inventory/hosts.ini playbooks/site.yml --ask-become-pass
	```

3. Perform OS upgrades

	```bash
	ansible-playbook -i inventory/hosts.ini playbooks/site.yml --tags os_upgrade --ask-become-pass
	```

4. Update Portainer

	```bash
	ansible-playbook -i inventory/hosts.ini playbooks/portainer.yml --ask-become-pass
	```

5. Update code-server

	```bash
	ansible-playbook -i inventory/hosts.ini playbooks/codeserver.yml --tags codeserver_update --ask-become-pass
	```

6. Reboot manually

	```bash
	ansible-playbook -i inventory/hosts.ini playbooks/site.yml --ask-become-pass --tags manual_reboot
	```

	Non-cluster, non-Proxmox hosts reboot in parallel. K3s dev nodes
	and RKE2 mgmt nodes reboot one at a time with drain → reboot →
	wait-for-Ready → uncordon, so etcd quorum is preserved on each
	cluster. The `os_upgrade` tag uses the same safe path on both when
	`/var/run/reboot-required` is set after the apt upgrade. To reboot
	a single cluster, use
	[`playbooks/k3s-dev-reboot.yml`](docs/k3s-ops.md#safe-rolling-reboot)
	or
	[`playbooks/rke2-mgmt-reboot.yml`](docs/rke2-mgmt-ops.md#safe-rolling-reboot).
	Proxmox hosts are never auto-rebooted by either path — see
	[docs/proxmox-ops.md](docs/proxmox-ops.md#reboot-policy).

For K3s cluster install, upgrades, node add/remove, kubeconfig fetch, snapshots, and health checks, see [docs/k3s-ops.md](docs/k3s-ops.md). The same operations for the RKE2 management cluster live in [docs/rke2-mgmt-ops.md](docs/rke2-mgmt-ops.md). The one-time Helm bootstrap of cert-manager / Rancher / Argo CD that runs after RKE2 is up — and before `homelab-gitops` takes over — lives in [docs/rke2-mgmt-bootstrap-ops.md](docs/rke2-mgmt-bootstrap-ops.md). For Proxmox post-install setup, reboot policy, and verification, see [docs/proxmox-ops.md](docs/proxmox-ops.md).

### Ad-hoc commands

Example:

```bash
ansible -i inventory/hosts.ini rpis \
  -m shell \
  -a "readlink -f /usr/bin/python3 && /usr/bin/python3 --version"
```

### Dry-runs

Add `--check --diff` to commands.

## CI

`.github/workflows/ci.yml` runs on every push and pull request to `main`. The runners have no network path to the hosts, so the workflow covers everything that doesn't require cluster contact:

- **yamllint** — config in `.yamllint.yml`
- **ansible-lint** — config in `.ansible-lint`, currently passing the `production` profile
- **inventory parse** — `ansible-inventory --list`
- **playbook syntax-check** — `ansible-playbook --syntax-check` on every file under `playbooks/`

Required collections are pinned in `requirements.yml`. To run the same checks locally:

```bash
pip install --user ansible-core ansible-lint yamllint
ansible-galaxy collection install -r requirements.yml
yamllint .
ansible-lint
for f in playbooks/*.yml; do
  ansible-playbook --syntax-check "$f" --extra-vars "target_node=k3s-dev-node-3"
done
```

`ansible.cfg` at the repo root sets `inventory = inventory/hosts.ini` and silences the hyphenated-group-name warning, so the `-i inventory/hosts.ini` flag is optional in every command above.
