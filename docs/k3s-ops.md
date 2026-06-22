# K3s Cluster Operations

Operational runbook for the `k3s-edge` cluster. Covers bootstrap, day-2 ops (upgrades, snapshots, node add/remove, VIP), and health checks.

All commands assume you are at the repo root (`rpi-ansible/`) on your workstation. The Ansible control node is your laptop; the managed hosts are the Raspberry Pis in `[k3s-edge]`.

---

## Cluster facts

| Item | Value |
|---|---|
| Cluster name | `k3s-edge` |
| Topology | 3 RPi nodes, all K3s servers (HA control plane with embedded etcd) |
| Control-plane VIP | `10.0.3.20` (managed by kube-vip via ARP on `eth0`) |
| Inventory group | `[k3s-edge]` in `inventory/hosts.ini` |
| Group vars | `inventory/group_vars/k3s-edge.yml` |
| Bootstrap node | First host in `[k3s-edge]` (currently `k3s-edge-node-1` / `yavin2`) |
| etcd snapshot location | `/var/lib/rancher/k3s/server/db/snapshots/` on each server |
| etcd snapshot schedule | Every 6 hours (`0 */6 * * *`), retain 10 |
| Kubeconfig destination | `kubeconfig-k3s-edge.yaml` at repo root (gitignored — contains admin cert) |

### Inventory hostname vs. K3s node name

K3s registers each node under its **system hostname** (`hostname --short`), which may differ from its inventory alias. On this cluster:

| Inventory alias | System hostname (K3s node name) |
|---|---|
| `k3s-edge-node-1` | `yavin2` |
| `k3s-edge-node-2` | `yavin3` |
| `k3s-edge-node-3` | `yavin4` |

`kubectl get nodes` shows the system hostname. Ansible inventory uses the alias. Playbooks that need to address a node via `kubectl` (drain, uncordon, delete) read `ansible_hostname` so they target the K3s name, not the inventory alias.

---

## Initial bootstrap

For a brand-new cluster — three Pis, OS already installed, SSH reachable, present in `[rpis]` and `[k3s-edge]`.

```bash
# 1. Apply baseline OS config to every Pi
ansible-playbook -i inventory/hosts.ini playbooks/site.yml --ask-become-pass

# 2. Install K3s (serial: 1, bootstraps node-1, joins the rest)
ansible-playbook -i inventory/hosts.ini playbooks/k3s-edge-install.yml --ask-become-pass

# 3. Fetch the kubeconfig down to the workstation
ansible-playbook -i inventory/hosts.ini playbooks/k3s-edge-kubeconfig.yml --ask-become-pass
sudo chown $USER:staff kubeconfig-k3s-edge.yaml   # workaround until fetch-kubeconfig perms bug is fixed
export KUBECONFIG=$(pwd)/kubeconfig-k3s-edge.yaml

# 4. Validate
kubectl get nodes -o wide
kubectl --server=https://10.0.3.20:6443 get --raw=/healthz
```

The install playbook does a pre-flight integrity check: if the bootstrap node has no K3s but other nodes do, it fails fast. This prevents accidentally forming a new cluster on the bootstrap host and orphaning the rest.

---

## Applying day-2 ops changes to an existing cluster

When the day-2 ops branch (or any future change to `config.yaml` keys, kube-vip manifests, etc.) lands on an existing cluster:

1. **Stage new config and manifests.** Rewrites `/etc/rancher/k3s/config.yaml` and drops kube-vip manifests on the bootstrap node. Does **not** restart K3s — install tasks are skipped because the binary already exists.

   ```bash
   ansible-playbook -i inventory/hosts.ini playbooks/k3s-edge-install.yml --ask-become-pass
   ```

2. **Verify the config landed:**

   ```bash
   ansible -i inventory/hosts.ini k3s-edge -a "grep etcd-snapshot /etc/rancher/k3s/config.yaml" --become
   ```

   All three nodes should show the three `etcd-snapshot-*` lines.

3. **Roll a restart to activate the new config.** This is also the routine upgrade command — it drains each node, re-runs the install script (which restarts K3s and picks up the new config), waits for `Ready`, and uncordons. One node at a time, quorum preserved.

   ```bash
   ansible-playbook -i inventory/hosts.ini playbooks/k3s-edge-update.yml --ask-become-pass
   ```

