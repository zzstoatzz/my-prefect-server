output "server_ip" {
  description = "Public IP of the Prefect server"
  value       = hcloud_server.prefect.ipv4_address
}

output "ssh_command" {
  description = "SSH into the server"
  value       = "ssh root@${hcloud_server.prefect.ipv4_address}"
}
