"""Test get_config and close with real Netmiko against a fake SSH shell."""

import socket
import threading
import time

import paramiko
import pytest
from napalm.base.exceptions import CommandErrorException

from napalm_opengear.opengear import OpenGearDriver

PAUSE = None
REJECTED = ['config: unrecognized option\n']
LEGACY = ['config.system.name og\n', 'config.system.model CM7116\n']
CURRENT = ['Entity system/fips\n  enabled false\n\n', PAUSE, 'Item names for entity user\n  Entity user item root\n']
HOST_KEY = paramiko.RSAKey.generate(2048)


class FakeServer(paramiko.ServerInterface):
    def check_auth_password(self, username, password):
        return paramiko.AUTH_SUCCESSFUL

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED

    def check_channel_pty_request(self, *args):
        return True

    def check_channel_shell_request(self, channel):
        return True


def run_shell(channel, responses):
    """Behave like a login shell: echo input, run commands, print a prompt."""
    prompt = 'admin@og:~$ '
    channel.sendall(prompt)
    buffer = ''
    while True:
        data = channel.recv(1024).decode()
        if not data:
            return
        buffer += data
        channel.sendall(data.replace('\n', '\r\n'))
        while '\n' in buffer:
            line, buffer = buffer.split('\n', 1)
            command, _, tail = line.strip().partition(';')
            on_terminal = '</dev/null' not in command
            command = command.split(' <')[0]
            if on_terminal and command in responses:
                continue  # like `config` on a terminal: waits for input, no prompt
            if command == 'sudo -s':
                prompt = 'root@og:~# '
            for chunk in responses.get(command, []):
                if chunk is PAUSE:
                    time.sleep(12)  # longer than Netmiko's default 10 second read_timeout
                else:
                    channel.sendall(chunk.replace('\n', '\r\n'))
            if tail:
                channel.sendall(tail.replace('echo', '').replace('""', '').strip() + '\r\n')
            channel.sendall(prompt)


@pytest.fixture
def connect():
    """Return a function that opens a driver against a shell with the given responses."""
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    state = {}

    def serve(responses):
        sock, _ = listener.accept()
        transport = paramiko.Transport(sock)
        transport.add_server_key(HOST_KEY)
        transport.start_server(server=FakeServer())
        state['channel'] = transport.accept(20)
        try:
            run_shell(state['channel'], responses)
        except (OSError, EOFError):
            pass  # the client hung up while the shell was still printing

    def open_driver(responses, **extra_args):
        threading.Thread(target=serve, args=(responses,), daemon=True).start()
        driver = OpenGearDriver(
            '127.0.0.1', 'admin', 'pw',
            optional_args={'port': listener.getsockname()[1], 'ssh_strict': False, **extra_args},
        )
        driver.open()
        state['driver'] = driver
        return driver

    open_driver.state = state
    yield open_driver
    if state['driver'].device:
        state['driver'].close()
    listener.close()


def lines(chunks):
    return [line for chunk in chunks if chunk for line in chunk.splitlines()]


def test_current_firmware_uses_show_config_and_survives_a_pause(connect):
    driver = connect({'config -g config': REJECTED, 'config --show-config': CURRENT})
    running = driver.get_config(retrieve='running')['running']
    assert running.splitlines()[-len(lines(CURRENT)):] == lines(CURRENT)
    assert 'unrecognized' not in running
    assert '__NAPALM' not in running


def test_legacy_firmware_uses_config_g(connect):
    driver = connect({'config -g config': LEGACY})
    running = driver.get_config(retrieve='running')['running']
    assert running.splitlines()[-len(lines(LEGACY)):] == lines(LEGACY)


def test_rejected_commands_raise_instead_of_returning_the_error(connect):
    driver = connect({'config -g config': REJECTED, 'config --show-config': REJECTED})
    with pytest.raises(CommandErrorException):
        driver.get_config(retrieve='running')


@pytest.mark.parametrize('secret, expected', [('enable-pw', 'enable-pw'), (None, 'pw'), ('', 'pw')])
def test_secret_from_nornir_does_not_collide(connect, secret, expected):
    """Nornir always sets optional_args['secret'], sometimes to nothing."""
    driver = connect({'config --show-config': CURRENT}, secret=secret)
    assert driver.secret == expected
    assert 'secret' not in driver.netmiko_optional_args
    assert driver.device.secret == expected


def test_close_ends_the_ssh_session(connect):
    driver = connect({})
    channel = connect.state
    driver.close()
    deadline = time.time() + 10
    while not channel['channel'].closed and time.time() < deadline:
        time.sleep(0.1)
    assert channel['channel'].closed
    assert driver.device is None
