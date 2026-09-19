# Dedicated GPU host deployment

Issue [#534](https://github.com/joemccann/radon/issues/534). Target: the purchased
GEX45-1, HEL1, Ubuntu 24.04 amd64, NVIDIA RTX PRO 4000 Blackwell SFF 24GB.
This is an additional host. Do not run the trading app's `setup-vps.sh` here.

## Scope and release boundary

`cloud/gpu/` is deliberately outside the app host's `cloud/services/` inventory.
An ordinary production deploy cannot install, enable, or restart these units.
Bootstrap prepares the OS; runtime installation leaves inference stopped.
No app environment, model-ladder rung, training job, or adapter promotion changes.

This host serves vLLM privately on **8350**, as requested in #534. Draft #532's
llama.cpp tagger service on localhost **8331** remains a separate implementation.
Do not enable both or point production at the base model as a substitute for a
validated task adapter. Remote ladder integration requires a separate change and
the pinned-test-set evaluation gate. Ports 8340 and 8321 belong to other services.

## Verified runtime pins

| Component | Pin |
|---|---|
| vLLM official image | v0.28.0, linux/amd64, digest in `cloud/gpu/pins.json` |
| Image CUDA | 13.0.2; image architectures include sm_120 |
| Host driver | Ubuntu `nvidia-driver-580-open`, minimum major 580 |
| Base model | Qwen/Qwen2.5-1.5B-Instruct at `989aa7980e4cf806f80c7fef2b1adb7bc71aa306` |

Verified 2026-09-19 against [Docker Hub metadata](https://hub.docker.com/v2/repositories/vllm/vllm-openai/tags/v0.28.0),
the registry manifest/config, [vLLM release](https://github.com/vllm-project/vllm/releases/tag/v0.28.0),
[NVIDIA compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html),
and [Hugging Face revision](https://huggingface.co/api/models/Qwen/Qwen2.5-1.5B-Instruct/revision/989aa7980e4cf806f80c7fef2b1adb7bc71aa306).
OS security package updates remain enabled; the serving image and model are immutable.
GPU execution, memory use, latency, and inference correctness still require the
delivered hardware. Driver 550 from the original issue is insufficient for this image.

## 1. Stage a CI-green release

Verify the new server IP in Robot and its SSH host fingerprint through an
independent trusted channel. Use the existing operator key; never disable SSH
host-key checks. Keep a second root SSH session open during bootstrap.

Set `GPU_HOST` to the purchased host and `GPU_RELEASE` to the reviewed, CI-green
40-character commit. These commands run from the repository on the operator
machine and write only to the new GPU host:

```sh
ssh root@"$GPU_HOST" 'install -d -m 0700 /root/radon-gpu-release'
git archive "$GPU_RELEASE" cloud/gpu |
  ssh root@"$GPU_HOST" 'tar -x -C /root/radon-gpu-release'
scp /Users/joemccann/.ssh/id_ed25519.pub root@"$GPU_HOST":/root/operator.pub
```

The installer refuses source artifacts under writable or non-root-owned
directories. Stage under `/root`, never execute a privileged install from the
`radon` user's mutable checkout. The bootstrap public-key file can contain the
reviewed app-host authorized keys as well as the operator key; never copy private keys.

## 2. Bootstrap Ubuntu

Find the actual public NIC using `ip route get 1.1.1.1`. Supply the operator's
current public IP with `/32` (IPv6 `/128`), not a Tailscale address. On the GPU host:

```sh
bash /root/radon-gpu-release/cloud/gpu/bootstrap.sh \
  --role radon-slm --authorized-keys /root/operator.pub \
  --ssh-cidr YOUR_PUBLIC_IP/32 --public-interface YOUR_PUBLIC_NIC
```

The script rejects app/broker markers, the wrong OS/architecture, malformed keys,
non-public recovery addresses, and privileged preexisting `radon` group membership.
It installs signed NVIDIA/Tailscale repositories, Ubuntu Docker, the open NVIDIA
driver, container toolkit, SSH key authentication, and UFW rules. It never grants
`radon` Docker access or blanket sudo. Repeat with the complete reviewed key list.
The explicit recovery SSH rule remains until tailnet access is verified.

## 3. Enroll and restrict the tailnet

Run on the host, complete the displayed browser login, and assign `tag:radon-slm`
in the existing Tailscale admin console:

```sh
tailscale up --hostname=radon-slm --accept-routes=false --accept-dns=false
tailscale ip -4
```

Review existing tailnet policy first. A new narrow rule does not override an
existing allow-all grant. Permit the app node to `tag:radon-slm` TCP8350 and the
operator/admin nodes to TCP22; deny other peer access to the GPU node. Do not
advertise routes or exit-node service. Avoid reusable auth keys and command-line
secrets. Verify two independent SSH connections before closing public recovery.

Robot is a **stateless public-interface firewall**. It cannot see encrypted
100.x tailnet source addresses. Its public rules must allow UDP41641, required
outbound connection responses (including HTTPS/Tailscale DERP and DNS), and the
specific public recovery SSH address. Apply Hetzner's documented return-traffic
rules before default deny; preserve IPv6 coverage. Never permit public TCP8350.
UFW denies public8350 even if an older broad allow exists. Tailnet TCP22/8350 is
allowed on `tailscale0`; Tailscale policy supplies peer restrictions.

Explicitly reboot after verifying access, reconnect, and check `nvidia-smi`,
`systemctl is-active docker tailscaled`, and `tailscale ip -4`. Do not reuse the
app-host environment file: this host does not need trading credentials.

## 4. Install the disabled runtime

```sh
bash /root/radon-gpu-release/cloud/gpu/install-runtime.sh
```

Installed files:

| Path | Ownership/mode | Purpose |
|---|---|---|
| `/etc/radon/gpu-host` | root, 0644 | Dedicated-host marker |
| `/etc/radon/slm.json` | root:radon, 0640 | Tailnet address, context size, memory cap, adapters |
| `/etc/radon/slm-pins.json` | root:root, 0644 | Reviewed immutable image/model pins |
| `/etc/radon/slm-secrets.env` | root:root, 0600 | Generated `VLLM_API_KEY`; optional `HF_TOKEN` |
| `/usr/local/sbin/radon-slm-runtime` | root:root, 0755 | Fixed Docker verbs with preflight |
| `/var/lib/radon/slm/cache` | radon:radon, 0750 | Model and compilation cache |
| `/var/lib/radon/slm/adapters` | root:radon, 0750 | Reviewed immutable adapter versions |

Set `tailscale_ip` in `slm.json` to the actual address; the placeholder will fail
preflight unless assigned to the node. Initial `adapters` is empty so hardware
commissioning does not require trained files. Configuration is JSON, never
sourced shell. Secrets accept literal tokens only, no shell quotes/expansion.
The public Qwen model does not require an HF token. Transfer the generated API
key through the established secret-management path when app integration is ready.

```sh
radon-slm-runtime pull
radon-slm-runtime check
systemctl start radon-slm
```

`pull` fetches only the pinned official image. `check` verifies private address
assignment, driver version, image availability, secret shape/permissions, and
configured adapter artifacts without starting inference. The service runs the
container as `radon` with a read-only root filesystem, writable cache, read-only
adapters, dropped capabilities, and no runtime LoRA mutation endpoint. The host
Docker helper runs as root and has no `radon` sudo grant. No port is published
through Docker NAT; vLLM binds the specific tailnet address on host networking.

## 5. Commission, then explicitly enable

From the app host, verify private `/health` returns 200, a `/v1/models` request
without bearer authentication returns 401, and the authenticated request returns
200. `/health` itself is intentionally unauthenticated upstream; restrict peers
with Tailscale policy. Verify TCP8350 is unreachable using the public IPv4 and
IPv6 from outside the host. Review `journalctl -u radon-slm` for CUDA/kernel errors.
The first model download/compilation can take several minutes.

Only after those probes succeed: `systemctl enable radon-slm`. Keep the production
ladder rung off. Stop with `systemctl stop radon-slm`; no app service depends on it.
Do not train concurrently with serving until GPU memory/concurrency is measured.

## Adapters, upgrade, and rollback

Copy validated PEFT artifacts to root-owned, non-writable version directories:
`adapters/tagger/v1/adapter_config.json` and `adapter_model.safetensors` (likewise
`distill/v1`). The runtime accepts only these task/version path forms; symlinks
and writable artifact paths are refused. Set `adapters` to
`{"radon-tagger":"tagger/v1"}` only after evaluation. Rank must be <=16 for this
runtime; the training base revision must match the model pin. Context4096 is a
serving starting point, not proof that the full 883-tag training prompt fits.

Back up the prior root-owned config and pins before upgrades. Stage a new
CI-green release, install, pull, check, restart explicitly, and repeat probes.
Rollback by restoring the prior config/pins and restarting; retain the previous
image and adapters until the replacement passes. Service reload is not model
reload. This design uses explicit version paths rather than the issue's mutable
`current` symlink so every restart identifies its adapter version.

Training timers, baseline evaluation, adapter promotion automation, B2 adapter
backup enrollment, watchdog integration, and app ladder changes remain separate
follow-ups. They require either GPU commissioning or the unfinished training
contract in #532. No files here activate those workflows implicitly.
