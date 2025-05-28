import imaplib
import email
from email.header import decode_header
import os
import re
import requests
from urllib.parse import urlparse
import time
import shutil
from bs4 import BeautifulSoup
import sys
import logging
import base64
from email import policy
from email.parser import BytesParser

# IMAP配置区
EMAIL = '8888888@qq.com'  # 替换为你的QQ邮箱
PASSWORD = 'xxxxxxxxxx'  # 替换为你的授权码
IMAP_SERVER = 'imap.qq.com'
DOWNLOAD_FOLDER = './pdf'  # 本地保存PDF的文件夹

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 设置默认编码为UTF-8
sys.stdout.reconfigure(encoding='utf-8')

def setup_environment():
    """初始化环境"""
    # 创建保存PDF的文件夹（如果不存在则自动创建）
    if not os.path.exists(DOWNLOAD_FOLDER):
        os.makedirs(DOWNLOAD_FOLDER)
    
    # 检查并安装依赖
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        logger.info("正在安装所需依赖...")
        import subprocess
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'requests', 'beautifulsoup4'])

def clean_filename(filename):
    """清理文件名并确保有PDF扩展名"""
    if not filename:
        filename = f"pdf_{int(time.time())}"
    
    # 解码可能的编码字符
    try:
        filename = decode_mime_words(filename)
    except:
        pass
    
    # 移除非法字符
    filename = re.sub(r'[\\/*?:"<>|]', "_", filename)
    if not filename.lower().endswith('.pdf'):
        filename += '.pdf'
    return filename.strip()

def decode_mime_words(s):
    """增强版的邮件头解码"""
    if not s:
        return ""
    
    try:
        decoded = []
        for word, encoding in decode_header(s):
            if isinstance(word, bytes):
                try:
                    if encoding:
                        word = word.decode(encoding, errors='replace')
                    elif word.startswith(b'=?utf-8?B?'):  # 检测Base64编码
                        # 提取Base64编码部分
                        match = re.search(rb'=\?utf-8\?B\?(.*?)\?=', word)
                        if match:
                            base64_part = match.group(1)
                            try:
                                word = base64.b64decode(base64_part).decode('utf-8')
                            except:
                                word = word.decode('utf-8', errors='replace')
                    else:
                        # 尝试常见编码
                        for enc in ['utf-8', 'gbk', 'gb2312', 'gb18030', 'big5']:
                            try:
                                word = word.decode(enc)
                                break
                            except UnicodeDecodeError:
                                continue
                        else:  # 如果所有编码都失败
                            word = word.decode('utf-8', errors='replace')
                except Exception as e:
                    logger.warning(f"解码失败，使用ASCII: {str(e)}")
                    word = word.decode('ascii', errors='replace')
            decoded.append(word)
        return ''.join(decoded)
    except Exception as e:
        logger.error(f"解码完全失败: {str(e)}")
        # 如果是base64编码的字符串，尝试直接解码
        if isinstance(s, str) and s.startswith('=?utf-8?B?'):
            try:
                match = re.search(r'=\?utf-8\?B\?(.*?)\?=', s)
                if match:
                    base64_part = match.group(1)
                    return base64.b64decode(base64_part).decode('utf-8')
            except:
                pass
        return str(s)

def extract_pdf_links(html_content):
    """使用BeautifulSoup提取所有PDF链接"""
    soup = BeautifulSoup(html_content, 'html.parser')
    links = []
    
    # 查找所有链接
    for a in soup.find_all('a'):
        href = a.get('href')
        if not href:
            continue
            
        # 检查链接文本或链接本身是否包含PDF
        if ('pdf' in href.lower() or 
            (a.text and 'pdf' in a.text.lower()) or 
            (a.text and '下载' in a.text)):
            links.append(href)
    
    # 查找按钮式下载
    for btn in soup.find_all(attrs={"onclick": re.compile(r"(http|https)")}):
        btn_text = btn.get_text().lower()
        if 'pdf' in btn_text or '下载' in btn_text:
            if match := re.search(r'"(https?://.*?)"', btn['onclick']):
                links.append(match.group(1))
    
    # 额外搜索包含PDF的可能链接URL模式
    for link in soup.find_all('a', href=re.compile(r'.*\.pdf.*', re.I)):
        if href := link.get('href'):
            links.append(href)
            
    return list(set(links))  # 去重