4. **Run the full health check below.**

kube-vip is staged into `/var/lib/rancher/k3s/server/manifests/` on the bootstrap node and **auto-applied** by K3s — no restart needed for that piece. The DaemonSet reconciles cluster-wide on its own.

---

## Routine upgrades

K3s release is pinned in `inventory/group_vars/k3s-edge.yml`:

- `k3s_channel` — release channel (`stable`, `latest`, `v1.31`, …). Default `stable`.
- `k3s_version` — exact version (e.g. `v1.31.5+k3s1`). Overrides channel when set.

To upgrade, bump one of them, then:

```bash
ansible-playbook -i inventory/hosts.ini playbooks/k3s-edge-update.yml --ask-become-pass
```

The playbook is `serial: 1` with `any_errors_fatal: true`. For each node:

1. Drain (from a peer node, so the drain command keeps working while the target is down).
2. Re-download the install script.
3. Run the install script with the pinned `INSTALL_K3S_CHANNEL` / `INSTALL_K3S_VERSION`. The script restarts K3s in place.
4. Wait for local API on `:6443`.
5. Wait for `kubectl wait --for=condition=Ready node/<name>` from a peer.
6. Uncordon.

If step 5 fails, Ansible halts before touching the next node — etcd quorum (2-of-3) stays intact.

### Tip: config-only restart

If you want to restart K3s to pick up a `config.yaml` change without bumping the version, pin `k3s_version` to whatever `kubectl get nodes -o wide` shows right now, then run the update. The install script will reinstall the same version and restart the service.

---

## Safe rolling reboot

Reboot the cluster one node at a time, preserving etcd quorum the same way the rolling upgrade does. Use this any time something asks for a reboot — OS upgrades that set `/var/run/reboot-required`, kernel changes, firmware updates, manual maintenance, etc.

Entry points:

```bash
# Reboot only the K3s dev cluster.
ansible-playbook -i inventory/hosts.ini playbooks/k3s-edge-reboot.yml --ask-become-pass

# Reboot a single K3s node.
ansible-playbook -i inventory/hosts.ini playbooks/k3s-edge-reboot.yml \
  --ask-become-pass --limit k3s-edge-node-3
```

The K3s reboot path is `serial: 1` with `any_errors_fatal: true`. For each node:

1. Pick a peer in `[k3s-edge]` as the helper for `kubectl` calls.
2. Drain the target (from the helper, so the drain command keeps working while the target is down).
3. Reboot the target. The `ansible.builtin.reboot` module waits for SSH to come back.
4. Wait for the local K3s API on `:6443`.
5. `kubectl wait --for=condition=Ready node/<name>` from the helper.
6. Uncordon.

If step 5 fails, Ansible halts before touching the next node — etcd quorum (2-of-3) stays intact.

When `site.yml --tags os_upgrade` runs, the K3s safe-reboot play also runs, but each host's `safe-reboot.yml` checks `/var/run/reboot-required` first and `end_host`s itself if no reboot is needed. So nodes that didn't need to reboot after `apt upgrade` skip the drain/reboot dance entirely.

The simple `ansible.builtin.reboot` tasks in the `base` role self-skip for hosts in the `k3s-edge` group — the safe-reboot plays own the reboot for those nodes.

---

## Fetching the kubeconfig

```bash
ansible-playbook -i inventory/hosts.ini playbooks/k3s-edge-kubeconfig.yml --ask-become-pass
sudo chown $USER:staff kubeconfig-k3s-edge.yaml   # see Known Issues
export KUBECONFIG=$(pwd)/kubeconfig-k3s-edge.yaml
kubectl get nodes
```

The playbook slurps `/etc/rancher/k3s/k3s.yaml` from the bootstrap node, rewrites the server URL from `127.0.0.1` to the VIP `10.0.3.20`, and writes it to `kubeconfig-k3s-edge.yaml` at the repo root. The VIP-rewritten URL means `kubectl` survives any single server going down.

`export` only affects the current shell. Persist it in your shell rc if you want.

---

## Adding a node

1. Add the host to `[rpis]` and `[k3s-edge]` in `inventory/hosts.ini`.
2. Apply baseline + install. Existing nodes are skipped automatically (`creates: /usr/local/bin/k3s`).

   ```bash
   ansible-playbook -i inventory/hosts.ini playbooks/site.yml --ask-become-pass
   ansible-playbook -i inventory/hosts.ini playbooks/k3s-edge-install.yml --ask-become-pass
   ```

