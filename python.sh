#!/bin/bash
# Python 多版本安装脚本 (支持 Ubuntu/Debian)
# 优化版：适配所有 Debian 和 Ubuntu 系统，提供简洁日志输出

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

# 系统信息
DISTRO=""
DISTRO_VERSION=""
IS_UBUNTU=false
IS_DEBIAN=false

# 镜像配置
USE_CHINA_MIRRORS=false
LOCATION=""

# 检测系统信息
detect_system() {
    echo -e "${YELLOW}检测系统信息...${NC}"
    
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        DISTRO="$ID"
        DISTRO_VERSION="$VERSION_ID"
        
        case "$DISTRO" in
            ubuntu)
                IS_UBUNTU=true
                echo -e "${GREEN}检测到 Ubuntu $DISTRO_VERSION${NC}"
                ;;
            debian)
                IS_DEBIAN=true
                echo -e "${GREEN}检测到 Debian $DISTRO_VERSION${NC}"
                ;;
            *)
                echo -e "${RED}警告：未测试的系统 $DISTRO $DISTRO_VERSION${NC}"
                echo -e "${YELLOW}将尝试使用 Debian 兼容模式${NC}"
                IS_DEBIAN=true
                ;;
        esac
    else
        echo -e "${RED}错误：无法检测系统版本${NC}" >&2
        exit 1
    fi
}

