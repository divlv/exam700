---
name: k3s-remote-diagnostics
description: Diagnose the remote MyWeb K3s cluster through the read-only SSH alias myk3s-ro. Use for failing Pods, Kubernetes Events, K3s service health, Traefik routing, HTTPS and Let's Encrypt ACME failures, Services, Ingresses, PVCs, scheduling, resource pressure, DNS/TLS checks, and remote K3s configuration. Never use it to change the cluster or server.
---

# Remote K3s diagnostics

Use `ssh.exe` directly from native Windows GitHub Copilot CLI.

Do not call a PowerShell wrapper script.
Do not open an unrestricted interactive SSH shell.

## SSH target

Always use the SSH config alias:

```text
myk3s-ro
```

Do not replace it with a literal hostname, IP address, username, or another SSH target.

Connection details, identity, timeout, keepalive, and batch mode are expected to come from the user's `~/.ssh/config`.

## Command runner

Run the remote read-only dispatcher directly:

```powershell
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro <command> <arguments>
```

Examples:

```powershell
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro status
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro resources
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro kube get nodes -o wide
```

Do not override the SSH identity, user, hostname, BatchMode, timeout, or keepalive settings already defined for `myk3s-ro` unless the user explicitly asks to troubleshoot SSH itself.

## Safety boundary

The remote dispatcher and Kubernetes RBAC are authoritative. Still follow these rules:

- Perform diagnosis only.
- Never attempt `apply`, `create`, `delete`, `edit`, `patch`, `replace`, `scale`, `set`, `label`, `annotate`, `taint`, `cordon`, `uncordon`, `drain`, `exec`, `attach`, `cp`, `port-forward`, or `rollout restart`.
- Never override kubeconfig, token, user, context, API server, certificate, or impersonation flags.
- Never request Kubernetes Secrets.
- Read Pod references, Events, ConfigMaps, workload specifications, and logs instead.
- Never use SSH forwarding, SCP, SFTP, rsync, or copy files to/from the server.
- Never add `-L`, `-R`, `-D`, `-W`, or other forwarding/tunnelling options.
- Never request an interactive TTY or unrestricted interactive shell.
- Avoid streaming commands. Use bounded `--since`, `--tail`, and line limits.
- Do not modify local repository files unless the user separately asks for a code or manifest change.
- When proposing a fix, show the exact command or manifest change but do not execute it.
- If a requested operation would modify the remote host or cluster, stop and explain that the skill is read-only.

## Available remote commands

```text
kube <allowlisted read-only kubectl command>
journal [duration] [lines]
read <allowed-path> [start-line] [line-count]
find <allowed-root> [max-depth]
status
resources
ports
dns <hostname>
tls <hostname> [port]
help
```

All Kubernetes investigation commands run remotely with K3s' bundled kubectl and a read-only kubeconfig.

## Investigation workflow

Start broad, then narrow. Keep output small.

### 1. Establish cluster state

```powershell
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro status
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro resources
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro kube get nodes -o wide
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro kube get pods -A -o wide
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro kube get events -A --sort-by=.metadata.creationTimestamp
```

Look first for `Pending`, `CrashLoopBackOff`, `ImagePullBackOff`, `CreateContainerConfigError`, mount failures, failed probes, `OOMKilled`, scheduling failures, and recent Warning Events.

### 2. Diagnose a Pod

```powershell
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro kube describe pod POD_NAME -n NAMESPACE
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro kube logs POD_NAME -n NAMESPACE --all-containers --tail=500
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro kube logs POD_NAME -n NAMESPACE --all-containers --previous --tail=500
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro kube get deployment,statefulset,daemonset,replicaset -n NAMESPACE -o wide
```

Read the owning workload YAML only when needed:

```powershell
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro kube get deployment DEPLOYMENT_NAME -n NAMESPACE -o yaml
```

Correlate container status, exit code, reason, Events, probes, mounts, environment references, `securityContext`, image, requests, and limits.

### 3. Diagnose Service and Ingress routing

```powershell
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro kube get service,endpoints,endpointslice -A -o wide
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro kube get ingress -A -o yaml
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro kube get ingressroute,middleware,traefikservice -A -o yaml
```

Confirm selectors match Pod labels, Service `targetPort` matches the container port, endpoints exist, `spec.ingressClassName` is `traefik` when appropriate, and router entrypoints and TLS resolver names match the installed Traefik configuration.

### 4. Diagnose Traefik and Let's Encrypt

```powershell
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro kube get pods -n kube-system -l app.kubernetes.io/name=traefik -o wide
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro kube logs deployment/traefik -n kube-system --tail=1000
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro kube get helmchart traefik -n kube-system -o yaml
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro kube get helmchartconfig traefik -n kube-system -o yaml
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro kube get service traefik -n kube-system -o yaml
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro read /var/lib/rancher/k3s/server/manifests/traefik-config.yaml
```

Search evidence for ACME resolver mismatch, HTTP challenge entrypoint problems, DNS resolving to the wrong IP, blocked ports 80/443, rate limits, storage permission errors, malformed Ingress TLS configuration, and certificate renewal failures.

For a hostname:

```powershell
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro dns example.com
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro tls example.com 443
```

### 5. Diagnose K3s host-level problems

```powershell
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro journal 2h 1500
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro read /etc/rancher/k3s/config.yaml
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro find /var/lib/rancher/k3s/server/manifests 4
ssh.exe -o ClearAllForwardings=yes -o PermitLocalCommand=no myk3s-ro ports
```

Use host evidence for kubelet/containerd failures, disk pressure, certificate issues, manifest-controller errors, port conflicts, and K3s startup errors.

## SSH failure handling

If `ssh.exe` fails, distinguish transport/authentication problems from cluster problems.

- Exit code `255`: SSH transport, DNS, host-key, authentication, SSH config, or connection problem.
- `Permission denied`: authentication/authorization issue; do not retry with passwords or different identities unless the user explicitly asks.
- Host key verification failure: report it; do not disable host-key checking.
- Timeout/refused: report the endpoint/transport failure; do not treat it as a K3s failure.
- Remote dispatcher denial: report the exact denied command; do not work around the dispatcher.

Do not add `StrictHostKeyChecking=no` or override `UserKnownHostsFile` to bypass SSH trust checks.

## Reporting format

Return:

1. A one- or two-sentence diagnosis.
2. Evidence: exact object names, namespace, relevant Event reason, status, and short log excerpts.
3. Root cause and confidence level.
4. The smallest proposed fix.
5. Read-only verification commands to run after the user applies the fix.

Clearly separate observed facts from inference. Do not claim a root cause when evidence only supports a hypothesis.
