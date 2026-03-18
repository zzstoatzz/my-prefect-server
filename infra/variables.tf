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
  description = "Hetzner server type (cpx21 = 3 vCPU, 4 GB RAM, 80 GB disk)"
  type        = string
  default     = "cpx21"
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
