data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "docs" {
  bucket        = "${local.name}-raw-docs-${data.aws_caller_identity.current.account_id}"
  force_destroy = true   # dev only: lets terraform destroy delete a non-empty bucket
}

resource "aws_s3_bucket_public_access_block" "docs" {
  bucket                  = aws_s3_bucket.docs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "docs" {
  bucket = aws_s3_bucket.docs.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Send S3 events to EventBridge, which will trigger the ingest worker later
resource "aws_s3_bucket_notification" "docs" {
  bucket      = aws_s3_bucket.docs.id
  eventbridge = true
}

output "docs_bucket" { value = aws_s3_bucket.docs.bucket }