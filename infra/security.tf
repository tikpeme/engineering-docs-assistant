# ---------- ALB: the only thing open to the internet ----------
resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "Public entry point"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "alb_out" {
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"   # all protocols
}

# ---------- API: reachable only from the ALB ----------
resource "aws_security_group" "api" {
  name        = "${local.name}-api"
  description = "FastAPI tasks"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "api_from_alb" {
  security_group_id            = aws_security_group.api.id
  referenced_security_group_id = aws_security_group.alb.id   # <-- SG reference, not an IP
  from_port                    = 8000
  to_port                      = 8000
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "api_out" {
  security_group_id = aws_security_group.api.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# ---------- Ingest worker: no inbound at all ----------
resource "aws_security_group" "ingest" {
  name        = "${local.name}-ingest"
  description = "Ingest worker tasks, outbound only"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_egress_rule" "ingest_out" {
  security_group_id = aws_security_group.ingest.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# ---------- Aurora: only the two services, port 5432 ----------
resource "aws_security_group" "db" {
  name        = "${local.name}-db"
  description = "Aurora PostgreSQL"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "db_from_api" {
  security_group_id            = aws_security_group.db.id
  referenced_security_group_id = aws_security_group.api.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "db_from_ingest" {
  security_group_id            = aws_security_group.db.id
  referenced_security_group_id = aws_security_group.ingest.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}
# No egress rule for db: stateful SGs let replies out automatically.