# 检测地理位置
detect_location() {
    echo -e "${YELLOW}检测地理位置...${NC}"
    
    # 尝试获取地理位置
    if command -v curl &> /dev/null; then
        LOCATION=$(timeout 5 curl -s https://www.cloudflare.com/cdn-cgi/trace 2>/dev/null | grep 'loc=' | cut -d= -f2 || echo "")
    fi
    
    # 如果无法获取位置，尝试备用方法
    if [ -z "$LOCATION" ]; then
        LOCATION=$(timeout 5 curl -s https://ipinfo.io/country 2>/dev/null || echo "")
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
    echo -e "${YELLOW}检查网络连接...${NC}"
    if ! timeout 5 ping -c 1 -W 2 8.8.8.8 &> /dev/null; then
        echo -e "${YELLOW}警告：网络连接可能有问题，安装过程可能较慢${NC}"
    else
        echo -e "${GREEN}网络连接正常${NC}"
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

# 更新软件源
update_packages() {
    echo -e "${YELLOW}[1/5] 更新软件包列表...${NC}"
    
    # 更新前先安装必要工具
    if ! command -v curl &> /dev/null; then
        echo "  └─ 安装 curl..."
        apt-get update -q && apt-get install -y curl
    fi
    
    if ! command -v software-properties-common &> /dev/null; then
        echo "  └─ 安装必要工具..."
        apt-get install -y software-properties-common apt-transport-https ca-certificates gnupg lsb-release
    fi
    
    echo "  └─ 更新软件包列表..."
    apt-get update -q
    echo -e "${GREEN}  ✓ 软件包列表已更新${NC}"
}

# 添加Python源
setup_python_repository() {
    echo -e "${YELLOW}[2/5] 配置 Python 软件源...${NC}"
    
    if [ "$IS_UBUNTU" = true ]; then
        # Ubuntu 使用 deadsnakes PPA
        echo "  └─ 添加 deadsnakes PPA..."
        if add-apt-repository -y ppa:deadsnakes/ppa; then
            echo -e "${GREEN}  ✓ deadsnakes PPA 添加成功${NC}"
        else
            echo -e "${RED}  ✗ 添加 PPA 失败，尝试手动配置...${NC}"
            return 1
        fi
    else
        # Debian 使用官方源或第三方源
        echo "  └─ 配置 Debian Python 源..."
        
        # 对于 Debian，尝试使用官方 backports 或直接编译
        if [ "$DISTRO_VERSION" = "12" ]; then
            # Debian 12 (bookworm)
            echo "deb http://deb.debian.org/debian bookworm-backports main" > /etc/apt/sources.list.d/backports.list
        elif [ "$DISTRO_VERSION" = "11" ]; then
            # Debian 11 (bullseye)
            echo "deb http://deb.debian.org/debian bullseye-backports main" > /etc/apt/sources.list.d/backports.list
        fi
        
        # 尝试添加 deadsnakes PPA（可能在某些 Debian 版本上工作）
        if ! add-apt-repository -y ppa:deadsnakes/ppa 2>/dev/null; then
            echo -e "${YELLOW}  注意：无法添加 PPA，将使用系统默认源${NC}"
        fi
        
        echo -e "${GREEN}  ✓ Debian 源配置完成${NC}"
    fi
    
    # 更新软件包列表
    echo "  └─ 更新软件包列表..."
    if apt-get update -q; then
        echo -e "${GREEN}  ✓ 软件包列表更新成功${NC}"
    else
        echo -e "${YELLOW}  警告：软件包列表更新有问题，继续尝试安装...${NC}"
    fi
}

# 安装指定版本的Python
install_python() {
    local version=$1
    echo -e "${YELLOW}[3/5] 安装 Python ${version}...${NC}"
    
    # 构建包名列表
    local packages=(
        "python${version}"
        "python${version}-venv"
        "python${version}-dev"
    )
    
    # 尝试添加额外包
    local optional_packages=(
        "python${version}-distutils"
        "python${version}-lib2to3"
        "python${version}-gdbm"
        "python${version}-tk"
    )
    
    # 安装主要包
    local installed_count=0
    for package in "${packages[@]}"; do
        echo "  └─ 安装 $package..."
        if apt-get install -y "$package"; then
            echo -e "${GREEN}    ✓ $package 安装成功${NC}"
            ((installed_count++))
        else
            echo -e "${RED}    ✗ $package 安装失败${NC}"
        fi
    done
    
    # 安装可选包
    for package in "${optional_packages[@]}"; do
        echo "  └─ 尝试安装 $package..."
        if apt-cache show "$package" &> /dev/null && apt-get install -y "$package" 2>/dev/null; then
            echo -e "${GREEN}    ✓ $package 安装成功${NC}"
            ((installed_count++))
        else
            echo -e "${YELLOW}    ⚠ $package 不可用或安装失败，跳过${NC}"
        fi
    done
    
    if [ $installed_count -eq 0 ]; then
        echo -e "${RED}错误：没有成功安装任何 Python 包${NC}" >&2
        exit 1
    fi
    
    echo -e "${GREEN}  ✓ Python ${version} 安装完成 ($installed_count 个包)${NC}"
}

# 安装和配置pip
install_pip() {
    local version=$1
    echo -e "${YELLOW}[4/5] 配置 pip...${NC}"
    
    local python_cmd="python${version}"
    
    # 检查python命令是否可用
    if ! command -v "$python_cmd" &> /dev/null; then
        echo -e "${RED}错误：找不到 $python_cmd 命令${NC}" >&2
        exit 1
    fi
    
    # 检查是否已有pip
    echo "  └─ 检查 pip 状态..."
    if ! $python_cmd -m pip --version &> /dev/null; then
        echo "  └─ 安装 pip..."
        if command -v curl &> /dev/null; then
            if curl -sS https://bootstrap.pypa.io/get-pip.py | $python_cmd; then
                echo -e "${GREEN}    ✓ pip 安装成功${NC}"
            else
                echo -e "${RED}    ✗ pip 安装失败${NC}"
                # 尝试从包管理器安装
                echo "  └─ 尝试从包管理器安装 pip..."
                apt-get install -y python3-pip python${version}-pip 2>/dev/null || true
            fi
        fi
    else
        echo -e "${GREEN}    ✓ pip 已存在${NC}"
    fi
    
    # 升级pip
    echo "  └─ 升级 pip 到最新版本..."
    if [ "$USE_CHINA_MIRRORS" = true ]; then
        $python_cmd -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple --quiet --disable-pip-version-check
    else
        $python_cmd -m pip install --upgrade pip --quiet --disable-pip-version-check
    fi
    echo -e "${GREEN}  ✓ pip 配置完成${NC}"
}

# 配置pip国内镜像源
configure_pip_mirrors() {
    local version=$1
    if [ "$USE_CHINA_MIRRORS" = true ]; then
        echo -e "${YELLOW}[5/5] 配置 pip 国内镜像源...${NC}"
        
        # 创建pip配置目录
        local pip_config_dir="/etc/pip"
        mkdir -p "$pip_config_dir"
        
        # 配置全局pip镜像源
        cat > "$pip_config_dir/pip.conf" << 'EOF'
[global]
index-url = https://pypi.tuna.tsinghua.edu.cn/simple
trusted-host = pypi.tuna.tsinghua.edu.cn
timeout = 60
retries = 5
EOF
        
        # 为当前用户配置pip镜像源（如果不是root用户运行）
        if [ -n "$SUDO_USER" ]; then
            local user_home=$(eval echo ~$SUDO_USER)
            local user_pip_dir="$user_home/.config/pip"
            mkdir -p "$user_pip_dir"
            cp "$pip_config_dir/pip.conf" "$user_pip_dir/"
            chown -R $SUDO_USER:$SUDO_USER "$user_pip_dir"
        fi
        
        # 为root用户配置pip镜像源
        local root_pip_dir="/root/.config/pip"
        mkdir -p "$root_pip_dir"
        cp "$pip_config_dir/pip.conf" "$root_pip_dir/"
        
        echo -e "${GREEN}  ✓ 清华大学 pip 镜像源配置完成${NC}"
    else
        echo -e "${YELLOW}[5/5] 跳过镜像源配置...${NC}"
    fi
}

# 配置默认命令
setup_alternatives() {
    local version=$1
    echo -e "${YELLOW}配置默认命令...${NC}"
    
    local python_path="/usr/bin/python${version}"
    
    # 检查python文件是否存在
    if [ -f "$python_path" ]; then
        # 设置python3的替代方案
        echo "  └─ 配置 python3 命令..."
        update-alternatives --install /usr/bin/python3 python3 "$python_path" 100 2>/dev/null || true
        
        # 设置python命令（可选）
        if ! command -v python &> /dev/null; then
            echo "  └─ 配置 python 命令..."
            update-alternatives --install /usr/bin/python python "$python_path" 100 2>/dev/null || true
        fi
        
        echo -e "${GREEN}  ✓ 默认命令配置完成${NC}"
    else
        echo -e "${YELLOW}  警告：未找到 $python_path，跳过默认命令配置${NC}"
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
    echo "  检查 Python 安装..."
    if command -v "$python_cmd" &> /dev/null; then
        local py_version=$($python_cmd --version 2>&1)
        echo -e "${GREEN}  ✓ Python版本: $py_version${NC}"
        echo -e "${GREEN}  ✓ Python路径: $(which $python_cmd)${NC}"
    else
        echo -e "${RED}  ✗ Python ${version} 未正确安装${NC}"
        ((errors++))
    fi
    
    # 检查pip
    echo "  检查 pip 安装..."
    if $python_cmd -m pip --version &> /dev/null; then
        local pip_version=$($python_cmd -m pip --version | head -1)
        echo -e "${GREEN}  ✓ Pip版本: $pip_version${NC}"
    else
        echo -e "${RED}  ✗ Pip 未正确安装${NC}"
        ((errors++))
    fi
    
    # 检查默认命令
    echo "  检查默认命令..."
    if command -v python3 &> /dev/null; then
        echo -e "${GREEN}  ✓ 默认python3: $(python3 --version)${NC}"
    fi
    
    if command -v python &> /dev/null; then
        echo -e "${GREEN}  ✓ 默认python: $(python --version)${NC}"
    fi
    
    echo
    if [ $errors -eq 0 ]; then
        echo -e "${GREEN}🎉 Python ${version} 安装成功！${NC}"
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
        echo -e "${RED}❌ 安装过程中出现 $errors 个错误${NC}"
        echo -e "${YELLOW}建议检查网络连接和系统兼容性${NC}"
        exit 1
    fi
}

# 清理函数
cleanup() {
    echo -e "${YELLOW}清理临时文件...${NC}"
    apt-get autoremove -y -q > /dev/null 2>&1 || true
    apt-get autoclean -q > /dev/null 2>&1 || true
}

# 错误处理
error_handler() {
    local exit_code=$?
    echo -e "\n${RED}❌ 错误：脚本执行失败 (退出码: $exit_code)${NC}" >&2
    echo -e "${YELLOW}可能的原因：${NC}" >&2
    echo -e "  1. 网络连接问题" >&2
    echo -e "  2. 软件源不兼容" >&2
    echo -e "  3. 系统权限不足" >&2
    echo -e "  4. 磁盘空间不足" >&2
    echo
    echo -e "${CYAN}建议：${NC}" >&2
    echo -e "  - 检查网络连接" >&2
    echo -e "  - 确保有足够的磁盘空间" >&2
    echo -e "  - 尝试手动安装: apt install python${SELECTED_VERSION}" >&2
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
    detect_system
    detect_location
    select_version
    check_existing_installation "$SELECTED_VERSION"
    
    echo
    echo -e "${BLUE}开始安装 Python ${SELECTED_VERSION}...${NC}"
    echo
    
    update_packages
    setup_python_repository
    install_python "$SELECTED_VERSION"
    install_pip "$SELECTED_VERSION"
    configure_pip_mirrors "$SELECTED_VERSION"
    setup_alternatives "$SELECTED_VERSION"
    verify_installation "$SELECTED_VERSION"
    cleanup
    
    echo
    echo -e "${GREEN}🎉 安装脚本执行完毕！${NC}"
}

# 脚本入口
main "$@"