3. Verify with `kubectl get nodes`.

---

## Removing a node

```bash
ansible-playbook -i inventory/hosts.ini playbooks/k3s-edge-remove-node.yml \
  --ask-become-pass \
  --extra-vars "target_node=k3s-edge-node-3"
```

The playbook:
1. Drains the target from a peer.
2. Deletes the node object via `kubectl delete node`.
3. Runs the K3s uninstall script on the target.

**The bootstrap node (first host in `[k3s-edge]`) cannot be removed this way.** It's the source of truth for the join token, and removing it would orphan the rest of the cluster. To replace the bootstrap node, restore from a snapshot or tear the cluster down and reinstall.

After removal, also remove the host from `inventory/hosts.ini`.

---

## etcd snapshots

Embedded etcd writes a snapshot every 6 hours to `/var/lib/rancher/k3s/server/db/snapshots/` on each server, keeping the most recent 10. Knobs are in `inventory/group_vars/k3s-edge.yml`:

- `k3s_etcd_snapshot_cron`
- `k3s_etcd_snapshot_retention`
- `k3s_etcd_snapshot_dir`

Changing any of these requires step 1 (install) to rewrite `config.yaml`, then step 3 (rolling restart) for K3s to re-read it.

List snapshots:

```bash
ansible -i inventory/hosts.ini k3s-edge -a "ls -lh /var/lib/rancher/k3s/server/db/snapshots" --become
```

Snapshot filenames include the **system hostname** of the server that wrote them (e.g. `etcd-snapshot-yavin2-1781064000`), not the inventory alias.

### Restoring etcd from a snapshot

Destructive — wipes existing cluster state. Pick the surviving server with the snapshot you want, then:

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

---

## Control plane VIP (kube-vip)

kube-vip runs as a DaemonSet on every control-plane node. Manifests live in `playbooks/roles/k3s/{files,templates}/`:

- `files/kube-vip-rbac.yaml` — ClusterRole + ClusterRoleBinding + ServiceAccount.
- `templates/kube-vip.yaml.j2` — DaemonSet (templated with `kubevip_iface`, `kubevip_version`, `k3s_vip`).

The install playbook stages these into `/var/lib/rancher/k3s/server/manifests/` **on the bootstrap node only**. K3s reconciles the cluster-wide DaemonSet from there.

kube-vip claims `k3s_vip` (`10.0.3.20`) via ARP on `eth0`, with leader election among the pods. Exactly one node holds the VIP at any time. The K3s server cert includes the VIP as a SAN, so `kubectl` targeting `https://10.0.3.20:6443` survives any single server going down.

To change kube-vip itself: bump `kubevip_version` (or edit the template) in `inventory/group_vars/k3s-edge.yml`, then re-run the install playbook. K3s reconciles the updated manifest in place — no restart needed.

### Failover test

ARP-based VIP failover usually works, but "usually" is the part you find out about during a real incident. Run this once after install and any time kube-vip is upgraded, so you have confidence the path is real.

1. **Find the current VIP holder.**

   ```bash
   ansible -i inventory/hosts.ini k3s-edge -a "ip -4 addr show eth0" --become | grep -E "10\.0\.3\.20|CHANGED"
   ```

   Exactly one node should list `10.0.3.20/32`. Note which one — call it `<holder>`.

2. **Start a watcher in a second terminal.** This polls the API through the VIP once a second so you can see exactly how long the blip lasts.

   ```bash
   while true; do
     date +%H:%M:%S
     kubectl --server=https://10.0.3.20:6443 get --raw=/healthz || echo FAIL
     sleep 1
   done
   ```

3. **Take the holder down.** Either a graceful shutdown or a hard power cut — both are valid tests, but graceful is friendlier to etcd.

   ```bash
   ansible -i inventory/hosts.ini <holder> -a "shutdown -h now" --become
   ```

4. **Watch the failover.** The watcher should show a few seconds of failures, then `ok` resume as another kube-vip pod wins the leader election and claims the VIP via gratuitous ARP. Typical blip: 3–10 seconds. Anything over ~30 seconds is suspect.

