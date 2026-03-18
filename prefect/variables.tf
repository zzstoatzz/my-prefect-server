variable "domain" {
  description = "Domain where the Prefect server is running"
  type        = string
}

variable "auth_string" {
  description = "Prefect API auth string (basic_auth_key for the provider)"
  type        = string
  sensitive   = true
}
