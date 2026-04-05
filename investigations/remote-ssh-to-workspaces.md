# Remote SSH Access to Agent-Created Workspaces

When OpenSWE creates a DevPod workspace from ECS, the workspace metadata (SSH keys, EC2
instance address, workspace config) is stored in `~/.devpod/` on the ECS container's ephemeral
filesystem — not on a developer's laptop. The local DevPod installation has no knowledge of
that workspace.

**DevPod Pro** solves this with a central server, but it is a paid product.

### DIY Approach

The current architecture is a reasonable foundation. The pieces to add:

1. **Registry** — DynamoDB (or similar) mapping workspace name → EC2 IP + SSH public key,
   written when `devpod up` completes
2. **Key storage** — SSH keys stored in SSM Parameter Store or Secrets Manager so any client
   can retrieve them
3. **Client tooling** — a small CLI or VS Code extension that queries the registry and
   populates `~/.ssh/config` on demand
4. **Network** — workspaces are on Tailscale VPN, so no public IP exposure needed; no bastion
   required, just Tailscale connectivity

The agent already stores the workspace name (`sandbox_id`) in LangGraph thread metadata
(server.py line 325). The workspace name and EC2 IP could be posted as a GitHub comment when
the workspace is created, giving a developer an immediate pointer to the running environment.

### Current Workaround

To SSH into an agent-created workspace today:
1. Find the EC2 instance IP in the AWS console (filter by creation time / tags)
2. Retrieve the SSH key DevPod stored in `~/.devpod/` on the ECS container (before it stops)
3. Adjust the EC2 security group to allow inbound SSH from your IP (or use Tailscale)
