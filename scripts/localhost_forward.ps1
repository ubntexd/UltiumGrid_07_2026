# Run on Windows (PowerShell) if Cursor auto-forward fails:
# Forwards local 8080/8000 to the VPS UltiumGrid stack.
# Usage: .\scripts\localhost_forward.ps1 -HostName 176.97.70.254 -User dev
param(
  [string]$HostName = "176.97.70.254",
  [string]$User = "dev"
)
ssh -N -L 8080:127.0.0.1:8080 -L 8000:127.0.0.1:8000 "${User}@${HostName}"
