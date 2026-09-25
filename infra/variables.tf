variable "name" {
  description = "Name prefix for all created resources."
  type        = string
  default     = "agent-orch"
}

variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

# ── Compute ───────────────────────────────────────────────────────────────────

variable "instance_type" {
  description = <<-EOT
    EC2 instance type. Must fit the runtime resource_limits in
    backend/config/runtimes.yaml for however many agent runs you expect at once
    (e.g. the beacon preset asks for 2 cpus / 4g).

    An AWS account on the FREE plan rejects anything that is not
    free-tier-eligible, so m7i-flex.large (2 vCPU / 8 GiB, x86_64) is the
    default because it matches t3.large's specs and is eligible. List the
    allowed types with:
      aws ec2 describe-instance-types \
        --filters Name=free-tier-eligible,Values=true \
        --query 'InstanceTypes[].{Type:InstanceType,MemMiB:MemoryInfo.SizeInMiB}'
  EOT
  type        = string
  default     = "m7i-flex.large"
}

variable "root_volume_gb" {
  description = "Root EBS volume size in GB (OS, images, container layers)."
  type        = number
  default     = 30
}

variable "data_volume_gb" {
  description = "Persistent data volume size in GB (Postgres, workspaces, ACME certs)."
  type        = number
  default     = 50
}

variable "availability_zone" {
  description = "AZ for the instance and data volume. Empty = first available AZ in the region."
  type        = string
  default     = ""
}

variable "subnet_id" {
  description = "Subnet to launch into. Empty = the default VPC's default subnet."
  type        = string
  default     = ""
}

# ── Access ────────────────────────────────────────────────────────────────────

variable "key_name" {
  description = "Existing EC2 key pair name for SSH. Empty = rely on SSM Session Manager only."
  type        = string
  default     = ""
}

variable "ssh_cidr" {
  description = "CIDR allowed to reach port 22 (e.g. \"1.2.3.4/32\"). Port 22 stays closed unless both key_name and this are set."
  type        = string
  default     = ""
}

# ── TLS / DNS ─────────────────────────────────────────────────────────────────

variable "domain" {
  description = <<-EOT
    Public hostname the backend answers on, e.g. "api.example.com". Leave empty
    to fall back to "<elastic-ip>.sslip.io", which resolves to the instance and
    still gets a valid Let's Encrypt certificate with no DNS setup.
  EOT
  type        = string
  default     = ""
}

variable "route53_zone_id" {
  description = "Route53 hosted zone id for var.domain. Empty = create no DNS record (you manage it)."
  type        = string
  default     = ""
}

# ── Application ───────────────────────────────────────────────────────────────

variable "backend_image" {
  description = "Backend image reference, built by .github/workflows/images.yml or deploy/push-images.sh."
  type        = string
  default     = "ghcr.io/aadarshkt/agent-orch-backend:latest"
}

variable "cors_origins" {
  description = "Browser origins allowed by CORS — must include the deployed frontend origin."
  type        = list(string)

  validation {
    condition     = length(var.cors_origins) > 0
    error_message = "At least one CORS origin is required, e.g. [\"https://your-app.vercel.app\"]."
  }
}

variable "postgres_user" {
  description = "Postgres superuser name for the in-VM database."
  type        = string
  default     = "postgres"
}

variable "postgres_db" {
  description = "Postgres database name."
  type        = string
  default     = "postgres"
}

# ── Secrets (never hardcode; see infra/apply.sh) ──────────────────────────────

variable "openrouter_api_key" {
  description = "OpenRouter API key forwarded to the openrouter-agent runtime."
  type        = string
  sensitive   = true
}

variable "github_token" {
  description = "GitHub PAT (repo scope) used by the git_commit executor for publishing. Optional."
  type        = string
  sensitive   = true
  default     = ""
}

variable "ghcr_username" {
  description = "GHCR username used by the host and the CLI executor to pull images."
  type        = string
  default     = "aadarshkt"
}

variable "ghcr_token" {
  description = "GHCR PAT with read:packages. Only needed if the pushed packages are private."
  type        = string
  sensitive   = true
  default     = ""
}
