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
│ ├── k3s-dev-uninstall.yml
│ ├── portainer.yml
│ ├── roles
│ │ ├── base
│ │ │ └── tasks
│ │ │     └── main.yml
│ │ ├── codeserver
│ │ │ └── tasks
│ │ │     └── main.yml
│ │ ├── k3s
│ │ │ └── tasks
│ │ │     ├── install.yml
│ │ │     ├── main.yml
│ │ │     ├── rpi-prerequisites.yml
│ │ │     └── uninstall.yml
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

### Ad-hoc commands

Example:

```bash
ansible -i inventory/hosts.ini rpis \
  -m shell \
  -a "readlink -f /usr/bin/python3 && /usr/bin/python3 --version"
```

### Dry-runs

Add `--check --deff` to commands.