5. **Confirm the VIP moved.**

   ```bash
   ansible -i inventory/hosts.ini k3s-edge -a "ip -4 addr show eth0" --become | grep -E "10\.0\.3\.20|CHANGED"
   ```

   A *different* node should now hold `10.0.3.20/32`. The original `<holder>` will show `CHANGED => UNREACHABLE` — expected, it's powered off.

6. **Bring the original holder back up.** Power it on, wait for SSH, then verify k3s came back cleanly:

   ```bash
   ansible -i inventory/hosts.ini <holder> -a "systemctl is-active k3s" --become
   kubectl get nodes
   ```

   The node should return to `Ready`. The VIP will *not* move back automatically — kube-vip's leader election is sticky, and there's no reason to flap it. To force the VIP back, repeat the test against the new holder.

If failover doesn't happen, check kube-vip pod logs (`kubectl -n kube-system logs ds/kube-vip-ds`) on the surviving nodes for leader-election errors, and confirm `kubevip_iface` in `inventory/group_vars/k3s-edge.yml` actually matches the interface holding the LAN address on all three Pis.

---

## Cert rotation

K3s issues its own internal PKI: server, etcd peer/client, kube-apiserver, kubelet, admin client, etc. Two layers, two cadences:

| Layer | Validity | Rotation |
|---|---|---|
| Leaf certs (server, etcd, apiserver, kubelet, admin client) | 12 months | **Automatic** on k3s service start if within 90 days of expiry |
| Root CAs | 10 years | Manual via `k3s certificate rotate-ca` when the day comes |

The practical rule: **restart k3s on every server at least once every ~9 months and leaf certs rotate themselves.** The existing workflows already cover this incidentally — `k3s-edge-update.yml`, `k3s-edge-reboot.yml`, and any `site.yml --tags os_upgrade` run that triggers a safe-reboot all restart k3s. As long as the cluster isn't sitting completely untouched for a full year, certs stay fresh.

If you've left the cluster alone too long and a cert *has* expired, recovery is:

```bash
# On each server
sudo k3s certificate rotate
sudo systemctl restart k3s
```

### Forcing a rotation without a version bump

