data "aws_caller_identity" "current" {}

data "aws_vpc" "default" {
  default = true
}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

locals {
  az         = var.availability_zone != "" ? var.availability_zone : data.aws_availability_zones.available.names[0]
  ssm_prefix = "/agent-orch/${var.name}"

  # Caddy's site address. With a real domain (+ hosted zone) we add an A record;
  # otherwise sslip.io resolves "<ip>.sslip.io" to the instance, so Caddy still
  # obtains a valid Let's Encrypt certificate with zero DNS setup.
  site_address = var.domain != "" ? var.domain : "${aws_eip.backend.public_ip}.sslip.io"
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }

  filter {
    name   = "availability-zone"
    values = [local.az]
  }
}

locals {
  subnet_id = var.subnet_id != "" ? var.subnet_id : data.aws_subnets.default.ids[0]

  user_data = templatefile("${path.module}/../deploy/cloud-init.yaml.tftpl", {
    domain        = local.site_address
    backend_image = var.backend_image
    aws_region    = var.aws_region
    ssm_prefix    = local.ssm_prefix
    cors_origins  = join(",", var.cors_origins)
    postgres_user = var.postgres_user
    postgres_db   = var.postgres_db
    ghcr_username = var.ghcr_username
    compose_b64   = base64encode(file("${path.module}/../deploy/docker-compose.yml"))
    caddyfile_b64 = base64encode(file("${path.module}/../deploy/Caddyfile"))
    bootstrap_b64 = base64encode(file("${path.module}/../deploy/bootstrap.sh"))
  })
}

# ── Network ───────────────────────────────────────────────────────────────────

resource "aws_security_group" "backend" {
  name        = "${var.name}-backend"
  description = "agent-orch backend host"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "HTTP (ACME challenge and redirect to HTTPS)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS (API + SSE)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  dynamic "ingress" {
    for_each = var.key_name != "" && var.ssh_cidr != "" ? [1] : []
    content {
      description = "SSH"
      from_port   = 22
      to_port     = 22
      protocol    = "tcp"
      cidr_blocks = [var.ssh_cidr]
    }
  }

  egress {
    description = "All outbound (GHCR pulls, git, OpenRouter, SSM)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_eip" "backend" {
  domain = "vpc"
}

# ── Secrets ───────────────────────────────────────────────────────────────────

resource "random_password" "postgres" {
  length  = 32
  special = false
}

# unconditional: the password is generated, so it is unknown until apply.
resource "aws_ssm_parameter" "postgres_password" {
  name      = "${local.ssm_prefix}/postgres_password"
  type      = "SecureString"
  value     = random_password.postgres.result
  overwrite = true
}

resource "aws_ssm_parameter" "openrouter_api_key" {
  name      = "${local.ssm_prefix}/openrouter_api_key"
  type      = "SecureString"
  value     = var.openrouter_api_key
  overwrite = true
}

locals {
  # Only plain variables here: for_each keys must be known at plan time, and a
  # generated value (random_password) or an empty check on one would not be.
  optional_secrets = {
    github_token = var.github_token
    ghcr_token   = var.ghcr_token
  }
}

resource "aws_ssm_parameter" "optional" {
  # Optional tokens only get a parameter when a value was supplied.
  # nonsensitive() reveals whether a secret is set — a parameter name, never a
  # value — which for_each requires; a sensitive value cannot be used as a key.
  for_each = toset([for k, v in local.optional_secrets : k if nonsensitive(v) != ""])

  name      = "${local.ssm_prefix}/${each.key}"
  type      = "SecureString"
  value     = local.optional_secrets[each.key]
  overwrite = true
}

# ── IAM ───────────────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "backend" {
  name               = "${var.name}-backend"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

# Session Manager access (shell on the box without opening port 22).
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.backend.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "read_secrets" {
  statement {
    actions = ["ssm:GetParameter"]

    resources = [
      "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter${local.ssm_prefix}/*",
    ]
  }
}

resource "aws_iam_role_policy" "read_secrets" {
  name   = "read-secrets"
  role   = aws_iam_role.backend.id
  policy = data.aws_iam_policy_document.read_secrets.json
}

resource "aws_iam_instance_profile" "backend" {
  name = "${var.name}-backend"
  role = aws_iam_role.backend.name
}

# ── Compute + storage ─────────────────────────────────────────────────────────

resource "aws_instance" "backend" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  subnet_id              = local.subnet_id
  vpc_security_group_ids = [aws_security_group.backend.id]
  iam_instance_profile   = aws_iam_instance_profile.backend.name
  key_name               = var.key_name != "" ? var.key_name : null
  user_data              = local.user_data

  # user_data embeds the site address, so a change means a rebuild.
  user_data_replace_on_change = true

  root_block_device {
    volume_size = var.root_volume_gb
    volume_type = "gp3"
    encrypted   = true
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  tags = { Name = "${var.name}-backend" }

  # Seeded before first boot so bootstrap.sh can read them.
  depends_on = [
    aws_ssm_parameter.postgres_password,
    aws_ssm_parameter.openrouter_api_key,
    aws_ssm_parameter.optional,
  ]
}

# Independent of the instance's AZ so there is no dependency cycle with the
# attachment; bootstrap.sh waits for the device to appear.
resource "aws_ebs_volume" "data" {
  availability_zone = local.az
  size              = var.data_volume_gb
  type              = "gp3"
  encrypted         = true

  tags = { Name = "${var.name}-data" }
}

resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.data.id
  instance_id = aws_instance.backend.id
}

resource "aws_eip_association" "backend" {
  instance_id   = aws_instance.backend.id
  allocation_id = aws_eip.backend.id
}

resource "aws_route53_record" "backend" {
  count = var.domain != "" && var.route53_zone_id != "" ? 1 : 0

  zone_id = var.route53_zone_id
  name    = var.domain
  type    = "A"
  ttl     = 60
  records = [aws_eip.backend.public_ip]
}
