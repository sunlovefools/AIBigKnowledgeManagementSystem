from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from datetime import datetime, timedelta
import ipaddress
import os

os.makedirs('ssl', exist_ok=True)

# Generate private key
private_key = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
    backend=default_backend()
)

# Build certificate
subject = issuer = x509.Name([
    x509.NameAttribute(NameOID.COUNTRY_NAME, u'CN'),
    x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, u'State'),
    x509.NameAttribute(NameOID.LOCALITY_NAME, u'City'),
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, u'Dev'),
    x509.NameAttribute(NameOID.COMMON_NAME, u'localhost'),
])

cert = x509.CertificateBuilder().subject_name(
    subject
).issuer_name(
    issuer
).public_key(
    private_key.public_key()
).serial_number(
    x509.random_serial_number()
).not_valid_before(
    datetime.utcnow()
).not_valid_after(
    datetime.utcnow() + timedelta(days=365)
).add_extension(
    x509.SubjectAlternativeName([
        x509.DNSName(u'localhost'),
        x509.DNSName(u'cgi-backend'),
        x509.DNSName(u'*.local'),
        x509.IPAddress(ipaddress.IPv4Address(u'127.0.0.1')),
    ]),
    critical=False,
).sign(private_key, hashes.SHA256(), default_backend())

# Write certificate
with open('ssl/cert.pem', 'wb') as f:
    f.write(cert.public_bytes(serialization.Encoding.PEM))

# Write private key
with open('ssl/cert.key', 'wb') as f:
    f.write(private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()
    ))

print('✓ 证书已生成: ssl/cert.pem')
print('✓ 私钥已生成: ssl/cert.key')
