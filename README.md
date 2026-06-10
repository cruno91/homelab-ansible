# Ansible Management for Homelab

This repo manages OSes for the fleet of hardware and VMs running in the homelab.

Current roles:
- Apply a safe baseline for all OSes
- Manage tagged upgrades
- Manage Portainer for the simple Docker containers needed in the lab
- Manage code-server
- Provision and tear down a K3s dev cluster on Raspberry Pis
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

All three k3s-dev nodes run as K3s servers (HA control plane with embedded etcd) and are untainted, so they also schedule workloads.

---

## Repo Structure

```text
├── inventory
│ ├── group_vars
│ │ ├── all.yml
│ │ └── k3s-dev.yml
│ ├── host_vars
│ │ └── codeserver-pi.yml
│ └── hosts.ini
├── playbooks
│ ├── codeserver.yml
│ ├── k3s-dev-install.yml
│ ├── k3s-dev-kubeconfig.yml
│ ├── k3s-dev-remove-node.yml
│ ├── k3s-dev-uninstall.yml
│ ├── k3s-dev-update.yml
│ ├── portainer.yml
│ ├── roles
│ │ ├── base
│ │ │ └── tasks
│ │ │     └── main.yml
│ │ ├── codeserver
│ │ │ └── tasks
│ │ │     └── main.yml
│ │ ├── k3s
│ │ │ ├── files
│ │ │ │ └── kube-vip-rbac.yaml
│ │ │ ├── tasks
│ │ │ │ ├── fetch-kubeconfig.yml
│ │ │ │ ├── install.yml
│ │ │ │ ├── main.yml
│ │ │ │ ├── rpi-prerequisites.yml
│ │ │ │ ├── uninstall.yml
│ │ │ │ └── update.yml
│ │ │ └── templates
│ │ │     └── kube-vip.yaml.j2
│ │ └── portainer
│ │     └── tasks
│ │         └── main.yml
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

7. Install the K3s dev cluster

	Bootstraps `k3s-dev-node-1` with `server --cluster-init` and joins the remaining nodes as additional servers. Enables `cgroup_memory` and reboots each Pi if needed.

	```bash
	ansible-playbook -i inventory/hosts.ini playbooks/k3s-dev-install.yml --ask-become-pass
	```

8. Uninstall the K3s dev cluster

	Tears down non-primary nodes first, then the primary.

	```bash
	ansible-playbook -i inventory/hosts.ini playbooks/k3s-dev-uninstall.yml --ask-become-pass
	```

9. Roll a K3s upgrade across the cluster

	Drains each node in turn, re-runs the install script with the pinned channel/version, waits for `Ready`, and uncordons. Bump `k3s_channel` or `k3s_version` in `inventory/group_vars/k3s-dev.yml` before running.

	```bash
	ansible-playbook -i inventory/hosts.ini playbooks/k3s-dev-update.yml --ask-become-pass
	```

10. Fetch the kubeconfig for local `kubectl`

	Writes `kubeconfig-k3s-dev.yaml` at the repo root with the server URL rewritten to the kube-vip VIP.

	```bash
	ansible-playbook -i inventory/hosts.ini playbooks/k3s-dev-kubeconfig.yml --ask-become-pass
	export KUBECONFIG=$(pwd)/kubeconfig-k3s-dev.yaml
	kubectl get nodes
	```

11. Remove a K3s node from the cluster

	Drains it from a peer, deletes the node object, then uninstalls K3s on the target. The bootstrap node cannot be removed this way.

	```bash
	ansible-playbook -i inventory/hosts.ini playbooks/k3s-dev-remove-node.yml \
	  --ask-become-pass \
	  --extra-vars "target_node=k3s-dev-node-3"
	```

## K3s Day-2 Operations

### Version pinning and upgrades

`inventory/group_vars/k3s-dev.yml` controls the K3s release:

- `k3s_channel` — release channel (`stable`, `latest`, `v1.31`, …). Default `stable`.
- `k3s_version` — exact version (e.g. `v1.31.5+k3s1`). Overrides channel when set.

Bump either, then run `playbooks/k3s-dev-update.yml`. The playbook is `serial: 1` and drains each node before upgrading, so quorum (2-of-3) and workloads stay up. Verify with the health checks below afterward.

### Control plane VIP (kube-vip)

kube-vip is deployed as a DaemonSet on every control-plane node. Manifests live in `playbooks/roles/k3s/{files,templates}/` and are staged into `/var/lib/rancher/k3s/server/manifests/` on the bootstrap node; K3s auto-applies them and reconciles cluster-wide. kube-vip claims `k3s_vip` (default `10.0.3.20`) via ARP on `eth0`, with leader election among the pods. The K3s server cert includes the VIP as a SAN, so `kubectl` can target `https://10.0.3.20:6443` and survive any single server going down.

