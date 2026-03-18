variable "hcloud_token" {
  description = "Hetzner Cloud API token"
  type        = string
  sensitive   = true
}

variable "ssh_public_key_path" {
  description = "Path to SSH public key"
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

variable "server_type" {
  description = "Hetzner server type (cpx31 = 4 vCPU, 8 GB RAM, 160 GB disk)"
  type        = string
  default     = "cpx31"
}

variable "location" {
  description = "Hetzner datacenter location"
  type        = string
  default     = "ash"
}

variable "server_name" {
  description = "Name for the server"
  type        = string
  default     = "prefect-server"
}