def download_pdf(url, folder):
    """增强版PDF下载器"""
    try:
        logger.info(f"正在下载: {url}")
        session = requests.Session()
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'application/pdf, */*'
        }
        
        # 处理重定向
        response = session.get(url, headers=headers, stream=True, timeout=30, allow_redirects=True)
        
        # 检查响应状态和内容类型
        if response.status_code != 200:
            logger.warning(f"下载失败，状态码: {response.status_code}")
            return False
            
        content_type = response.headers.get('Content-Type', '').lower()
        if 'application/pdf' not in content_type and not url.lower().endswith('.pdf'):
            # 检查内容是否为PDF
            if not response.content.startswith(b'%PDF'):
                logger.warning(f"非PDF内容: {content_type}")
                return False
        
        # 确定文件名
        filename = None
        if 'Content-Disposition' in response.headers:
            if match := re.findall(r'filename=["\']?(.+?)["\']?$', response.headers['Content-Disposition']):
                filename = match[0]
        
        if not filename:
            filename = os.path.basename(urlparse(url).path) or f"pdf_{int(time.time())}"
        
        filename = clean_filename(filename)
        filepath = os.path.join(folder, filename)
        
        # 处理文件名冲突
        counter = 1
        while os.path.exists(filepath):
            name, ext = os.path.splitext(filename)
            filepath = os.path.join(folder, f"{name}_{counter}{ext}")
            counter += 1
        
        # 下载文件
        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(8192):
                f.write(chunk)
        
        logger.info(f"✓ 成功下载: {filename}")
        return True
        
    except Exception as e:
        logger.error(f"✗ 下载失败 [{url}]: {str(e)}")
        return False

def process_email_parts(msg):
    """处理邮件各部分内容"""
    subject = decode_mime_words(msg.get('Subject', '无主题'))
    logger.info(f"\n处理邮件: {subject}")
    
    pdf_count = 0
    
    # 1. 处理PDF附件
    logger.info("检查是否存在PDF附件...")
    
    for part in msg.walk():
        content_type = part.get_content_type().lower()
        content_disp = str(part.get('Content-Disposition') or '').lower()
        
        # 获取并解码文件名
        raw_filename = part.get_filename()
        filename = None
        
        if raw_filename:
            # 尝试解码文件名
            filename = decode_mime_words(raw_filename)
            logger.info(f"发现附件: [原始名称: {raw_filename}] -> [解码后: {filename}], 类型: {content_type}")
        elif 'attachment' in content_disp:
            logger.info(f"发现未命名附件, 类型: {content_type}")
            
        # 检查是否为附件
        is_attachment = bool(filename) or 'attachment' in content_disp
        
        # 检查是否为PDF (通过内容类型或文件名)
        is_pdf = (
            'application/pdf' in content_type or 
            'application/octet-stream' in content_type or
            (filename and filename.lower().endswith('.pdf'))
        )
        
        # 保存可能的PDF附件
        if is_attachment and (is_pdf or 'application/octet-stream' in content_type):
            try:
                payload = part.get_payload(decode=True)
                if not payload:
                    logger.warning(f"附件内容为空: {filename}")
                    continue
                
                # 检查文件头是否为PDF格式(%PDF-)
                is_valid_pdf = payload[:5].startswith(b'%PDF-')
                
                # 处理文件名
                if not filename:
                    filename = f"附件_{int(time.time())}.pdf"
                elif not filename.lower().endswith('.pdf') and is_valid_pdf:
                    filename = f"{filename}.pdf"
                    
                filename = clean_filename(filename)
                
                if is_valid_pdf:
                    logger.info(f"确认为有效PDF: {filename}")
                    
                    # 保存文件
                    filepath = os.path.join(DOWNLOAD_FOLDER, filename)
                    counter = 1
                    while os.path.exists(filepath):
                        name, ext = os.path.splitext(filename)
                        filepath = os.path.join(DOWNLOAD_FOLDER, f"{name}_{counter}{ext}")
                        counter += 1
                    
                    with open(filepath, 'wb') as f:
                        f.write(payload)
                    
                    logger.info(f"✓ 成功保存附件: {filename}")
                    pdf_count += 1
                elif 'application/octet-stream' in content_type and filename.lower().endswith('.pdf'):
                    # 对于octet-stream类型但文件名以.pdf结尾的，也尝试保存
                    logger.warning(f"附件声明为通用二进制但文件名为PDF，尝试保存: {filename}")
                    
                    filepath = os.path.join(DOWNLOAD_FOLDER, filename)
                    counter = 1
                    while os.path.exists(filepath):
                        name, ext = os.path.splitext(filename)
                        filepath = os.path.join(DOWNLOAD_FOLDER, f"{name}_{counter}{ext}")
                        counter += 1
                    
                    with open(filepath, 'wb') as f:
                        f.write(payload)
                    
                    logger.info(f"✓ 成功保存二进制附件: {filename}")
                    pdf_count += 1
                else:
                    logger.warning(f"附件不是有效的PDF文件: {filename}")
            
            except Exception as e:
                logger.error(f"保存附件失败: {str(e)}")
                logger.error(f"失败的附件名: {raw_filename} -> {filename}")
                
        # 处理嵌入附件的特殊情况
        elif not is_attachment and is_pdf and part.get_payload(decode=True):
            try:
                # 有些邮件系统可能不标记Content-Disposition，但仍包含PDF
                payload = part.get_payload(decode=True)
                if payload[:5].startswith(b'%PDF-'):
                    embed_filename = filename or f"嵌入PDF_{int(time.time())}.pdf"
                    embed_filename = clean_filename(embed_filename)
                    
                    filepath = os.path.join(DOWNLOAD_FOLDER, embed_filename)
                    counter = 1
                    while os.path.exists(filepath):
                        name, ext = os.path.splitext(embed_filename)
                        filepath = os.path.join(DOWNLOAD_FOLDER, f"{name}_{counter}{ext}")
                        counter += 1
                    
                    with open(filepath, 'wb') as f:
                        f.write(payload)
                    
                    logger.info(f"✓ 成功保存嵌入PDF: {embed_filename}")
                    pdf_count += 1
            except Exception as e:
                logger.error(f"保存嵌入PDF失败: {str(e)}")

    # 2. 处理正文中的PDF链接
    links_processed = []
    
    for part in msg.walk():
        content_type = part.get_content_type().lower()
        
        if 'text/html' in content_type:
            try:
                # 获取HTML内容
                charset = part.get_content_charset() or 'utf-8'
                html_content = part.get_payload(decode=True).decode(charset, errors='replace')
                
                # 提取PDF链接
                pdf_links = extract_pdf_links(html_content)
                
                # 下载链接
                for link in pdf_links:
                    if link in links_processed:
                        continue
                        
                    # 处理相对链接
                    if not link.startswith(('http://', 'https://')):
                        continue
                        
                    if download_pdf(link, DOWNLOAD_FOLDER):
                        pdf_count += 1
                    links_processed.append(link)
            except Exception as e:
                logger.error(f"HTML处理出错: {str(e)}")
    
    return pdf_count

