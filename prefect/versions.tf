terraform {
  required_version = ">= 1.0"

  required_providers {
    prefect = {
      source  = "prefecthq/prefect"
      version = "~> 2.0"
    }
  }
}
