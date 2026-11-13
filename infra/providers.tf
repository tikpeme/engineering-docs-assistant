provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "engineering-docs-assistant"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}