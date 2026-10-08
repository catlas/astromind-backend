"""
Малък SMTP сървър само за тестове: защитена връзка (SSL от началото или STARTTLS),
AUTH PLAIN/LOGIN, запис на получените писма. Слуша само на 127.0.0.1.

Подобно на реален сървър (напр. Exim), удостоверяването се предлага едва след като
връзката е защитена, а писмо се приема само след успешен вход.
"""
import base64
import datetime
import ipaddress
import socket
import ssl
import threading
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def make_self_signed_cert(directory: str, hostname: str = "localhost"):
    """Самоподписан сертификат за localhost. Връща (път_до_сертификата, път_до_ключа)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(hostname), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        # Строгата проверка на Python 3.13 изисква пълен набор разширения и за самоподписан "CA"
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_encipherment=True, key_cert_sign=True, crl_sign=False,
                content_commitment=False, data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    certfile = Path(directory) / "cert.pem"
    keyfile = Path(directory) / "key.pem"
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return str(certfile), str(keyfile)


class _Connection:
    """Четене на редове и изпращане на отговори; сокетът може да се замени след STARTTLS."""

    def __init__(self, sock):
        self.sock = sock
        self.buffer = b""

    def readline(self):
        while b"\r\n" not in self.buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                return None
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b"\r\n", 1)
        return line

    def send(self, text: str):
        self.sock.sendall(text.encode("utf-8") + b"\r\n")


class FakeSmtpServer:
    def __init__(self, certfile, keyfile, *, implicit_tls=False, starttls=True, auth_mechanisms="PLAIN LOGIN",
                 username="mailbox@example.test", password="s3cret-pass"):
        self.implicit_tls = implicit_tls
        self.starttls = starttls
        self.auth_mechanisms = auth_mechanisms
        self.username = username
        self.password = password
        self.messages = []       # {"mail_from", "rcpt_to", "data"} за всяко прието писмо
        self.auth_results = []   # True/False за всеки опит за вход
        self.auth_methods = []   # кой начин за вход е ползван при всеки опит
        self.events = []         # напр. "starttls", "tls-handshake-failed"

        self._context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._context.load_cert_chain(certfile, keyfile)
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(5)
        self._listener.settimeout(0.2)
        self.port = self._listener.getsockname()[1]
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stopped.set()
        self._thread.join(timeout=3)
        self._listener.close()

    def _serve(self):
        while not self._stopped.is_set():
            try:
                client, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, raw):
        conn = _Connection(raw)   # conn.sock сочи към текущия (след TLS - обвития) сокет
        try:
            raw.settimeout(5)
            tls = False
            if self.implicit_tls:
                try:
                    conn.sock = self._context.wrap_socket(raw, server_side=True)
                except (ssl.SSLError, OSError):
                    self.events.append("tls-handshake-failed")
                    return
                tls = True
            conn.send("220 fake.test ESMTP ready")

            authed = False
            mail_from = None
            rcpt_to = []
            while True:
                raw_line = conn.readline()
                if raw_line is None:
                    return
                line = raw_line.decode("utf-8", "replace")
                upper = line.upper()

                if upper.startswith(("EHLO", "HELO")):
                    replies = ["250-fake.test"]
                    if self.starttls and not tls:
                        replies.append("250-STARTTLS")
                    if tls and self.auth_mechanisms:
                        replies.append(f"250-AUTH {self.auth_mechanisms}")
                    replies.append("250 8BITMIME")
                    conn.send("\r\n".join(replies))
                elif upper == "STARTTLS":
                    if self.starttls and not tls:
                        conn.send("220 Ready to start TLS")
                        try:
                            conn.sock = self._context.wrap_socket(conn.sock, server_side=True)
                        except (ssl.SSLError, OSError):
                            self.events.append("tls-handshake-failed")
                            return
                        conn.buffer = b""
                        tls = True
                        self.events.append("starttls")
                    else:
                        conn.send("503 STARTTLS not available")
                elif upper.startswith("AUTH "):
                    if not tls:
                        conn.send("530 Must issue a STARTTLS command first")
                        continue
                    self.auth_methods.append(upper.split(" ")[1])
                    user, password = self._read_credentials(conn, line)
                    authed = (user == self.username and password == self.password)
                    self.auth_results.append(authed)
                    conn.send("235 Authentication succeeded" if authed else "535 Authentication failed")
                elif upper.startswith("MAIL FROM:"):
                    if not authed:
                        conn.send("530 Authentication required")
                        continue
                    mail_from = line[line.index("<") + 1:line.index(">")]
                    rcpt_to = []
                    conn.send("250 OK")
                elif upper.startswith("RCPT TO:"):
                    rcpt_to.append(line[line.index("<") + 1:line.index(">")])
                    conn.send("250 OK")
                elif upper == "DATA":
                    conn.send("354 End data with <CR><LF>.<CR><LF>")
                    data_lines = []
                    while True:
                        data_line = conn.readline()
                        if data_line is None:
                            return
                        if data_line == b".":
                            break
                        data_lines.append(data_line[1:] if data_line.startswith(b"..") else data_line)
                    self.messages.append({"mail_from": mail_from, "rcpt_to": rcpt_to, "data": b"\r\n".join(data_lines)})
                    conn.send("250 OK queued")
                elif upper in ("RSET", "NOOP"):
                    conn.send("250 OK")
                elif upper == "QUIT":
                    conn.send("221 Bye")
                    return
                else:
                    conn.send("502 Command not implemented")
        except (OSError, ssl.SSLError, ValueError):
            pass
        finally:
            # wrap_socket откача оригиналния сокет, затова затваряме текущия
            for sock in {conn.sock, raw}:
                try:
                    sock.close()
                except OSError:
                    pass

    @staticmethod
    def _read_credentials(conn, line):
        parts = line.split(" ", 2)
        mechanism = parts[1].upper()
        try:
            if mechanism == "PLAIN":
                if len(parts) == 3:
                    initial = parts[2]
                else:
                    conn.send("334 ")
                    initial = conn.readline().decode()
                _, user, password = base64.b64decode(initial).decode().split("\0")
                return user, password
            if mechanism == "LOGIN":
                if len(parts) == 3:
                    user = base64.b64decode(parts[2]).decode()   # потребителят е в първоначалния отговор
                else:
                    conn.send("334 VXNlcm5hbWU6")
                    user = base64.b64decode(conn.readline()).decode()
                conn.send("334 UGFzc3dvcmQ6")
                password = base64.b64decode(conn.readline()).decode()
                return user, password
        except (ValueError, UnicodeDecodeError):
            pass
        return None, None
