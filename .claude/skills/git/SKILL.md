---
name: git
description: Use when running Git commands in Codex, in temporary directories, or when cloning public repositories over HTTPS.
---

# Git Skill

## Purpose

Use this skill whenever you need to run Git commands from Codex, especially on Windows 11, in temporary directories, or when cloning public repositories over HTTPS.

The goal is to avoid avoidable first-run Git failures such as Windows `schannel` TLS errors, path collisions, accidental overwrites, and unclear post-clone state.

## When to Use This Skill

Use this skill when the task includes any of the following:

- Clone, checkout, fetch, pull, submodule update, or inspect a Git repository.
- Work with a repository URL, especially an HTTPS GitHub URL.
- Create or use a temporary working directory for repository analysis.
- Run Git from a Windows Codex session.
- The user asks to avoid intermediate Git errors.

## Core Rules

1. Prefer per-command Git configuration over global changes.
2. Do not change the user's global Git configuration unless explicitly requested.
3. On Windows, use OpenSSL backend for HTTPS Git network operations by default.
4. Never overwrite an existing directory unless the user explicitly requested it.
5. Use absolute paths.
6. Verify the result after clone or checkout.
7. Keep logs clear and short.
8. Do not expose secrets, tokens, private URLs with credentials, or Authorization headers.
9. If authentication is required, stop and report the exact authentication requirement instead of guessing credentials.
10. If a command fails, explain the exact failing command and the minimal next fix.

## Windows Git HTTPS Rule

For HTTPS Git operations on Windows, avoid the common `schannel` TLS failure:

```text
schannel: AcquireCredentialsHandle failed: SEC_E_NO_CREDENTIALS
```

Run network Git commands with a per-command OpenSSL backend:

```powershell
git -c http.sslBackend=openssl clone "<repo-url>" "<target-path>"
```

Use the same pattern for other HTTPS network commands:

```powershell
git -C "<repo-path>" -c http.sslBackend=openssl fetch --all --prune
git -C "<repo-path>" -c http.sslBackend=openssl pull --ff-only
git -C "<repo-path>" -c http.sslBackend=openssl submodule update --init --recursive
```

Do not run this unless the user explicitly asks for a persistent change:

```powershell
git config --global http.sslBackend openssl
```

## Safe Clone Workflow

When asked to clone or checkout a repository into a temporary directory, follow this workflow.

### 1. Create a unique temporary root

Use a deterministic root when the user gave one, otherwise create a unique directory under:

```text
C:\tmp\agents
```

Example:

```powershell
$baseRoot = "C:\tmp\agents"
$runId = Get-Random -Minimum 10000 -Maximum 99999
$root = Join-Path $baseRoot $runId

New-Item -ItemType Directory -Path $root -Force | Out-Null
```

### 2. Derive a safe target directory name

Use the repository name without `.git`.

Example:

```powershell
$repoUrl = "https://github.com/LukasNiessen/terrashark"
$repoName = [System.IO.Path]::GetFileNameWithoutExtension(([Uri]$repoUrl).AbsolutePath.TrimEnd('/'))
$target = Join-Path $root $repoName
```

### 3. Avoid overwrite

If the target exists, choose a unique neighboring directory.

```powershell
$baseTarget = $target
$index = 2

while (Test-Path -LiteralPath $target) {
    $target = "${baseTarget}-$index"
    $index++
}
```

### 4. Clone with OpenSSL on Windows

```powershell
git -c http.sslBackend=openssl clone "$repoUrl" "$target"
```

### 5. Verify clone state

```powershell
git -C "$target" status --short --branch
git -C "$target" remote -v
```

Report the final directory and current branch.

## Recommended Reusable Clone Snippet

Use this PowerShell pattern for public HTTPS repositories on Windows:

