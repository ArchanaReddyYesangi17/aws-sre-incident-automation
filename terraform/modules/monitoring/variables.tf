variable "prefix" { type = string }
variable "cluster_name" { type = string }
variable "sns_topic_arn" { type = string }
variable "log_retention" { type = number }
variable "tags" { type = map(string) }
