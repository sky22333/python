#!/bin/bash
# 一键安装 Python 多版本（Ubuntu/Debian 适用）
# 功能：通过 deadsnakes PPA 安装 Python 3.10/3.11/3.12，并配置默认命令
# 支持版本选择，默认安装 Python 3.12

set -e  # 遇到错误立即退出

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# 支持的Python版本
SUPPORTED_VERSIONS=("3.10" "3.11" "3.12")
DEFAULT_VERSION="3.12"
SELECTED_VERSION=""

# 镜像配置
USE_CHINA_MIRRORS=false
LOCATION=""

# 检测地理位置
detect_location() {
    echo -e "${YELLOW}检测地理位置...${NC}"
    
    # 尝试获取地理位置
    if command -v curl &> /dev/null; then
        LOCATION=$(curl -s --connect-timeout 5 https://www.cloudflare.com/cdn-cgi/trace | grep 'loc=' | cut -d= -f2 2>/dev/null || echo "")
    fi
    
    # 如果无法获取位置，尝试备用方法
    if [ -z "$LOCATION" ]; then
        LOCATION=$(curl -s --connect-timeout 5 https://ipinfo.io/country 2>/dev/null || echo "")
    fi
    
    if [ "$LOCATION" = "CN" ]; then
        USE_CHINA_MIRRORS=true
        echo -e "${GREEN}检测到中国大陆地区，将使用国内镜像源${NC}"
    else
        USE_CHINA_MIRRORS=false
        if [ -n "$LOCATION" ]; then
            echo -e "${GREEN}检测到地区: $LOCATION，使用默认镜像源${NC}"
        else
            echo -e "${YELLOW}无法检测地理位置，使用默认镜像源${NC}"
        fi
    fi
}
show_banner() {
    echo -e "${CYAN}"
    echo "=================================================="
    echo "    Python 多版本安装脚本 (Ubuntu/Debian)"
    echo "    支持版本: ${SUPPORTED_VERSIONS[*]}"
    echo "    默认版本: Python ${DEFAULT_VERSION}"
    echo "=================================================="
    echo -e "${NC}"
}

# 检查root权限
check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        echo -e "${RED}错误：此脚本需要root权限！请使用 sudo 运行。${NC}" >&2
        exit 1
    fi
}

check_system() {
    if ! command -v apt-get &> /dev/null; then
        echo -e "${RED}错误：此脚本仅支持基于 APT 的系统（Ubuntu/Debian）${NC}" >&2
        exit 1
    fi
    
    # 检查网络连接
    if ! ping -c 1 archive.ubuntu.com &> /dev/null; then
        echo -e "${YELLOW}警告：网络连接可能有问题，安装过程可能较慢${NC}"
    fi
}

# 版本选择菜单
select_version() {
    echo -e "${BLUE}请选择要安装的 Python 版本：${NC}"
    echo
    for i in "${!SUPPORTED_VERSIONS[@]}"; do
        version="${SUPPORTED_VERSIONS[$i]}"
        if [ "$version" = "$DEFAULT_VERSION" ]; then
            echo -e "  $((i+1)). Python ${version} ${GREEN}[默认]${NC}"
        else
            echo -e "  $((i+1)). Python ${version}"
        fi
    done
    echo
    echo -e "${CYAN}直接按回车键安装默认版本 (Python ${DEFAULT_VERSION})${NC}"
    
    while true; do
        read -p "请输入选项 (1-${#SUPPORTED_VERSIONS[@]}) 或直接回车: " choice
        
        # 如果直接回车，使用默认版本
        if [ -z "$choice" ]; then
            SELECTED_VERSION="$DEFAULT_VERSION"
            echo -e "${GREEN}已选择默认版本: Python ${SELECTED_VERSION}${NC}"
            break
        fi
        
        # 验证输入
        if [[ "$choice" =~ ^[1-9][0-9]*$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "${#SUPPORTED_VERSIONS[@]}" ]; then
            SELECTED_VERSION="${SUPPORTED_VERSIONS[$((choice-1))]}"
            echo -e "${GREEN}已选择: Python ${SELECTED_VERSION}${NC}"
            break
        else
            echo -e "${RED}无效选择，请输入 1-${#SUPPORTED_VERSIONS[@]} 之间的数字或直接回车${NC}"
        fi
    done
}

# 检查是否已安装指定版本
check_existing_installation() {
    local version=$1
    local python_cmd="python${version}"
    
    if command -v "$python_cmd" &> /dev/null; then
        local current_version=$($python_cmd --version 2>&1 | cut -d' ' -f2)
        echo -e "${YELLOW}检测到已安装 Python ${current_version}${NC}"
        
        read -p "是否继续安装并重新配置？[y/N]: " confirm
        if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
            echo -e "${BLUE}安装已取消${NC}"
            exit 0
        fi
    fi
}

# 添加PPA并更新软件源
setup_repository() {
    echo -e "${YELLOW}[1/5] 配置软件源...${NC}"
    
    # 安装必要工具
    apt-get update -qq
    apt-get install -y -qq software-properties-common curl wget gpg lsb-release > /dev/null
    
    # 添加 deadsnakes PPA
    echo "添加 deadsnakes PPA..."
    add-apt-repository -y ppa:deadsnakes/ppa > /dev/null 2>&1
    
    # 更新软件包列表
    echo "更新软件包列表..."
    apt-get update -qq
}

# 安装指定版本的Python
install_python() {
    local version=$1
    echo -e "${YELLOW}[2/5] 安装 Python ${version}...${NC}"
    
    # 构建包名列表
    local packages=(
        "python${version}"
        "python${version}-venv"
        "python${version}-dev"
        "python${version}-distutils"
    )
    
    # 检查包是否可用并安装
    for package in "${packages[@]}"; do
        if apt-cache show "$package" &> /dev/null; then
            echo "安装 $package..."
            apt-get install -y -qq "$package" > /dev/null
        else
            echo -e "${YELLOW}警告: 包 $package 不可用，跳过${NC}"
        fi
    done
}

# 安装pip
install_pip() {
    local version=$1
    echo -e "${YELLOW}[3/5] 配置 pip...${NC}"
    
    local python_cmd="python${version}"
    
    # 检查是否已有pip
    if ! $python_cmd -m pip --version &> /dev/null; then
        echo "安装 pip..."
        if [ "$USE_CHINA_MIRRORS" = true ]; then
            curl -sS https://bootstrap.pypa.io/get-pip.py | $python_cmd -
        else
            curl -sS https://bootstrap.pypa.io/get-pip.py | $python_cmd
        fi
    fi
    
    # 升级pip到最新版本
    echo "升级 pip..."
    if [ "$USE_CHINA_MIRRORS" = true ]; then
        $python_cmd -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple --quiet
    else
        $python_cmd -m pip install --upgrade pip --quiet
    fi
}

# 配置pip国内镜像源
configure_pip_mirrors() {
    local version=$1
    if [ "$USE_CHINA_MIRRORS" = true ]; then
        echo -e "${YELLOW}[4/5] 配置 pip 国内镜像源...${NC}"
        
        # 创建pip配置目录
        local pip_config_dir="/etc/pip"
        mkdir -p "$pip_config_dir"
        
        # 配置全局pip镜像源
        cat > "$pip_config_dir/pip.conf" << EOF
[global]
index-url = https://pypi.tuna.tsinghua.edu.cn/simple
trusted-host = pypi.tuna.tsinghua.edu.cn
timeout = 6000
EOF
        
        # 为当前用户配置pip镜像源
        local user_pip_dir="$HOME/.config/pip"
        mkdir -p "$user_pip_dir"
        cp "$pip_config_dir/pip.conf" "$user_pip_dir/"
        
        # 为root用户配置pip镜像源
        local root_pip_dir="/root/.config/pip"
        mkdir -p "$root_pip_dir"
        cp "$pip_config_dir/pip.conf" "$root_pip_dir/"
        
        echo -e "${GREEN}已配置清华大学 pip 镜像源${NC}"
    fi
}
setup_alternatives() {
    local version=$1
    echo -e "${YELLOW}[4/4] 配置默认命令...${NC}"
    
    local python_path="/usr/bin/python${version}"
    local priority=100
    
    # 设置python3的替代方案
    if [ -f "$python_path" ]; then
        update-alternatives --install /usr/bin/python3 python3 "$python_path" $priority 2>/dev/null || true
        update-alternatives --install /usr/bin/python python "$python_path" $priority 2>/dev/null || true
    fi
    
    # 设置pip的软链接
    local pip_path=$(find /usr/local/bin /home/*/.local/bin -name "pip${version}" 2>/dev/null | head -1)
    if [ -z "$pip_path" ]; then
        pip_path="/usr/local/bin/pip${version}"
    fi
    
    if [ -f "$pip_path" ]; then
        ln -sf "$pip_path" /usr/bin/pip 2>/dev/null || true
        ln -sf "$pip_path" /usr/bin/pip3 2>/dev/null || true
    fi
}

# 验证安装
verify_installation() {
    local version=$1
    echo
    echo -e "${GREEN}=================================================="
    echo -e "           安装完成！验证结果"
    echo -e "==================================================${NC}"
    
    local python_cmd="python${version}"
    local errors=0
    
    # 检查Python版本
    if command -v "$python_cmd" &> /dev/null; then
        echo -e "${GREEN}✓ Python版本:${NC} $($python_cmd --version)"
        echo -e "${GREEN}✓ Python路径:${NC} $(which $python_cmd)"
    else
        echo -e "${RED}✗ Python ${version} 未正确安装${NC}"
        ((errors++))
    fi
    
    # 检查pip
    if $python_cmd -m pip --version &> /dev/null; then
        echo -e "${GREEN}✓ Pip版本:${NC} $($python_cmd -m pip --version | cut -d' ' -f1-2)"
    else
        echo -e "${RED}✗ Pip 未正确安装${NC}"
        ((errors++))
    fi
    
    # 检查默认命令
    if command -v python3 &> /dev/null; then
        echo -e "${GREEN}✓ 默认python3:${NC} $(python3 --version)"
    fi
    
    if command -v python &> /dev/null; then
        echo -e "${GREEN}✓ 默认python:${NC} $(python --version)"
    fi
    
    echo
    if [ $errors -eq 0 ]; then
        echo -e "${GREEN}Python ${version} 安装成功！${NC}"
        echo
        echo -e "${CYAN}使用方法：${NC}"
        echo -e "  直接使用: ${GREEN}python${version}${NC} 或 ${GREEN}python3${NC}"
        echo -e "  安装包: ${GREEN}python${version} -m pip install 包名${NC}"
        echo -e "  创建虚拟环境: ${GREEN}python${version} -m venv 环境名${NC}"
        
        if [ "$USE_CHINA_MIRRORS" = true ]; then
            echo
            echo -e "${CYAN}镜像源信息：${NC}"
            echo -e "  已配置清华大学 pip 镜像源"
            echo -e "  配置文件: /etc/pip/pip.conf"
        fi
    else
        echo -e "${RED}安装过程中出现 $errors 个错误，请检查上述信息${NC}"
        exit 1
    fi
}

# 清理函数
cleanup() {
    echo -e "\n${YELLOW}正在清理临时文件...${NC}"
    apt-get autoremove -y -qq > /dev/null 2>&1 || true
    apt-get autoclean -qq > /dev/null 2>&1 || true
}

# 错误处理
error_handler() {
    local exit_code=$?
    echo -e "\n${RED}错误：脚本执行失败 (退出码: $exit_code)${NC}" >&2
    echo -e "${YELLOW}请检查网络连接和系统权限${NC}" >&2
    cleanup
    exit $exit_code
}

# 主流程
main() {
    # 注册错误处理
    trap error_handler ERR
    
    show_banner
    check_root
    check_system
    detect_location
    select_version
    check_existing_installation "$SELECTED_VERSION"
    
    echo
    echo -e "${BLUE}开始安装 Python ${SELECTED_VERSION}...${NC}"
    echo
    
    setup_repository
    install_python "$SELECTED_VERSION"
    install_pip "$SELECTED_VERSION"
    configure_pip_mirrors "$SELECTED_VERSION"
    setup_alternatives "$SELECTED_VERSION"
    verify_installation "$SELECTED_VERSION"
    cleanup
    
    echo
    echo -e "${GREEN}安装脚本执行完毕！${NC}"
}

# 脚本入口
main "$@"