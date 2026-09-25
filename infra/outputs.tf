output "api_url" {
  description = "Backend base URL. Set this as NEXT_PUBLIC_API_BASE in Vercel, then redeploy."
  value       = "https://${local.site_address}"
}

output "public_ip" {
  description = "Elastic IP of the backend host."
  value       = aws_eip.backend.public_ip
}

output "instance_id" {
  description = "EC2 instance id."
  value       = aws_instance.backend.id
}

output "cors_origins" {
  description = "Origins the backend currently allows via CORS_ORIGINS."
  value       = var.cors_origins
}

output "ssm_shell" {
  description = "Open a shell on the host without SSH."
  value       = "aws ssm start-session --target ${aws_instance.backend.id} --region ${var.aws_region}"
}

output "ssh_command" {
  description = "SSH command (only when key_name is set)."
  value       = var.key_name != "" ? "ssh -i <path-to-${var.key_name}.pem> ubuntu@${aws_eip.backend.public_ip}" : null
}

output "bootstrap_log" {
  description = "Watch first-boot provisioning here (via SSM/SSH)."
  value       = "sudo tail -f /var/log/cloud-init-output.log"
}

output "stack_status" {
  description = "Check the running stack on the host."
  value       = "cd /opt/agent-orch && sudo docker compose --env-file /mnt/data/agent-orch/backend.env ps"
}