```powershell
$repoUrl = "https://github.com/LukasNiessen/terrashark"
$baseRoot = "C:\tmp\agents"
$runId = Get-Random -Minimum 10000 -Maximum 99999
$root = Join-Path $baseRoot $runId

New-Item -ItemType Directory -Path $root -Force | Out-Null

$repoName = [System.IO.Path]::GetFileNameWithoutExtension(([Uri]$repoUrl).AbsolutePath.TrimEnd('/'))
if ([string]::IsNullOrWhiteSpace($repoName)) {
    throw "Cannot derive repository name from URL."
}

$target = Join-Path $root $repoName
$baseTarget = $target
$index = 2

while (Test-Path -LiteralPath $target) {
    $target = "${baseTarget}-$index"
    $index++
}

git -c http.sslBackend=openssl clone "$repoUrl" "$target"

if ($LASTEXITCODE -ne 0) {
    throw "Git clone failed with exit code $LASTEXITCODE."
}

git -C "$target" status --short --branch

Write-Host "Cloned to: $target"
```

## Existing Repository Workflow

If the directory already contains a Git repository, do not reclone unless needed.

Check first:

```powershell
git -C "<repo-path>" rev-parse --is-inside-work-tree
git -C "<repo-path>" status --short --branch
```

For updating an existing repository over HTTPS on Windows:

```powershell
git -C "<repo-path>" -c http.sslBackend=openssl fetch --all --prune
git -C "<repo-path>" status --short --branch
```

Use `pull --ff-only` only when the user asked to update the working tree:

```powershell
git -C "<repo-path>" -c http.sslBackend=openssl pull --ff-only
```

## Private Repository Handling

If the repository is private or authentication is required:

1. Do not invent credentials.
2. Do not ask the user to paste tokens into chat.
3. Prefer an already configured credential manager, GitHub CLI, or SSH agent.
4. Report the authentication requirement clearly.

Useful checks:

```powershell
gh auth status
git credential-manager diagnose
ssh -T git@github.com
```

For GitHub repositories where `gh` is already authenticated, this is acceptable:

```powershell
gh repo clone "<owner>/<repo>" "<target-path>"
```

But for plain public HTTPS GitHub repositories, prefer:

```powershell
git -c http.sslBackend=openssl clone "<repo-url>" "<target-path>"
```

## SSH URLs

For SSH repository URLs, do not use `http.sslBackend`.

Use normal Git SSH commands:

```powershell
git clone "git@github.com:<owner>/<repo>.git" "<target-path>"
```

If SSH fails, check:

```powershell
ssh -T git@github.com
```

Do not create or overwrite SSH keys unless explicitly requested.

## Submodules

After cloning a repository that contains submodules, initialize them with the same Windows HTTPS rule:

```powershell
git -C "<repo-path>" -c http.sslBackend=openssl submodule update --init --recursive
```

If submodules use SSH URLs, do not force HTTPS options. Inspect `.gitmodules` first:

```powershell
git -C "<repo-path>" config --file .gitmodules --get-regexp url
```

## Error Handling

### If `schannel` fails

Do not repeat the same plain Git command. Retry once with:

```powershell
git -c http.sslBackend=openssl <original-git-network-command>
```

### If OpenSSL fails with certificate validation

Do not disable SSL verification.

Do not use this except as a last resort and only after user approval:

```powershell
git -c http.sslVerify=false clone "<repo-url>" "<target-path>"
```

Instead, report that local CA/certificate configuration must be fixed.

### If the target directory is not empty

Do not delete it automatically. Choose a unique sibling directory or ask only if deletion is required.

### If the clone partially created the target directory

Before retrying, remove only the failed target directory that was created by the current command, not any user-owned directory.

```powershell
if (Test-Path -LiteralPath "$target") {
    Remove-Item -LiteralPath "$target" -Recurse -Force
}
```

Use this only when the target path was created by the current operation and is safe to remove.

## Reporting Format

After a successful clone, report only the essentials:

```text
Done. Repository cloned to:

C:\tmp\agents\<run-id>\<repo-name>

Current branch:
main
```

If a command failed, report:

```text
Git command failed:
<command>

Error:
<short error>

Next safe fix:
<fix>
```

## Do Not Do

- Do not modify global Git configuration without explicit user approval.
- Do not disable SSL verification by default.
- Do not overwrite existing directories.
- Do not delete user directories to retry a clone.
- Do not expose tokens, credentials, or private key material.
- Do not switch from HTTPS to SSH unless the repository URL or user request requires it.
- Do not proceed with private repositories when authentication is missing.
