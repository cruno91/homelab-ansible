# RKE2 Management Cluster Operations

Operational runbook for the `rke2-mgmt` cluster — the homelab's management plane that will host Rancher, Argo CD, Harbor, and the central observability backend.

The role is modeled on `playbooks/roles/k3s/`. Most patterns (action-dispatch `main.yml`, `serial: 1` rolling ops, kube-vip via the server-manifests dir, VIP rewrite on kubeconfig fetch) are identical — see [docs/k3s-ops.md](k3s-ops.md) for the deeper writeup of *why* those patterns exist. This doc focuses on the parts that differ.

---

## Cluster facts

| Item | Value |
|---|---|
| Cluster name | `rke2-mgmt` |
| Topology | 3 Ubuntu Server VMs on the Proxmox NUC, all RKE2 servers (HA control plane with embedded etcd) |
| Control-plane VIP | `10.0.3.40` (managed by kube-vip via ARP) |
| API port | `6443` |
| Supervisor / join port | `9345` (new-server registration) |
| Inventory group | `[rke2-mgmt]` in `inventory/hosts.ini` |
| Group vars | `inventory/group_vars/rke2-mgmt.yml` |
| Bootstrap node | First host in `[rke2-mgmt]` (currently `rke2-mgmt-node-1` / `10.0.3.41`) |
| etcd snapshot location | `/var/lib/rancher/rke2/server/db/snapshots/` on each server |
| etcd snapshot schedule | Every 6 hours (`0 */6 * * *`), retain 10 |
| Kubeconfig destination | `kubeconfig-rke2-mgmt.yaml` at repo root (gitignored — contains admin cert) |

