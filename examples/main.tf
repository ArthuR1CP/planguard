# A tiny, credential-free Terraform config used by .github/workflows/demo.yml.
#
# The demo applies with `-var rev=1`, then plans with `-var rev=2`. Bumping `rev`
# forces replacement of `local_file.database` and `null_resource.app_server`.
# `.planguard.yml` next to this file declares those types as stateful / compute,
# so planguard reports what it would for a real database or VM.

terraform {
  required_version = ">= 1.5"
  required_providers {
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
  }
}

variable "rev" {
  description = "Bump to simulate a change that forces replacement."
  type        = number
  default     = 1
}

# Plays the role of a database: content changes force replacement.
resource "local_file" "database" {
  filename = "${path.module}/.out/database.txt"
  content  = "schema revision ${var.rev}\n"
}

# Plays the role of a VM: triggers changes force replacement.
resource "null_resource" "app_server" {
  triggers = {
    image = "app:${var.rev}"
  }
}

# Unchanged between revisions: shows up as no-op.
resource "local_file" "readme" {
  filename = "${path.module}/.out/README.txt"
  content  = "managed by terraform\n"
}

# Only exists in rev 2: shows up as a create.
resource "local_file" "feature_flag" {
  count    = var.rev >= 2 ? 1 : 0
  filename = "${path.module}/.out/flags.txt"
  content  = "new_checkout=true\n"
}
