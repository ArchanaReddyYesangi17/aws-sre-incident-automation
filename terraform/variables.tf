variable "environment" {
  description = "Deployment environment"
  type        = string
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Must be dev, staging, or prod."
  }
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "Availability zones for subnet distribution"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b", "us-east-1c"]
}

variable "cluster_version" {
  description = "EKS Kubernetes version"
  type        = string
  default     = "1.28"
}

variable "node_instance_type" {
  description = "EC2 instance type for on-demand node group"
  type        = string
  default     = "m5.xlarge"
}

variable "node_min_size" {
  description = "Minimum nodes in on-demand group"
  type        = number
  default     = 2
}

variable "node_max_size" {
  description = "Maximum nodes in on-demand group"
  type        = number
  default     = 20
}

variable "node_desired_size" {
  description = "Initial desired node count"
  type        = number
  default     = 3
}

variable "spot_instance_types" {
  description = "EC2 instance types for spot node group"
  type        = list(string)
  default     = ["m5.xlarge", "m5a.xlarge", "m4.xlarge"]
}

variable "log_retention_days" {
  description = "CloudWatch log group retention in days"
  type        = number
  default     = 30
}

variable "pagerduty_endpoint" {
  description = "PagerDuty HTTPS endpoint for SNS subscription"
  type        = string
  sensitive   = true
}

variable "tags" {
  description = "Common resource tags"
  type        = map(string)
  default = {
    Project   = "aws-sre-platform"
    CostCenter = "engineering"
  }
}