When no upgrade is pending but you want to refresh certs (e.g. you noticed it's been 11 months), use the config-only restart pattern from [Routine upgrades](#routine-upgrades): pin `k3s_version` to whatever `kubectl get nodes -o wide` currently shows, then run `k3s-edge-update.yml`. The rolling restart triggers auto-rotation on each node.

### Local kubeconfig

The admin client cert embedded in `kubeconfig-k3s-edge.yaml` is on the same 12-month cycle. After any rotation event, re-fetch it:

```bash
ansible-playbook -i inventory/hosts.ini playbooks/k3s-edge-kubeconfig.yml --ask-become-pass
```

Otherwise `kubectl` from the workstation will start failing TLS handshakes while the cluster itself is healthy.

---

## Health checks

Run after install, after every upgrade, after a node add/remove, and as routine maintenance. Requires `KUBECONFIG` set to the local kubeconfig.

```bash
export KUBECONFIG=$(pwd)/kubeconfig-k3s-edge.yaml
```

| Check | Command | Healthy result |
|---|---|---|
| All nodes Ready | `kubectl get nodes` | 3 nodes, all `Ready`, role column shows `control-plane,etcd` |
| Versions consistent | `kubectl get nodes -o wide` | `VERSION` column matches across all 3 nodes |
| Nothing cordoned | `kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.unschedulable}{"\n"}{end}'` | Each node prints empty / `<no value>` |
| Control plane pods | `kubectl -n kube-system get pods` | `coredns`, `local-path-provisioner`, `metrics-server`, `traefik`, `kube-vip-ds-*` all `Running` (`helm-install-*` `Completed` is normal) |
| kube-vip claimed VIP | `ansible -i inventory/hosts.ini k3s-edge -a "ip -4 addr show eth0" --become \| grep -E "10\\.0\\.3\\.20\|CHANGED"` | Exactly one node lists `10.0.3.20/32` on `eth0` (the `deprecated` flag is normal) |
| API reachable via VIP (auth'd) | `kubectl --server=https://10.0.3.20:6443 get --raw=/healthz` | `ok` |
| API reachable via VIP (unauth'd) | `ansible -i inventory/hosts.ini 'k3s-edge[0]' -a "curl -sk https://10.0.3.20:6443/healthz" --become` | `Unauthorized` 401 — passes because TLS + routing worked |
| etcd quorum | `kubectl get --raw=/healthz/etcd` | `ok` |
| etcd members | `kubectl get --raw='/healthz/etcd?verbose'` | All 3 servers healthy |
| No stuck pods | `kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded` | No rows |
| Snapshot freshness | `ansible -i inventory/hosts.ini k3s-edge -a "ls -t /var/lib/rancher/k3s/server/db/snapshots \| head -1" --become` | Newest file on each server within the last 6 hours |
| Cert expiry | `ansible -i inventory/hosts.ini k3s-edge -a "k3s certificate check" --become` | All certs > 90 days from expiry; anything closer will auto-rotate on next k3s restart |
| Service units active | `ansible -i inventory/hosts.ini k3s-edge -a "systemctl is-active k3s" --become` | All `active` |

---

## Common workflows

### "I want to upgrade K3s"

1. Bump `k3s_channel` or `k3s_version` in `inventory/group_vars/k3s-edge.yml`.
2. `ansible-playbook -i inventory/hosts.ini playbooks/k3s-edge-update.yml --ask-become-pass`
3. Run the health checks.

### "I want to reboot the cluster"

See [Safe rolling reboot](#safe-rolling-reboot). One node at a time, drained, quorum preserved.

### "I changed something in `config.yaml` / kube-vip / group_vars"

1. `ansible-playbook -i inventory/hosts.ini playbooks/k3s-edge-install.yml --ask-become-pass` — stages new files. Idempotent.
2. If the change requires a K3s restart (anything in `config.yaml`), run the update playbook to roll a restart through the cluster.
3. If the change is only kube-vip manifests, no restart needed — K3s auto-reconciles.

### "I want to add a node"

See [Adding a node](#adding-a-node).

### "I want to remove a node"

See [Removing a node](#removing-a-node). Bootstrap node excluded.

### "A node is broken and I need to recover"

- Non-bootstrap node — remove and re-add. Cluster keeps running on the other two.
- Bootstrap node — restore from etcd snapshot, or full teardown + reinstall.

### "I need to wipe the cluster"

```bash
ansible-playbook -i inventory/hosts.ini playbooks/k3s-edge-uninstall.yml --ask-become-pass
```

Runs uninstall on workers first, then the bootstrap node. Destructive — etcd, workloads, certs all gone.

---

## Bootstrap node fragility

The install role treats the first host in `[k3s-edge]` as the cluster's source of truth — it's where the join token is read from and where kube-vip manifests are staged. If that node is destroyed while others survive, naively re-running install would form a new cluster on it and orphan the rest.

`playbooks/k3s-edge-install.yml` detects this case in pre-flight (`Fail if bootstrap node missing K3s but other nodes have it`) and refuses to run.

Recovery options:
- Restore the bootstrap node from an etcd snapshot (see [Restoring etcd from a snapshot](#restoring-etcd-from-a-snapshot)).
- Tear the cluster down (`k3s-edge-uninstall.yml`) and reinstall fresh.

If you re-order `[k3s-edge]` in `inventory/hosts.ini` so a different host is first, the install playbook will pick a new bootstrap — but doing this on a running cluster will trigger the integrity check unless that new bootstrap also has K3s already installed. Re-ordering is safe; just understand which host is the source of truth.

---

## Security notes

### Cluster join token sensitivity

`/var/lib/rancher/k3s/server/node-token` is effectively a "join my cluster as a server" credential. Anyone holding it with network reachability to the API can register a new control-plane node, which is functionally cluster-admin. Treat it as a high-value secret:

- The token is read on the bootstrap node and passed through Ansible facts to joining nodes during install. It is not written anywhere outside `/var/lib/rancher/k3s/` on the cluster itself.
- The file is mode `0600`, owned by root — only `become: true` access can read it.
- If you suspect compromise, the token can be rotated. K3s supports `k3s token rotate` (newer versions) or manual rotation via the secrets-encryption flow; see [Backlog](#backlog) for the open mitigation task.
- Do not paste this token into chat, screenshots, or commit it to git. It is not stored in this repo and should not be.

---

## Known issues

### `ansible_hostname` deprecation warnings

`playbooks/roles/k3s/tasks/update.yml` and `remove-node.yml` use `ansible_hostname` (the top-level fact form). ansible-core 2.24 will require `ansible_facts['hostname']` instead. Non-blocking; swap when convenient.

### kube-vip address shows `deprecated` flag

`ip -4 addr show eth0` on the VIP holder shows `inet 10.0.3.20/32 scope global deprecated eth0`. This is normal — kube-vip sets a short preferred lifetime on the address. Traffic still flows; it just won't be chosen as a source IP for outbound connections.

---

## Reference: file layout

```
/
├── ansible.cfg
├── inventory/
│   ├── hosts.ini
│   └── group_vars/k3s-edge.yml         # All K3s tunables
├── playbooks/
│   ├── site.yml                       # Baseline OS + safe reboot plays for k3s-edge
│   ├── k3s-edge-install.yml            # Install / re-stage config + manifests
│   ├── k3s-edge-update.yml             # Rolling upgrade or config-only restart
│   ├── k3s-edge-reboot.yml             # Rolling reboot (drain → reboot → wait → uncordon)
│   ├── k3s-edge-kubeconfig.yml         # Fetch kubeconfig with VIP rewritten
│   ├── k3s-edge-remove-node.yml        # Drain + delete + uninstall single node
│   ├── k3s-edge-uninstall.yml          # Full teardown
│   └── roles/k3s/
│       ├── files/kube-vip-rbac.yaml
│       ├── templates/kube-vip.yaml.j2
│       └── tasks/
│           ├── main.yml               # Dispatcher on k3s_action
│           ├── rpi-prerequisites.yml  # iptables, cgroup_memory
│           ├── install.yml            # Bootstrap + join
│           ├── update.yml             # Drain → install → wait → uncordon
│           ├── safe-reboot.yml        # Drain → reboot → wait → uncordon
│           ├── uninstall.yml
│           └── fetch-kubeconfig.yml
└── docs/
    └── k3s-ops.md                     # This file
```

---

## Backlog

Open improvements, tagged `#todo` for grep. Order is roughly by blast-radius if ignored, not priority.

- **#todo Off-host etcd snapshot backup.** Snapshots currently live only on the nodes themselves under `/var/lib/rancher/k3s/server/db/snapshots/`. If all three Pis are lost simultaneously (fire, theft, simultaneous power-event corruption), the snapshots go with them. Options: enable k3s's native S3 backend (`etcd-s3: true` + credentials in `config.yaml`), or a `cron`/systemd-timer `rsync` pulling the newest snapshot from each node to a NAS or workstation.

- **#todo Resilient persistent storage.** K3s ships with `local-path-provisioner`, which pins each PV to the node that provisioned it. If that node dies, the PV's data is unrecoverable and the pod cannot reschedule. Fine for ephemeral dev workloads; not fine for anything stateful you care about. Options: Longhorn (replicated block storage across the three Pis), NFS from a NAS, or document the limitation and consciously avoid stateful workloads on this cluster.

- **#todo Monitoring and alerting.** The health-check table is a manual procedure — useful when sitting at the workstation, useless at 3am when etcd loses a member or kube-vip stops failing over. Minimal viable setup: Prometheus + Alertmanager in-cluster, or scrape the existing k3s metrics endpoint from an external Grafana Cloud free tier. Alert on at least: node NotReady, etcd quorum loss, cert expiry < 30 days, VIP not bound on any node.

- **#todo Join token rotation procedure.** See [Cluster join token sensitivity](#cluster-join-token-sensitivity). Document and ideally automate a rotation procedure — `k3s token rotate --new-token <value>` on a server, then update any cached copies. Test that joining nodes still works with the rotated token before considering this done.

- **#todo Image pull-through cache.** All three Pis pull images independently from public registries. Over time this hits Docker Hub anonymous pull rate limits (cryptic `ImagePullBackOff` with `toomanyrequests`) and burns home-internet bandwidth. Mitigation: run a registry in mirror mode (e.g. on the workstation or a NAS) and point k3s at it via `/etc/rancher/k3s/registries.yaml`.

- **#todo Workload / PV backup.** etcd snapshots restore the cluster's idea of what should be running, not the contents of PVs. Once stateful workloads exist on the cluster, a second backup track is needed: Velero is the standard; for a homelab a scripted `tar` of the PV directories to off-host storage is sufficient. Depends on the [resilient persistent storage](#backlog) decision — backup approach differs for local-path vs. Longhorn vs. NFS.
