# Which subnets Aurora may use: the private ones only
resource "aws_db_subnet_group" "main" {
  name       = "${local.name}-db"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_rds_cluster" "main" {
  cluster_identifier = "${local.name}-aurora"
  engine             = "aurora-postgresql"
  engine_mode        = "provisioned"   # Serverless v2 uses "provisioned" + db.serverless instances
  engine_version = "16.15"
  database_name      = "docs"

  master_username             = "dbadmin"
  manage_master_user_password = true   # AWS generates the password and stores it in Secrets Manager

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]

  storage_encrypted   = true
  enable_http_endpoint = true          # RDS Data API: lets us run SQL without a bastion host

  serverlessv2_scaling_configuration {
    min_capacity             = 0       # scale to zero when idle
    max_capacity             = 2
    seconds_until_auto_pause = 600     # pause after 10 idle minutes
  }

  skip_final_snapshot = true           # dev only: no snapshot on destroy
  deletion_protection = false          # dev only
}

resource "aws_rds_cluster_instance" "main" {
  identifier         = "${local.name}-aurora-1"
  cluster_identifier = aws_rds_cluster.main.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.main.engine
  engine_version     = aws_rds_cluster.main.engine_version
}

output "db_endpoint"   { value = aws_rds_cluster.main.endpoint }
output "db_secret_arn" { value = aws_rds_cluster.main.master_user_secret[0].secret_arn }
output "db_cluster_arn" { value = aws_rds_cluster.main.arn }