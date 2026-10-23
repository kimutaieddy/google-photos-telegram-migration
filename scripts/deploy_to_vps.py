"""
Automated VPS Deployment Script via Paramiko
Transfers the self-contained migration bundle, extracts it, and launches the daemon in background.
"""

import sys
import getpass
from pathlib import Path
import paramiko

VPS_HOST = "102.133.184.146"
VPS_USER = "n8nuser"
REMOTE_DIR = "/home/n8nuser/Migration"
BUNDLE_PATH = Path("data/vps_migration_bundle.zip")

def deploy(password: str):
    print(f"Connecting to {VPS_USER}@{VPS_HOST}...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    # Try public key first, fallback to password
    key_path = Path.home() / ".ssh" / "id_rsa"
    try:
        ssh.connect(VPS_HOST, username=VPS_USER, password=password, timeout=10)
    except Exception as e:
        print(f"Connection failed: {e}")
        return False

    print("Connected successfully!")
    
    # Copy public key to authorized_keys for passwordless future access
    pub_key_file = Path.home() / ".ssh" / "id_rsa.pub"
    if pub_key_file.exists():
        pub_key = pub_key_file.read_text().strip()
        cmd_auth = f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo '{pub_key}' >> ~/.ssh/authorized_keys && sort -u ~/.ssh/authorized_keys -o ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
        ssh.exec_command(cmd_auth)
        print("Installed local SSH public key to VPS authorized_keys.")

    sftp = ssh.open_sftp()
    
    # Create remote directory
    stdin, stdout, stderr = ssh.exec_command(f"mkdir -p {REMOTE_DIR}")
    stdout.channel.recv_exit_status()

    # Upload zip bundle
    remote_zip = f"{REMOTE_DIR}/bundle.zip"
    print(f"Uploading {BUNDLE_PATH} -> {remote_zip}...")
    sftp.put(str(BUNDLE_PATH), remote_zip)
    sftp.close()

    # Unpack and start
    print("Unpacking bundle on VPS and launching migration daemon...")
    run_cmds = [
        f"cd {REMOTE_DIR} && unzip -o bundle.zip && rm bundle.zip",
        f"cd {REMOTE_DIR} && python3 -m venv .venv || virtualenv .venv",
        f"cd {REMOTE_DIR} && .venv/bin/pip install -r requirements.txt",
        f"cd {REMOTE_DIR} && nohup .venv/bin/python main.py --gdrive --delete-after-upload --reverse --max-gb 90 --workers 4 > vps_migration.log 2>&1 &",
    ]
    full_cmd = " && ".join(run_cmds)
    stdin, stdout, stderr = ssh.exec_command(f"bash -c '{full_cmd}'")
    exit_status = stdout.channel.recv_exit_status()
    print("Remote launch output:")
    print(stdout.read().decode())
    err = stderr.read().decode()
    if err:
        print("Remote errors:", err)

    # Verify process is running
    stdin, stdout, stderr = ssh.exec_command("ps aux | grep '[m]ain.py'")
    ps_out = stdout.read().decode().strip()
    print("Active migration processes on VPS:")
    print(ps_out if ps_out else "Process started in background.")

    ssh.close()
    print("\nVPS Migration successfully started in the background!")
    return True

if __name__ == "__main__":
    if len(sys.argv) > 1:
        pwd = sys.argv[1]
    else:
        pwd = getpass.getpass(f"Enter SSH password for {VPS_USER}@{VPS_HOST}: ")
    deploy(pwd)

