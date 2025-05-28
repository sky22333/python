#!/bin/bash
# Python 快速安装脚本 (二进制方式)
# 支持 Ubuntu/Debian/CentOS 等主流Linux系统

set -e  # 遇到错误立即退出

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 配置参数
PYTHON_VERSION="3.12.0"
INSTALL_DIR="/opt/python-${PYTHON_VERSION}"
BIN_DIR="/usr/local/bin"
USE_CHINA_MIRROR=true

# 检测系统架构
ARCH=$(uname -m)
case "$ARCH" in
    x86_64) ARCH="x86_64";;
    aarch64) ARCH="aarch64";;
    *) echo -e "${RED}错误：不支持的架构 $ARCH${NC}"; exit 1;;
esac

# 显示标题
echo -e "${GREEN}"
echo "========================================"
echo "  Python 快速安装脚本 (二进制方式)"
echo "  版本: ${PYTHON_VERSION}"
echo "  架构: ${ARCH}"
echo "========================================"
echo -e "${NC}"

# 检查root权限
check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        echo -e "${RED}错误：此脚本需要root权限！${NC}" >&2
        exit 1
    fi
}

# 安装基础依赖
install_deps() {
    echo -e "${YELLOW}[1/4] 安装基础依赖...${NC}"
    if command -v apt-get &> /dev/null; then
        apt-get update -qq
        apt-get install -y -qq wget tar gzip make zlib1g-dev
    elif command -v yum &> /dev/null; then
        yum install -y -q wget tar gzip make zlib-devel
    else
        echo -e "${YELLOW}警告：无法识别的包管理器，跳过依赖安装${NC}"
    fi
    echo -e "${GREEN}  ✓ 依赖安装完成${NC}"
}

# 下载Python二进制包
download_python() {
    echo -e "${YELLOW}[2/4] 下载Python二进制包...${NC}"
    
    local filename="Python-${PYTHON_VERSION}.tgz"
    local url="https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz"
    
    if [ "$USE_CHINA_MIRROR" = true ]; then
        url="https://mirrors.huaweicloud.com/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz"
        echo -e "  使用国内镜像源 (华为云)"
    fi

    echo -e "  下载地址: ${BLUE}${url}${NC}"
    rm -rf /tmp/Python-${PYTHON_VERSION}*
    wget -q --show-progress -O /tmp/${filename} "${url}"
    
    if [ $? -ne 0 ]; then
        echo -e "${RED}  ✗ 下载失败，请检查网络连接${NC}"
        exit 1
    fi
    echo -e "${GREEN}  ✓ 下载完成${NC}"
}

# 安装Python
install_python() {
    echo -e "${YELLOW}[3/4] 安装Python...${NC}"
    
    echo -e "  解压到 ${INSTALL_DIR}"
    mkdir -p "${INSTALL_DIR}"
    tar -xzf "/tmp/Python-${PYTHON_VERSION}.tgz" -C "/tmp"
    
    echo -e "  编译安装 (优化模式)"
    cd "/tmp/Python-${PYTHON_VERSION}"
    ./configure \
        --prefix="${INSTALL_DIR}" \
        --enable-optimizations \
        --with-ensurepip=install \
        --enable-shared \
        LDFLAGS="-Wl,-rpath=${INSTALL_DIR}/lib"
    
    make -j$(nproc) > /dev/null
    make install > /dev/null
    
    # 创建符号链接
    ln -sf "${INSTALL_DIR}/bin/python3" "${BIN_DIR}/python${PYTHON_VERSION%.*}"
    ln -sf "${INSTALL_DIR}/bin/pip3" "${BIN_DIR}/pip${PYTHON_VERSION%.*}"
    
    echo -e "${GREEN}  ✓ 安装完成${NC}"
}

# 配置环境
setup_env() {
    echo -e "${YELLOW}[4/4] 配置环境...${NC}"
    
    # 更新动态库缓存
    ldconfig
    
    # 配置pip镜像 (国内用户)
    if [ "$USE_CHINA_MIRROR" = true ]; then
        echo -e "  配置pip国内镜像"
        mkdir -p /etc/pip
        cat > /etc/pip/pip.conf <<EOF
[global]
index-url = https://pypi.tuna.tsinghua.edu.cn/simple
trusted-host = pypi.tuna.tsinghua.edu.cn
EOF
    fi
    
    # 验证安装
    echo -e "\n${GREEN}验证安装:${NC}"
    "${INSTALL_DIR}/bin/python3" --version
    "${INSTALL_DIR}/bin/pip3" --version
    
    echo -e "\n${GREEN}使用说明:${NC}"
    echo -e "  Python路径: ${BLUE}${INSTALL_DIR}/bin/python3${NC}"
    echo -e "  Pip路径:    ${BLUE}${INSTALL_DIR}/bin/pip3${NC}"
    echo -e "  快捷命令:   ${BLUE}python${PYTHON_VERSION%.*} / pip${PYTHON_VERSION%.*}${NC}"
    
    echo -e "\n${GREEN}🎉 安装完成!${NC}"
}

# 主流程
main() {
    check_root
    install_deps
    download_python
    install_python
    setup_env
    
    # 清理临时文件
    rm -rf "/tmp/Python-${PYTHON_VERSION}"*
}

main "$@"
