locals {
  prefix = "sre-${var.environment}"
  common_tags = merge(var.tags, {
    Environment = var.environment
    ManagedBy   = "Terraform"
  })
}

resource "aws_sns_topic" "alerts" {
  name = "${local.prefix}-platform-alerts"
  tags = local.common_tags
}

resource "aws_sns_topic_subscription" "pagerduty" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "https"
  endpoint  = var.pagerduty_endpoint
}

module "vpc" {
  source = "./modules/vpc"

  prefix             = local.prefix
  vpc_cidr           = var.vpc_cidr
  availability_zones = var.availability_zones
  tags               = local.common_tags
}

module "eks" {
  source = "./modules/eks"

  prefix                  = local.prefix
  cluster_version         = var.cluster_version
  vpc_id                  = module.vpc.vpc_id
  private_subnet_ids      = module.vpc.private_subnet_ids
  node_instance_type      = var.node_instance_type
  node_min_size           = var.node_min_size
  node_max_size           = var.node_max_size
  node_desired_size       = var.node_desired_size
  spot_instance_types     = var.spot_instance_types
  tags                    = local.common_tags
}

module "monitoring" {
  source = "./modules/monitoring"

  prefix          = local.prefix
  cluster_name    = module.eks.cluster_name
  sns_topic_arn   = aws_sns_topic.alerts.arn
  log_retention   = var.log_retention_days
  tags            = local.common_tags
}
