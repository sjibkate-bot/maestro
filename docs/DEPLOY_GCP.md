# Deploy Maestro-SC on Google Cloud (option A: Anthropic API key)

This guide puts Maestro-SC on one Google Cloud virtual machine (VM) for a customer demo:

```
 people ── HTTPS ──▶ Caddy (certificate) ──▶ Maestro-SC app ──▶ MongoDB
                                                  │
                                                  └── HTTPS ──▶ api.anthropic.com (Claude)
```

* **One VM** runs three containers: Caddy, which gets the HTTPS certificate automatically; the app; and MongoDB. Only ports 80 and 443 are open. The app and the database can't be reached from outside.
* **Claude** is called with your Anthropic API key, exactly as on your laptop. No code changes.
* **Secrets** (the API key and the first admin's passcode) are kept in Google Secret Manager, not in files on the VM.
* **Sign-in is on**, and the session cookie is only sent over HTTPS.

The files used are in [`deploy/gcp/`](../deploy/gcp): `docker-compose.yml`, `Caddyfile`, `start.sh` and `maestro.env.example`.

Time needed: about 45 minutes the first time.

---

## Before you start

You need:

1. **A Google Cloud project with billing turned on.** Use a project only for this demo if you can. Everything below runs in **Cloud Shell** (the `>_` button at the top of the Google Cloud console), which already has `gcloud` installed and signed in.
2. **A new Anthropic API key**, from the Claude Console (platform.claude.com → API keys). Create a fresh key for this server. Don't reuse a key that has been pasted anywhere. While you're there, set a monthly spend limit for the workspace.
3. **An address for the demo**, for example `maestro.yourcompany.com`, and access to its DNS settings so you can add one record. (For a quick private test without a domain, see step 7.)
4. **Access to the code.** If the GitHub repository is private, create a GitHub *fine-grained personal access token* with **read-only "Contents"** access to `sjibkate-bot/maestro` only. You'll paste it once when cloning.

> **Data rule for the Air Force demo:** use sample or demo data. Don't load real Air Force data, especially anything that is Controlled Unclassified Information (CUI) or export-controlled, until the customer's security office has approved this hosting and the use of Claude. The B-52 demo pack is built from customer files, so check with them before loading even that.

---

## Step 1: Set your project and region

In Cloud Shell, set these once. Change the project ID; a US region is the sensible choice for a US government demo.

```bash
export PROJECT=your-project-id
export REGION=us-east4
export ZONE=us-east4-a
gcloud config set project $PROJECT
```

Turn on the services used:

```bash
gcloud services enable compute.googleapis.com secretmanager.googleapis.com iap.googleapis.com
```

> If Cloud Shell restarts, run the three `export` lines again before continuing.

## Step 2: Store the two secrets

Each command waits for you to paste a value. Nothing is shown on screen or saved in your shell history.

The Anthropic API key:

```bash
read -rsp "Anthropic API key: " V; echo; printf '%s' "$V" | gcloud secrets create maestro-anthropic-api-key --replication-policy=automatic --data-file=-; unset V
```

The passcode for the first administrator (at least 10 characters, with letters and digits):

```bash
read -rsp "Admin passcode: " V; echo; printf '%s' "$V" | gcloud secrets create maestro-admin-password --replication-policy=automatic --data-file=-; unset V
```

## Step 3: Create a service account for the VM

The VM uses this identity to read the two secrets, and nothing else.

```bash
gcloud iam service-accounts create maestro-vm --display-name="Maestro-SC VM"
```

```bash
for S in maestro-anthropic-api-key maestro-admin-password; do
  gcloud secrets add-iam-policy-binding $S \
    --member="serviceAccount:maestro-vm@${PROJECT}.iam.gserviceaccount.com" \
    --role=roles/secretmanager.secretAccessor
done
```

## Step 4: Reserve a fixed IP address

```bash
gcloud compute addresses create maestro-ip --region=$REGION
```

```bash
export IP=$(gcloud compute addresses describe maestro-ip --region=$REGION --format='value(address)'); echo $IP
```

Write the IP down; you need it for DNS in step 7.

## Step 5: Open the firewall

Web traffic (80 for the certificate check and the redirect to HTTPS, and 443 for the app):

```bash
gcloud compute firewall-rules create maestro-allow-web --network=default --direction=INGRESS \
  --action=ALLOW --rules=tcp:80,tcp:443 --source-ranges=0.0.0.0/0 --target-tags=maestro-web
```

SSH only through Google's Identity-Aware Proxy (IAP), so the VM's SSH port is never open to the internet:

```bash
gcloud compute firewall-rules create maestro-allow-iap-ssh --network=default --direction=INGRESS \
  --action=ALLOW --rules=tcp:22 --source-ranges=35.235.240.0/20 --target-tags=maestro-web
```

> New projects often have a rule called `default-allow-ssh` that opens SSH to the whole internet. If this project is only for the demo, delete it: `gcloud compute firewall-rules delete default-allow-ssh`.

## Step 6: Create the VM

2 vCPUs, 8 GB memory, a 50 GB disk and Debian 12. That's plenty for a demo.

```bash
gcloud compute instances create maestro-demo --zone=$ZONE \
  --machine-type=e2-standard-2 --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=50GB --boot-disk-type=pd-balanced \
  --address=$IP --tags=maestro-web \
  --service-account=maestro-vm@${PROJECT}.iam.gserviceaccount.com --scopes=cloud-platform \
  --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring
```

## Step 7: Point your address at the VM

In your DNS provider, add an **A record**: name `maestro` (or whatever you chose), value = the IP from step 4. Check it from Cloud Shell; the answer must be your IP:

```bash
getent hosts maestro.yourcompany.com
```

DNS changes usually take a few minutes to appear.

> **Quick test without a domain:** the free service `sslip.io` turns an IP into a hostname. For IP `34.12.56.78` use `34-12-56-78.sslip.io`. It's fine for checking the setup, but use your own domain for the customer: it looks right, and certificate limits on shared services like this can block you.

## Step 8: Install Docker and the code on the VM

Connect to the VM (the first time, it asks to create an SSH key; press Enter at the prompts):

```bash
gcloud compute ssh maestro-demo --zone=$ZONE --tunnel-through-iap
```

From here on, the commands run **on the VM**.

Install git and Docker:

```bash
sudo apt-get update && sudo apt-get install -y git
```

```bash
curl -fsSL https://get.docker.com | sudo sh
```

Get the code. If the repository is private, use your GitHub username and paste the token from "Before you start" as the password:

```bash
sudo git clone https://github.com/sjibkate-bot/maestro.git /opt/maestro
```

## Step 9: Settings

Create the settings file and open it in an editor:

```bash
cd /opt/maestro/deploy/gcp && sudo cp maestro.env.example .env && sudo nano .env
```

Set `MAESTRO_HOST` to your address from step 7, for example `MAESTRO_HOST=maestro.yourcompany.com`. Leave the rest as it is unless you want:

* `AI_ENABLED=false`, to run without Claude, on the built-in rules only;
* `RISK_FEEDS_ENABLED=false`, to switch off the daily public risk feeds.

Save with **Ctrl+O**, **Enter**, then exit with **Ctrl+X**. This file holds no secrets.

## Step 10: Start Maestro-SC

```bash
sudo /opt/maestro/deploy/gcp/start.sh
```

The first start takes a few minutes: it builds the app and downloads MongoDB and Caddy. At the end it prints the address. Caddy then gets the HTTPS certificate, which takes up to a minute.

Check it from the VM:

```bash
curl -s https://$(grep ^MAESTRO_HOST /opt/maestro/deploy/gcp/.env | cut -d= -f2)/api/health
```

You should see `{"status":"ok","ai_provider":"claude"}`. If `ai_provider` says `rules`, the API key wasn't picked up; see Troubleshooting.

## Step 11: Sign in and load the demo data

1. Open `https://<your address>/maestro` in a browser. You get the sign-in page.
2. Sign in as `admin` with the passcode from step 2.
3. **Admin console → Groups / Users:** create the accounts for the demo, or let people use **Request access** and approve them.
4. **Configuration → Load files:** load the demo files from your laptop (for example `samples/auto_pack/out/` for the car-manufacturing pack), the same way you do locally.

The admin passcode in Secret Manager is only used to create the first admin when there are no users yet. To change it later, use **Account** in the app.

---

## Running it day to day

All of these run on the VM (connect as in step 8).

| Task | Command |
|---|---|
| See what's running | `cd /opt/maestro/deploy/gcp && sudo docker compose ps` |
| Watch the app log | `cd /opt/maestro/deploy/gcp && sudo docker compose logs -f app` (Ctrl+C to stop watching) |
| Update to the latest code | `cd /opt/maestro && sudo git pull && sudo deploy/gcp/start.sh` |
| Restart everything | `sudo /opt/maestro/deploy/gcp/start.sh` |

Always start or update with `start.sh`, not `docker compose up`. Only `start.sh` loads the secrets.

**Saving money between demos.** A running VM costs money every hour. Stop it from Cloud Shell when you don't need it, and start it again before the demo. Maestro-SC comes back up by itself, with its data.

```bash
gcloud compute instances stop maestro-demo --zone=$ZONE
```

```bash
gcloud compute instances start maestro-demo --zone=$ZONE
```

The fixed IP and the disk are still charged while the VM is stopped, but much less. Check current prices in the Google Cloud pricing calculator.

**Changing the API key.** Add a new version of the secret in Cloud Shell, then run `start.sh` on the VM:

```bash
read -rsp "New Anthropic API key: " V; echo; printf '%s' "$V" | gcloud secrets versions add maestro-anthropic-api-key --data-file=-; unset V
```

## Backups

Daily snapshots of the whole VM disk, kept for 7 days (run in Cloud Shell):

```bash
gcloud compute resource-policies create snapshot-schedule maestro-daily --region=$REGION \
  --daily-schedule --start-time=04:00 --max-retention-days=7
```

```bash
gcloud compute disks add-resource-policies maestro-demo --resource-policies=maestro-daily --zone=$ZONE
```

A copy of just the database, written to a file on the VM (run on the VM):

```bash
cd /opt/maestro/deploy/gcp && sudo docker compose exec -T mongo mongodump --archive --gzip | sudo tee /opt/maestro-backup-$(date +%F).gz > /dev/null
```

## Tightening access (optional)

**Only the customer's networks.** The firewall in step 5 lets anyone reach the sign-in page. To allow only certain networks, replace `0.0.0.0/0` with their address ranges:

```bash
gcloud compute firewall-rules update maestro-allow-web --source-ranges=203.0.113.0/24,198.51.100.0/24
```

The certificate is renewed automatically about every 60 days, and renewal needs the ports open to the internet. For a demo of a few weeks, restrict access after the first start. Before a renewal, open it again, or put Google Cloud Armor in front (that needs a load balancer, which is beyond this guide).

**No Claude at all.** Set `AI_ENABLED=false` in `.env` and run `start.sh`. Everything still works: files are mapped with the built-in rules, and Ask Maestro answers from the computed figures.

---

## Troubleshooting

| What you see | What to do |
|---|---|
| The browser says the connection isn't secure, or nothing loads | DNS isn't pointing at the VM yet (step 7), or port 80/443 is blocked. Check the Caddy log: `sudo docker compose logs caddy` (in `/opt/maestro/deploy/gcp`). Once DNS is right, run `start.sh` again. |
| `start.sh` says `PERMISSION_DENIED` on a secret | The VM's service account can't read the secret. Repeat step 3, and check that the secret names are exactly as in step 2. |
| `start.sh` says to create the settings file | Step 9 was skipped: `sudo cp maestro.env.example .env` in `/opt/maestro/deploy/gcp`. |
| Health shows `"ai_provider":"rules"` | `AI_ENABLED` is `false` in `.env`, or the API key secret is empty. Fix it, then run `start.sh`. |
| Sign-in says the passcode is wrong for `admin` | The admin is created only on the very first start, with the secret's value at that time. Changing the secret later doesn't change the passcode, so sign in with the original one and change it under **Account**. |
| Claude calls fail in the app log (401) | The API key is wrong or was revoked. Add a new version (see "Changing the API key"). |

## Removing everything

In Cloud Shell. **This deletes the VM, its disk and all loaded data.**

```bash
gcloud compute instances delete maestro-demo --zone=$ZONE
gcloud compute addresses delete maestro-ip --region=$REGION
gcloud compute firewall-rules delete maestro-allow-web maestro-allow-iap-ssh
gcloud secrets delete maestro-anthropic-api-key
gcloud secrets delete maestro-admin-password
gcloud iam service-accounts delete maestro-vm@${PROJECT}.iam.gserviceaccount.com
```

If you set up backups, also delete the snapshot schedule and the snapshots (in the console: Compute Engine → Snapshots). Then delete the DNS record and revoke the Anthropic API key in the Claude Console.
