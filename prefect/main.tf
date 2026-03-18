provider "prefect" {
  endpoint       = "https://${var.domain}/api"
  basic_auth_key = var.auth_string
}

# kubernetes work pool — each flow run gets its own pod
resource "prefect_work_pool" "k8s" {
  name = "kubernetes-pool"
  type = "kubernetes"
}

# environment identifier
resource "prefect_variable" "environment" {
  name  = "environment"
  value = "dogfood"
}
