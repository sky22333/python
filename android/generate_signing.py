#!/usr/bin/env python3
import sys
import subprocess
import os
import base64
import secrets
import string
import datetime

try:
    import cryptography
except ImportError:
    print("检测到缺失 cryptography 库，正在自动安装...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "cryptography"])
        print("✅ 安装完成！\n")
    except Exception as e:
        print(f"❌ 安装失败: {e}")
        input("按回车键退出...")
        sys.exit(1)

from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives.serialization import pkcs12

# ================= 配置区域 =================
# 输出文件名
FILENAME = "release.jks"
# 密钥别名
KEY_ALIAS = "luleme_key"
# 证书通用名称 (通常是应用名称)
COMMON_NAME = "Luleme App"
# 证书有效期 (年)
VALIDITY_YEARS = 25
# 密钥长度 (位)
KEY_SIZE = 2048
# ===========================================

def main():
    print("\n🔐 Android 签名生成器\n" + "="*40)
    pwd = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20))
    if os.path.exists(FILENAME):
        if input(f"⚠️  文件 {FILENAME} 已存在，是否覆盖？(y/n): ").strip().lower() != 'y':
            return

    print("⚙️  正在生成密钥和证书...")

    key = rsa.generate_private_key(public_exponent=65537, key_size=KEY_SIZE)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, COMMON_NAME)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=365*VALIDITY_YEARS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    with open(FILENAME, "wb") as f:
        f.write(pkcs12.serialize_key_and_certificates(
            name=KEY_ALIAS.encode(),
            key=key,
            cert=cert,
            cas=None,
            encryption_algorithm=serialization.BestAvailableEncryption(pwd.encode())
        ))

    b64_str = base64.b64encode(open(FILENAME, "rb").read()).decode()
    
    print(f"✅ 文件生成成功: {os.path.abspath(FILENAME)}\n")
    print("GitHub Secrets 配置:")
    print("-" * 50)
    print(f"SIGNING_KEY_BASE64:\n{b64_str}\n")
    print("-" * 50)
    print(f"KEY_ALIAS:          {KEY_ALIAS}")
    print(f"KEY_STORE_PASSWORD: {pwd}")
    print(f"KEY_PASSWORD:       {pwd}")
    print("-" * 50)
    input("\n按回车键退出...")

if __name__ == "__main__":
    main()
