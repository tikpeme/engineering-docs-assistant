terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  backend "s3" {
    bucket       = "kestrel-rag-tfstate-171233144354"
    key          = "dev/terraform.tfstate"
    region       = "us-east-2"
    use_lockfile = true # S3-native locking, no DynamoDB table needed
    encrypt      = true
  }
}