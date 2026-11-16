"""Install one restricted reverse SSH identity; no general shell or forwarding."""
from pathlib import Path
import argparse
import os
import pwd
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--public-key', required=True)
    args = parser.parse_args()
    key = args.public_key.strip()
    assert key.startswith('ssh-ed25519 ') and '\n' not in key and len(key) < 300
    user = 'whalegirl-voice'
    home = Path('/var/lib/whalegirl-pc-voice')
    try:
        record = pwd.getpwnam(user)
        assert record.pw_dir == str(home) and record.pw_shell == '/usr/sbin/nologin'
    except KeyError:
        subprocess.run(['useradd', '--system', '--create-home', '--home-dir', str(home),
                        '--shell', '/usr/sbin/nologin', user], check=True)
        record = pwd.getpwnam(user)
    home.chmod(0o700)
    directory = home / '.ssh'
    directory.mkdir(mode=0o700, exist_ok=True)
    target = directory / 'authorized_keys'
    content = ('restrict,port-forwarding,permitlisten="127.0.0.1:19881",'
               'command="/usr/sbin/nologin" ' + key + '\n')
    if target.exists():
        assert target.read_text() == content, 'Existing identity differs; left untouched'
    else:
        target.write_text(content)
    target.chmod(0o600)
    for p in (home, directory, target):
        os.chown(p, record.pw_uid, record.pw_gid)
    config = Path('/etc/ssh/sshd_config.d/90-whalegirl-pc-voice.conf')
    content = '''# One dedicated PC voice tunnel; never a general login identity.
Match User whalegirl-voice
    AllowTcpForwarding remote
    PermitListen 127.0.0.1:19881
    PermitOpen none
    GatewayPorts no
    MaxSessions 0
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    X11Forwarding no
    AllowAgentForwarding no
    PermitTTY no
    ClientAliveInterval 15
    ClientAliveCountMax 3
Match all
'''
    if config.exists():
        previous = content.replace('    ClientAliveInterval 15\n    ClientAliveCountMax 3\n', '')
        assert config.read_text() in (content, previous), 'SSH scope differs; left untouched'
        if config.read_text() != content:
            config.with_suffix('.before-clientalive').write_text(config.read_text())
            config.write_text(content)
    else:
        config.write_text(content)
    config.chmod(0o644)
    subprocess.run(['/usr/sbin/sshd', '-t'], check=True)
    subprocess.run(['systemctl', 'reload', 'ssh.service'], check=True)
    effective = subprocess.check_output(['/usr/sbin/sshd', '-T', '-C',
        'user=whalegirl-voice,host=localhost,addr=127.0.0.1'], text=True)
    for value in ('allowtcpforwarding remote', 'gatewayports no', 'permitopen none',
                  'permitlisten 127.0.0.1:19881', 'maxsessions 0'):
        assert value in effective, value
    print('Dedicated voice identity installed: loopback reverse port 19881; shell sessions disabled')


if __name__ == '__main__':
    main()