The VMs are provisioned by [`homelab-tofu`](https://github.com/cruno91/homelab-tofu) from a cloud-init Ubuntu Server 26.04 template on Proxmox.

### Inventory hostname vs. RKE2 node name

RKE2 registers each node under its system hostname (`hostname --short`), which may differ from the Ansible inventory alias. `kubectl get nodes` shows the system hostname; Ansible inventory uses the alias. Tasks that call `kubectl` against a node by name use `ansible_hostname` so they target the registered name.

---

## How RKE2 differs from K3s in this repo

| | K3s (`k3s-edge`) | RKE2 (`rke2-mgmt`) |
|---|---|---|
| Install script | `https://get.k3s.io` | `https://get.rke2.io` |
| systemd unit | `k3s.service` | `rke2-server.service` |
| Service start | Install script starts it | Install script does **not** start it — role enables + starts explicitly |
| Config file | `/etc/rancher/k3s/config.yaml` | `/etc/rancher/rke2/config.yaml` |
| Data dir | `/var/lib/rancher/k3s/` | `/var/lib/rancher/rke2/` |
| Server-manifests dir (kube-vip lives here) | `/var/lib/rancher/k3s/server/manifests/` | `/var/lib/rancher/rke2/server/manifests/` |
| Join port | `6443` (unified) | `9345` (supervisor) — distinct from kube API |
| Server / token passed to joiners via | env vars (`K3S_URL`, `K3S_TOKEN`) | `config.yaml` keys (`server:`, `token:`) |
| `kubectl` on PATH | yes (`/usr/local/bin/k3s` → `kubectl` symlink) | no — binaries under `/var/lib/rancher/rke2/bin/`; role installs `/etc/profile.d/rke2.sh` to fix this for SSH sessions |
| Uninstall | `/usr/local/bin/k3s-uninstall.sh` | `/usr/local/bin/rke2-killall.sh` → `/usr/local/bin/rke2-uninstall.sh` |
| Prereqs | RPi-specific (cgroup_memory in `/boot/firmware/cmdline.txt`) | Ubuntu VM-flavored (apt deps, swapoff) |

Everything else — kube-vip ARP VIP, `tls-san` includes the VIP, etcd snapshots, serial-1 drain/upgrade/reboot/uncordon — is the same shape.

---

## Initial bootstrap

For a brand-new cluster — three Ubuntu VMs already provisioned, SSH reachable from the workstation as `ubuntu`, present in `[rke2-mgmt]`.

```bash
# 1. Apply baseline OS config to every VM
ansible-playbook playbooks/site.yml --limit rke2-mgmt --ask-become-pass

# 2. Install RKE2 (serial: 1, bootstraps node-1, joins the rest)
ansible-playbook playbooks/rke2-mgmt-install.yml --ask-become-pass

# 3. Fetch the kubeconfig down to the workstation
ansible-playbook playbooks/rke2-mgmt-kubeconfig.yml --ask-become-pass
export KUBECONFIG=$(pwd)/kubeconfig-rke2-mgmt.yaml

# 4. Validate
kubectl get nodes -o wide
kubectl --server=https://10.0.3.40:6443 get --raw=/healthz
```

If the bootstrap node has no RKE2 but other nodes do, it fails fast. This prevents accidentally forming a new cluster on the bootstrap host and orphaning the rest.

---

## Routine upgrades

RKE2 release is pinned in `inventory/group_vars/rke2-mgmt.yml`:

- `rke2_channel` — release channel (`stable`, `latest`, `v1.31`, …). Default `stable`.
- `rke2_version` — exact version (e.g. `v1.31.5+rke2r1`). Overrides channel when set.

To upgrade, bump one of them, then:

```bash
ansible-playbook playbooks/rke2-mgmt-update.yml --ask-become-pass
```

`serial: 1` with `any_errors_fatal: true`. For each node:

1. Drain (from a peer node).
2. Re-download the install script.
3. Run the install script with the pinned `INSTALL_RKE2_CHANNEL` / `INSTALL_RKE2_VERSION`.
4. **Restart `rke2-server`** — unlike K3s, RKE2's install script does not restart the service when binaries change, so the role does it explicitly.
5. Wait for local API on `:6443` + `/readyz`.
6. `kubectl wait --for=condition=Ready node/<name>` from a peer.
7. Uncordon.

If step 6 fails, Ansible halts before touching the next node — etcd quorum (2-of-3) stays intact.

### Config-only restart

The install playbook also rewrites `config.yaml` (idempotent — only re-runs install tasks if the binary is missing). To pick up a `config.yaml` change without a version bump, pin `rke2_version` to whatever `kubectl get nodes -o wide` shows right now, then run the update. The rolling restart re-reads the config.

---

## Safe rolling reboot

```bash
# Reboot only the RKE2 mgmt cluster.
ansible-playbook playbooks/rke2-mgmt-reboot.yml --ask-become-pass

# Reboot a single node.
ansible-playbook playbooks/rke2-mgmt-reboot.yml \
  --ask-become-pass --limit rke2-mgmt-node-2
```

Same shape as K3s: `serial: 1`, drain → reboot → wait for local RKE2 API → wait for Ready → uncordon. The OS-upgrade path (`site.yml --tags os_upgrade`) uses the same task file with `rke2_reboot_force: false`, so a node that didn't need to reboot after `apt upgrade` skips the dance entirely.

The base role's simple `ansible.builtin.reboot` tasks self-skip for hosts in the `rke2-mgmt` group — the safe-reboot plays own the reboot for those nodes (parallel to `k3s-edge` and `proxmox`).

---

## Fetching the kubeconfig

```bash
ansible-playbook playbooks/rke2-mgmt-kubeconfig.yml --ask-become-pass
export KUBECONFIG=$(pwd)/kubeconfig-rke2-mgmt.yaml
kubectl get nodes
```

Slurps `/etc/rancher/rke2/rke2.yaml` from the bootstrap node, rewrites the server URL from `127.0.0.1` to the VIP (`10.0.3.40`), renames the cluster/context/user from RKE2's hardcoded `default` to `rke2_cluster_name` (`rke2-mgmt`), and writes to `kubeconfig-rke2-mgmt.yaml` at the repo root. The VIP-rewritten URL means `kubectl` survives any single server going down; the renamed context means the file is self-describing and merge-safe.

---

## Adding / removing a node

Adding:
1. Provision the new VM via `homelab-tofu`.
2. Add the host to `[rke2-mgmt]` in `inventory/hosts.ini`.
3. Apply baseline + install:

   ```bash
   ansible-playbook playbooks/site.yml --limit rke2-mgmt --ask-become-pass
   ansible-playbook playbooks/rke2-mgmt-install.yml --ask-become-pass
   ```

   Existing nodes skip the install (`creates: /usr/local/bin/rke2`).

Removing:

```bash
ansible-playbook playbooks/rke2-mgmt-remove-node.yml \
  --ask-become-pass \
  --extra-vars "target_node=rke2-mgmt-node-3"
```

Drains the target from a peer, deletes the node object, runs the uninstall on the target. The bootstrap node cannot be removed via this playbook — same fragility constraint as K3s.

---

## etcd snapshots

Embedded etcd writes a snapshot every 6 hours to `/var/lib/rancher/rke2/server/db/snapshots/` on each server, keeping the most recent 10. Knobs in `inventory/group_vars/rke2-mgmt.yml`:

- `rke2_etcd_snapshot_cron`
- `rke2_etcd_snapshot_retention`
- `rke2_etcd_snapshot_dir`

List snapshots:

```bash
ansible rke2-mgmt -a "ls -lh /var/lib/rancher/rke2/server/db/snapshots" --become
```

### Restoring etcd from a snapshot

Destructive. On the chosen server:

```bash
sudo systemctl stop rke2-server
sudo rke2 server \
  --cluster-reset \
  --cluster-reset-restore-path=/var/lib/rancher/rke2/server/db/snapshots/<snapshot-file>
sudo systemctl start rke2-server

# On every other server, after the chosen one is back up
sudo systemctl stop rke2-server
sudo rm -rf /var/lib/rancher/rke2/server/db
sudo systemctl start rke2-server
```

---

## Health checks

Requires `KUBECONFIG=$(pwd)/kubeconfig-rke2-mgmt.yaml`.

| Check | Command | Healthy result |
|---|---|---|
| All nodes Ready | `kubectl get nodes` | 3 nodes, all `Ready`, role column shows `control-plane,etcd,master` |
| Versions consistent | `kubectl get nodes -o wide` | `VERSION` column matches across all 3 nodes |
| Control plane pods | `kubectl -n kube-system get pods` | `kube-vip-ds-*`, CNI pods, `etcd-*`, `kube-apiserver-*`, `kube-controller-manager-*`, `kube-scheduler-*` all `Running` |
| kube-vip claimed VIP | `ansible rke2-mgmt -a "ip -4 addr show ens18" --become \| grep -E "10\\.0\\.3\\.40\|CHANGED"` | Exactly one node lists `10.0.3.40/32` |
| API reachable via VIP | `kubectl --server=https://10.0.3.40:6443 get --raw=/healthz` | `ok` |
| Supervisor reachable via VIP | `ansible 'rke2-mgmt[0]' -a "curl -sk https://10.0.3.40:9345/ping" --become` | `pong` |
| etcd quorum | `kubectl get --raw=/healthz/etcd` | `ok` |
| Snapshot freshness | `ansible rke2-mgmt -a "ls -t /var/lib/rancher/rke2/server/db/snapshots \| head -1" --become` | Newest file on each server within the last 6 hours |
| Service units active | `ansible rke2-mgmt -a "systemctl is-active rke2-server" --become` | All `active` |

---

## Node-failure drill (Week 3 "Done when")

The platform plan calls for proving HA survives a node loss:

1. From the workstation, watch the API: `while true; do date +%H:%M:%S; kubectl --server=https://10.0.3.40:6443 get --raw=/healthz; sleep 1; done`.
2. From the Proxmox UI, hard-stop one VM (any node, including the current VIP holder).
3. Watch the VIP move (3–10s typical) and `kubectl get nodes` show the dead node `NotReady` after ~40s.
4. Bring the VM back; `systemctl is-active rke2-server` should report `active` after a minute and the node returns to `Ready`.

Document the observed times in your homelab notes — this is the kind of measurement that holds up in an interview answer.

---

## Reference: file layout

```
/
├── inventory/
│   ├── hosts.ini                       # [rke2-mgmt] group
│   └── group_vars/rke2-mgmt.yml        # All RKE2 tunables
├── playbooks/
│   ├── site.yml                        # Baseline + safe-reboot plays for rke2-mgmt
│   ├── rke2-mgmt-install.yml           # Install / re-stage config + manifests
│   ├── rke2-mgmt-update.yml            # Rolling upgrade or config-only restart
│   ├── rke2-mgmt-reboot.yml            # Rolling reboot (drain → reboot → wait → uncordon)
│   ├── rke2-mgmt-kubeconfig.yml        # Fetch kubeconfig with VIP rewritten
│   ├── rke2-mgmt-remove-node.yml       # Drain + delete + uninstall single node
│   ├── rke2-mgmt-uninstall.yml         # Full teardown
│   └── roles/rke2/
│       ├── files/kube-vip-rbac.yaml
│       ├── templates/
│       │   ├── config.yaml.j2          # First-server vs joining-server config
│       │   └── kube-vip.yaml.j2
│       └── tasks/
│           ├── main.yml                # Dispatcher on rke2_action
│           ├── prerequisites.yml       # apt deps, swapoff
│           ├── install.yml             # Bootstrap + join + enable/start unit
│           ├── update.yml              # Drain → install → restart → wait → uncordon
│           ├── safe-reboot.yml         # Drain → reboot → wait → uncordon
│           ├── uninstall.yml
│           └── fetch-kubeconfig.yml
└── docs/
    └── rke2-mgmt-ops.md                # This file
```