def main():
    setup_environment()
    
    logger.info("程序启动，初始化环境...")
    
    try:
        # 连接邮箱
        logger.info("正在连接邮箱服务器...")
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL, PASSWORD)
        mail.select('inbox')
        
        # 搜索未读邮件
        logger.info("搜索未读邮件...")
        status, messages = mail.search(None, 'UNSEEN')
        
        if status != 'OK' or not messages[0]:
            logger.info("没有找到未读邮件")
            mail.logout()
            return

        email_ids = messages[0].split()
        logger.info(f"找到 {len(email_ids)} 封未读邮件，开始处理...")
        
        total_emails = len(email_ids)
        total_pdfs = 0
        
        for i, email_id in enumerate(email_ids):
            logger.info(f"处理邮件 {i+1}/{total_emails}...")
            
            # 获取邮件内容
            status, msg_data = mail.fetch(email_id, '(RFC822)')
            if status != 'OK':
                logger.warning(f"无法获取邮件 ID: {email_id}")
                continue

            msg = email.message_from_bytes(msg_data[0][1])
            pdfs_found = process_email_parts(msg)
            total_pdfs += pdfs_found
            
            # 标记为已读
            mail.store(email_id, '+FLAGS', '\\Seen')
            
        logger.info(f"\n处理完成！共处理 {total_emails} 封邮件，下载 {total_pdfs} 个PDF文件")
        logger.info(f"文件保存在: {os.path.abspath(DOWNLOAD_FOLDER)}")
        
        # 安全关闭连接
        mail.close()
        mail.logout()

    except Exception as e:
        logger.error(f"程序出错: {str(e)}")
        if 'mail' in locals():
            try:
                mail.logout()
            except:
                pass

if __name__ == '__main__':
    main()