To change kube-vip itself: bump `kubevip_version` (or edit the template), then re-run `playbooks/k3s-dev-install.yml`. K3s reconciles the updated manifest in place.

### etcd snapshots

Embedded etcd snapshots every 6 hours, retaining 10, to `/var/lib/rancher/k3s/server/db/snapshots` on each server (`k3s_etcd_snapshot_*` vars). List them:

```bash
ansible -i inventory/hosts.ini k3s-dev -a "ls -lh /var/lib/rancher/k3s/server/db/snapshots" --become
```

### Restoring etcd from a snapshot

This is destructive — it wipes existing cluster state. Pick the surviving server with the snapshot you want, then:

```bash
# On the chosen server
sudo systemctl stop k3s
sudo k3s server \
  --cluster-reset \
  --cluster-reset-restore-path=/var/lib/rancher/k3s/server/db/snapshots/<snapshot-file>
sudo systemctl start k3s

# On every other server, after the chosen one is back up
sudo systemctl stop k3s
sudo rm -rf /var/lib/rancher/k3s/server/db
sudo systemctl start k3s
```

### Adding a node

1. Add the host to `[rpis]` and `[k3s-dev]` in `inventory/hosts.ini`.
2. Re-run `playbooks/k3s-dev-install.yml`. Existing nodes are skipped (`creates: /usr/local/bin/k3s`) and the new one joins as a server.

### Removing a node

Use Command 11. The bootstrap node cannot be removed this way — restore from a snapshot if you need to replace it.

### Bootstrap node fragility

The install role treats the first host in `[k3s-dev]` as the cluster's source of truth (where the join token is read from). If that node is destroyed while others survive, re-running install would form a new cluster on the bootstrap host and orphan the rest. `playbooks/k3s-dev-install.yml` detects this and fails fast. Recover by restoring the bootstrap node from a snapshot, or by tearing the whole cluster down and reinstalling.

## Cluster Health Checks

Run after install, after every upgrade, after a node add/remove, and as part of routine maintenance. Fetch the kubeconfig first (Command 10) so `kubectl` works from your workstation:

```bash
export KUBECONFIG=$(pwd)/kubeconfig-k3s-dev.yaml
```

| Check                  | Command                                                                                                                     | Healthy result                                                                                          |
|------------------------|-----------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------|
| All nodes Ready        | `kubectl get nodes`                                                                                                         | 3 nodes, all `Ready`, role column includes `control-plane,etcd,master`                                  |
| Versions consistent    | `kubectl get nodes -o wide`                                                                                                 | `VERSION` column matches across all 3 nodes (mismatch = mid-upgrade or failed upgrade)                  |
| Nothing cordoned       | `kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.unschedulable}{"\n"}{end}'`                    | Each node prints empty / `<no value>` (anything `true` means a drain didn't get uncordoned)             |
| Control plane pods     | `kubectl -n kube-system get pods`                                                                                           | `coredns`, `local-path-provisioner`, `metrics-server`, `traefik`, and `kube-vip-ds-*` are all `Running` |
| kube-vip claimed VIP   | `ansible -i inventory/hosts.ini k3s-dev -a "ip -4 addr show eth0"`                                                          | Exactly one node lists `10.0.3.20` as a secondary address on `eth0`                                     |
| API reachable via VIP  | `kubectl --server=https://10.0.3.20:6443 get --raw=/healthz`                                                                | `ok`                                                                                                    |
| etcd quorum            | `ansible -i inventory/hosts.ini k3s-dev[0] -a "k3s kubectl get --raw=/healthz/etcd" --become`                                | `ok`                                                                                                    |
| etcd members           | `ansible -i inventory/hosts.ini k3s-dev[0] -a "k3s kubectl get --raw=/healthz/etcd?verbose" --become`                        | Shows all 3 servers as healthy                                                                          |
| No stuck pods          | `kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded`                                        | No rows                                                                                                 |
| Snapshot freshness     | `ansible -i inventory/hosts.ini k3s-dev -a "ls -t /var/lib/rancher/k3s/server/db/snapshots \| head -1" --become`             | Newest file on each server is within the last 6 hours                                                   |
| Service units active   | `ansible -i inventory/hosts.ini k3s-dev -a "systemctl is-active k3s"`                                                       | All `active`                                                                                            |

If a check fails, the typical remediation is in the section above (snapshot restore, drain/uncordon manually, re-run install for a missing node, etc.).

### Ad-hoc commands

Example:

```bash
ansible -i inventory/hosts.ini rpis \
  -m shell \
  -a "readlink -f /usr/bin/python3 && /usr/bin/python3 --version"
```

### Dry-runs

Add `--check --deff` to commands.
